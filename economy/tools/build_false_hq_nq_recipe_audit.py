from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pymysql
import yaml


ROOT = Path.home() / "ffxiahbot"

CONFIG_FILE = ROOT / "bin" / "config.yaml"
MISSING_AUDIT_FILE = ROOT / "economy" / "reports" / "hq-missing-evidence-audit.csv"

OUTPUT_FILE = ROOT / "economy" / "reports" / "false-hq-nq-recipe-audit.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "false-hq-nq-recipe-audit-summary.json"


SKILL_COLUMNS = [
    "Wood",
    "Smith",
    "Gold",
    "Cloth",
    "Leather",
    "Bone",
    "Alchemy",
    "Cook",
]

MATURITY_ORDER = [
    "SEED",
    "GROWING",
    "ESTABLISHED",
    "MATURE",
    "ADVANCED",
    "FULL",
]


class FalseHQAuditError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def as_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0
    return int(value)


def load_db_settings() -> dict[str, Any]:
    data = yaml.safe_load(
        CONFIG_FILE.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(data, dict):
        raise FalseHQAuditError(
            f"Invalid config mapping: {CONFIG_FILE}"
        )

    source = data.get("db")
    if not isinstance(source, dict):
        source = data

    def required(*keys: str):
        for key in keys:
            if key in source and source[key] not in {None, ""}:
                return source[key]

        raise FalseHQAuditError(
            "Missing DB config key; expected one of: "
            + ", ".join(keys)
        )

    return {
        "host": required("hostname", "host"),
        "database": required("database", "dbname"),
        "user": required("username", "user"),
        "password": required("password"),
        "port": int(source.get("port", 3306)),
    }


def load_recipes() -> pd.DataFrame:
    cfg = load_db_settings()

    connection = pymysql.connect(
        host=str(cfg["host"]),
        user=str(cfg["user"]),
        password=str(cfg["password"]),
        database=str(cfg["database"]),
        port=int(cfg["port"]),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )

    query = """
        SELECT
            ID,
            Desynth,
            KeyItem,
            Wood,
            Smith,
            Gold,
            Cloth,
            Leather,
            Bone,
            Alchemy,
            Cook,
            Crystal,
            HQCrystal,
            Ingredient1,
            Ingredient2,
            Ingredient3,
            Ingredient4,
            Ingredient5,
            Ingredient6,
            Ingredient7,
            Ingredient8,
            Result,
            ResultHQ1,
            ResultHQ2,
            ResultHQ3,
            ResultQty,
            ResultName,
            content_tag
        FROM synth_recipes
        WHERE Desynth = 0
        ORDER BY ID
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
    finally:
        connection.close()

    frame = pd.DataFrame(rows)

    if frame.empty:
        raise FalseHQAuditError(
            "No synthesis recipes loaded."
        )

    return frame


def recipe_skill(row: pd.Series) -> tuple[int, str]:
    values = {
        column: as_int(row.get(column))
        for column in SKILL_COLUMNS
    }

    level = max(
        values.values(),
        default=0,
    )

    skills = [
        name
        for name, value in values.items()
        if value == level and value > 0
    ]

    return level, "|".join(skills)


def maturity_for_skill(skill: int) -> str:
    if skill <= 20:
        return "SEED"
    if skill <= 40:
        return "GROWING"
    if skill <= 60:
        return "ESTABLISHED"
    if skill <= 80:
        return "MATURE"
    if skill <= 100:
        return "ADVANCED"
    return "FULL"


def main() -> int:
    if not MISSING_AUDIT_FILE.exists():
        raise FalseHQAuditError(
            f"Missing input: {MISSING_AUDIT_FILE}"
        )

    audit = pd.read_csv(
        MISSING_AUDIT_FILE,
        low_memory=False,
    )

    target = audit[
        audit["recommended_action"]
        == "FALSE_HQ_HEURISTIC_REVIEW"
    ].copy()

    if len(target) != 42:
        raise FalseHQAuditError(
            f"Expected 42 false-HQ items, found {len(target)}"
        )

    recipes = load_recipes()

    target_ids = set(
        target["itemid"].astype(int)
    )

    rows: list[dict[str, Any]] = []

    for _, recipe in recipes.iterrows():
        itemid = as_int(
            recipe.get("Result")
        )

        if itemid not in target_ids:
            continue

        skill_level, craft_skill = recipe_skill(
            recipe
        )

        ingredients = [
            as_int(
                recipe.get(
                    f"Ingredient{i}"
                )
            )
            for i in range(1, 9)
        ]

        ingredients = [
            value
            for value in ingredients
            if value > 0
        ]

        rows.append(
            {
                "itemid": itemid,
                "name": clean_text(
                    target.loc[
                        target["itemid"] == itemid,
                        "name",
                    ].iloc[0]
                ),
                "recipe_id": as_int(
                    recipe.get("ID")
                ),
                "keyitem_id": as_int(
                    recipe.get("KeyItem")
                ),
                "craft_skill": craft_skill,
                "craft_level": skill_level,
                "minimum_maturity": maturity_for_skill(
                    skill_level
                ),
                "crystal": as_int(
                    recipe.get("Crystal")
                ),
                "hq_crystal": as_int(
                    recipe.get("HQCrystal")
                ),
                "ingredient_count": len(ingredients),
                "ingredient_ids": "|".join(
                    str(value)
                    for value in ingredients
                ),
                "result_quantity": as_int(
                    recipe.get("ResultQty")
                ),
                "has_hq1": int(
                    as_int(
                        recipe.get("ResultHQ1")
                    ) > 0
                ),
                "has_hq2": int(
                    as_int(
                        recipe.get("ResultHQ2")
                    ) > 0
                ),
                "has_hq3": int(
                    as_int(
                        recipe.get("ResultHQ3")
                    ) > 0
                ),
                "content_tag": clean_text(
                    recipe.get("content_tag")
                ),
                "recommended_class": "SCARCE",
                "policy_reason": "INDEPENDENT_NQ_PLUS_ONE_RECIPE",
                "seller_eligible": 1,
                "buyer_eligible": 1,
                "manual_review_required": int(
                    as_int(
                        recipe.get("KeyItem")
                    ) > 0
                ),
                "activation_ready": 0,
                "auto_live_promotion": 0,
            }
        )

    output = pd.DataFrame(rows)

    matched_ids = set(
        output["itemid"].astype(int)
    ) if not output.empty else set()

    missing_ids = sorted(
        target_ids - matched_ids
    )

    if missing_ids:
        raise FalseHQAuditError(
            "False-HQ items missing NQ recipes: "
            + ", ".join(
                str(value)
                for value in missing_ids
            )
        )

    # Multiple valid recipes are allowed. For policy summary, item-level
    # maturity should use the earliest available recipe route.
    output = output.sort_values(
        by=[
            "itemid",
            "craft_level",
            "recipe_id",
        ],
        kind="stable",
    )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    item_summary = (
        output.sort_values(
            by=[
                "itemid",
                "craft_level",
                "recipe_id",
            ],
            kind="stable",
        )
        .drop_duplicates(
            subset=["itemid"],
            keep="first",
        )
    )

    summary = {
        "status": "PASS",
        "target_items": 42,
        "matched_items": int(
            item_summary["itemid"].nunique()
        ),
        "recipe_rows": int(
            len(output)
        ),
        "keyitem_gated_items": int(
            item_summary[
                "manual_review_required"
            ].sum()
        ),
        "minimum_maturity": {
            str(k): int(v)
            for k, v in (
                item_summary[
                    "minimum_maturity"
                ]
                .value_counts()
                .to_dict()
                .items()
            )
        },
        "activation_ready": 0,
        "auto_live_promotions": 0,
    }

    SUMMARY_FILE.write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print(
        "======================================"
    )
    print(
        " False-HQ NQ Recipe Audit"
    )
    print(
        "======================================"
    )
    print(
        f"Target items:            "
        f"{summary['target_items']:>6}"
    )
    print(
        f"Matched items:           "
        f"{summary['matched_items']:>6}"
    )
    print(
        f"Recipe rows:             "
        f"{summary['recipe_rows']:>6}"
    )
    print(
        f"Key-item gated items:    "
        f"{summary['keyitem_gated_items']:>6}"
    )
    print()
    print(
        "Minimum maturity:"
    )

    for stage in MATURITY_ORDER:
        count = summary[
            "minimum_maturity"
        ].get(
            stage,
            0,
        )
        if count:
            print(
                f"  {stage:<14} "
                f"{count:>6}"
            )

    print()
    print(
        "Activation ready: 0"
    )
    print(
        "Auto live promotions: 0"
    )
    print()
    print(
        f"Audit:   {OUTPUT_FILE}"
    )
    print(
        f"Summary: {SUMMARY_FILE}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
