from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pymysql
import yaml


ROOT = Path.home() / "ffxiahbot"
LSB_ROOT = Path.home() / "server"

CONFIG_FILE = ROOT / "bin" / "config.yaml"
KEY_ITEM_ENUM = LSB_ROOT / "scripts" / "enum" / "key_item.lua"

AUDIT_FILE = ROOT / "economy" / "reports" / "candidate-market-audit.csv"
DETAIL_FILE = ROOT / "economy" / "reports" / "restricted-craft-recipe-audit.csv"
KEYITEM_FILE = ROOT / "economy" / "reports" / "restricted-craft-keyitem-summary.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "restricted-craft-audit-summary.json"

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

OUTPUT_COLUMNS = [
    ("Result", "NQ"),
    ("ResultHQ1", "HQ1"),
    ("ResultHQ2", "HQ2"),
    ("ResultHQ3", "HQ3"),
]

KEY_ITEM_RE = re.compile(
    r"^\s*([A-Z0-9_]+)\s*=\s*(\d+)\s*,?",
    re.MULTILINE,
)


class RestrictedCraftAuditError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip()

    if text.lower() == "nan":
        return ""

    return text


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
        raise RestrictedCraftAuditError(
            f"Invalid config mapping: {CONFIG_FILE}"
        )

    source = data.get("db")

    if not isinstance(source, dict):
        source = data

    def required(*keys: str):
        for key in keys:
            if key in source:
                value = source[key]

                if value not in {
                    None,
                    "",
                }:
                    return value

        raise RestrictedCraftAuditError(
            "Missing DB config key; expected one of: "
            + ", ".join(keys)
        )

    return {
        "host": required(
            "hostname",
            "host",
        ),
        "database": required(
            "database",
            "dbname",
        ),
        "user": required(
            "username",
            "user",
        ),
        "password": required(
            "password",
        ),
        "port": int(
            source.get(
                "port",
                3306,
            )
        ),
    }


def load_key_items() -> dict[int, str]:
    if not KEY_ITEM_ENUM.exists():
        raise RestrictedCraftAuditError(
            f"Missing LSB key-item enum: {KEY_ITEM_ENUM}"
        )

    text = KEY_ITEM_ENUM.read_text(
        encoding="utf-8",
        errors="replace",
    )

    mapping: dict[int, str] = {}

    for name, numeric in KEY_ITEM_RE.findall(
        text
    ):
        mapping[
            int(numeric)
        ] = name

    if not mapping:
        raise RestrictedCraftAuditError(
            "No key-item constants were parsed."
        )

    return mapping


def load_recipes() -> pd.DataFrame:
    cfg = load_db_settings()

    connection = pymysql.connect(
        host=str(
            cfg["host"]
        ),
        user=str(
            cfg["user"]
        ),
        password=str(
            cfg["password"]
        ),
        database=str(
            cfg["database"]
        ),
        port=int(
            cfg["port"]
        ),
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
            `Result`,
            ResultHQ1,
            ResultHQ2,
            ResultHQ3,
            ResultQty,
            ResultHQ1Qty,
            ResultHQ2Qty,
            ResultHQ3Qty,
            ResultName,
            content_tag
        FROM synth_recipes
        WHERE
            Desynth = 0
            AND KeyItem > 0
        ORDER BY ID
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                query
            )

            rows = cursor.fetchall()

    finally:
        connection.close()

    frame = pd.DataFrame(
        rows
    )

    if frame.empty:
        raise RestrictedCraftAuditError(
            "No restricted synthesis recipes were loaded."
        )

    return frame


def recipe_skill(
    row: pd.Series,
) -> tuple[
    int,
    str,
]:
    values = {
        column:
            as_int(
                row.get(
                    column
                )
            )
        for column in SKILL_COLUMNS
    }

    max_skill = max(
        values.values(),
        default=0,
    )

    max_names = [
        name
        for name, level
        in values.items()
        if (
            level == max_skill
            and level > 0
        )
    ]

    return (
        max_skill,
        "|".join(
            max_names
        ),
    )


def main() -> int:
    if not AUDIT_FILE.exists():
        raise RestrictedCraftAuditError(
            f"Missing audit file: {AUDIT_FILE}"
        )

    audit = pd.read_csv(
        AUDIT_FILE,
        low_memory=False,
    )

    required = {
        "audit_bucket",
        "itemid",
        "name",
        "candidate_class",
        "candidate_reason",
        "pricing_ready",
        "vendor_item",
        "rare",
        "can_equip",
        "stack_size",
        "base_sell",
        "rejection_reason",
    }

    missing = sorted(
        required
        - set(
            audit.columns
        )
    )

    if missing:
        raise RestrictedCraftAuditError(
            "candidate-market-audit.csv missing: "
            + ", ".join(missing)
        )

    restricted = audit[
        audit[
            "audit_bucket"
        ]
        == "RESTRICTED_CRAFT"
    ].copy()

    if restricted.empty:
        raise RestrictedCraftAuditError(
            "No RESTRICTED_CRAFT audit rows were found."
        )

    if restricted[
        "itemid"
    ].duplicated().any():
        raise RestrictedCraftAuditError(
            "Restricted-craft audit contains duplicate item IDs."
        )

    key_items = load_key_items()
    recipes = load_recipes()

    item_lookup = (
        restricted
        .set_index(
            "itemid"
        )
        .to_dict(
            orient="index"
        )
    )

    restricted_ids = set(
        restricted[
            "itemid"
        ].astype(int)
    )

    detail_rows: list[dict[str, Any]] = []

    for _, recipe in recipes.iterrows():
        (
            skill_level,
            skill_names,
        ) = recipe_skill(
            recipe
        )

        keyitem_id = as_int(
            recipe.get(
                "KeyItem"
            )
        )

        keyitem_name = key_items.get(
            keyitem_id,
            f"UNKNOWN_KEYITEM_{keyitem_id}",
        )

        for (
            output_column,
            output_role,
        ) in OUTPUT_COLUMNS:
            itemid = as_int(
                recipe.get(
                    output_column
                )
            )

            if (
                itemid <= 0
                or itemid
                not in restricted_ids
            ):
                continue

            item = item_lookup[
                itemid
            ]

            quantity_column = (
                "ResultQty"
                if output_role == "NQ"
                else f"Result{output_role}Qty"
            )

            detail_rows.append(
                {
                    "itemid":
                        itemid,

                    "name":
                        clean_text(
                            item.get(
                                "name"
                            )
                        ),

                    "recipe_id":
                        as_int(
                            recipe.get(
                                "ID"
                            )
                        ),

                    "output_role":
                        output_role,

                    "output_quantity":
                        as_int(
                            recipe.get(
                                quantity_column
                            )
                        ),

                    "keyitem_id":
                        keyitem_id,

                    "keyitem_name":
                        keyitem_name,

                    "craft_skill":
                        skill_names,

                    "craft_level":
                        skill_level,

                    "content_tag":
                        clean_text(
                            recipe.get(
                                "content_tag"
                            )
                        ),

                    "result_name":
                        clean_text(
                            recipe.get(
                                "ResultName"
                            )
                        ),

                    "candidate_class":
                        clean_text(
                            item.get(
                                "candidate_class"
                            )
                        ),

                    "candidate_reason":
                        clean_text(
                            item.get(
                                "candidate_reason"
                            )
                        ),

                    "pricing_ready":
                        as_int(
                            item.get(
                                "pricing_ready"
                            )
                        ),

                    "vendor_item":
                        as_int(
                            item.get(
                                "vendor_item"
                            )
                        ),

                    "rare":
                        as_int(
                            item.get(
                                "rare"
                            )
                        ),

                    "can_equip":
                        as_int(
                            item.get(
                                "can_equip"
                            )
                        ),

                    "stack_size":
                        as_int(
                            item.get(
                                "stack_size"
                            )
                        ),

                    "base_sell":
                        as_int(
                            item.get(
                                "base_sell"
                            )
                        ),

                    "rejection_reason":
                        clean_text(
                            item.get(
                                "rejection_reason"
                            )
                        ),
                }
            )

    detail = pd.DataFrame(
        detail_rows
    )

    if detail.empty:
        raise RestrictedCraftAuditError(
            "No restricted audit items matched KeyItem recipes."
        )

    matched_ids = set(
        detail[
            "itemid"
        ].astype(int)
    )

    unmatched_ids = sorted(
        restricted_ids
        - matched_ids
    )

    detail = detail.sort_values(
        by=[
            "keyitem_id",
            "itemid",
            "recipe_id",
            "output_role",
        ],
        kind="stable",
    )

    detail.to_csv(
        DETAIL_FILE,
        index=False,
    )

    summary_rows: list[dict[str, Any]] = []

    for (
        keyitem_id,
        keyitem_name,
    ), group in detail.groupby(
        [
            "keyitem_id",
            "keyitem_name",
        ],
        sort=True,
    ):
        unique_items = (
            group[
                [
                    "itemid",
                    "name",
                    "rare",
                    "can_equip",
                    "stack_size",
                    "pricing_ready",
                ]
            ]
            .drop_duplicates(
                subset=[
                    "itemid"
                ]
            )
        )

        summary_rows.append(
            {
                "keyitem_id":
                    int(
                        keyitem_id
                    ),

                "keyitem_name":
                    keyitem_name,

                "item_count":
                    int(
                        unique_items[
                            "itemid"
                        ].nunique()
                    ),

                "recipe_count":
                    int(
                        group[
                            "recipe_id"
                        ].nunique()
                    ),

                "nq_output_items":
                    int(
                        group.loc[
                            group[
                                "output_role"
                            ]
                            == "NQ",
                            "itemid",
                        ].nunique()
                    ),

                "hq_output_items":
                    int(
                        group.loc[
                            group[
                                "output_role"
                            ]
                            != "NQ",
                            "itemid",
                        ].nunique()
                    ),

                "equipment_items":
                    int(
                        unique_items[
                            "can_equip"
                        ].sum()
                    ),

                "rare_items":
                    int(
                        unique_items[
                            "rare"
                        ].sum()
                    ),

                "stack_12_items":
                    int(
                        (
                            unique_items[
                                "stack_size"
                            ]
                            == 12
                        ).sum()
                    ),

                "stack_99_items":
                    int(
                        (
                            unique_items[
                                "stack_size"
                            ]
                            == 99
                        ).sum()
                    ),

                "pricing_ready_items":
                    int(
                        unique_items[
                            "pricing_ready"
                        ].sum()
                    ),

                "min_craft_level":
                    int(
                        group[
                            "craft_level"
                        ].min()
                    ),

                "max_craft_level":
                    int(
                        group[
                            "craft_level"
                        ].max()
                    ),

                "craft_skills":
                    "|".join(
                        sorted(
                            {
                                clean_text(value)
                                for value
                                in group[
                                    "craft_skill"
                                ].dropna()
                                if clean_text(
                                    value
                                )
                            }
                        )
                    ),

                "content_tags":
                    "|".join(
                        sorted(
                            {
                                clean_text(value)
                                for value
                                in group[
                                    "content_tag"
                                ]
                                if clean_text(
                                    value
                                )
                            }
                        )
                    ),

                "sample_items":
                    "|".join(
                        unique_items[
                            "name"
                        ]
                        .head(12)
                        .map(
                            clean_text
                        )
                        .tolist()
                    ),
            }
        )

    keyitem_summary = pd.DataFrame(
        summary_rows
    ).sort_values(
        by=[
            "item_count",
            "keyitem_id",
        ],
        ascending=[
            False,
            True,
        ],
        kind="stable",
    )

    keyitem_summary.to_csv(
        KEYITEM_FILE,
        index=False,
    )

    summary = {
        "status":
            "PASS",

        "restricted_audit_items":
            int(
                len(
                    restricted
                )
            ),

        "matched_items":
            int(
                len(
                    matched_ids
                )
            ),

        "unmatched_items":
            int(
                len(
                    unmatched_ids
                )
            ),

        "unmatched_itemids":
            unmatched_ids,

        "detail_rows":
            int(
                len(
                    detail
                )
            ),

        "distinct_keyitems":
            int(
                keyitem_summary[
                    "keyitem_id"
                ].nunique()
            ),

        "keyitem_enum_entries":
            int(
                len(
                    key_items
                )
            ),
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
        " Restricted Craft Audit"
    )
    print(
        "======================================"
    )

    print(
        f"Restricted audit items: "
        f"{summary['restricted_audit_items']:>6}"
    )

    print(
        f"Matched items:          "
        f"{summary['matched_items']:>6}"
    )

    print(
        f"Unmatched items:        "
        f"{summary['unmatched_items']:>6}"
    )

    print(
        f"Distinct key items:     "
        f"{summary['distinct_keyitems']:>6}"
    )

    print(
        f"Recipe/output rows:     "
        f"{summary['detail_rows']:>6}"
    )

    print()
    print(
        "Top crafting key-item groups:"
    )

    display = keyitem_summary[
        [
            "keyitem_id",
            "keyitem_name",
            "item_count",
            "recipe_count",
            "equipment_items",
            "rare_items",
            "min_craft_level",
            "max_craft_level",
        ]
    ].head(
        30
    )

    print(
        display.to_string(
            index=False
        )
    )

    print()
    print(
        f"Detail:  {DETAIL_FILE}"
    )

    print(
        f"Groups:  {KEYITEM_FILE}"
    )

    print(
        f"Summary: {SUMMARY_FILE}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
