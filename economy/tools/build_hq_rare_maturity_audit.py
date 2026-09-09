from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
import pymysql
import yaml


ROOT = Path.home() / "ffxiahbot"

CONFIG_FILE = ROOT / "bin" / "config.yaml"
AUDIT_FILE = ROOT / "economy" / "reports" / "candidate-market-audit.csv"
POLICY_FILE = ROOT / "economy" / "generated" / "candidate-market-policy.csv"
PROVENANCE_FILE = ROOT / "economy" / "generated" / "item-provenance.csv"

DETAIL_FILE = ROOT / "economy" / "reports" / "hq-rare-maturity-audit.csv"
RECIPE_FILE = ROOT / "economy" / "reports" / "hq-maturity-recipe-evidence.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "hq-rare-maturity-audit-summary.json"


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

HQ_COLUMNS = [
    ("ResultHQ1", "HQ1"),
    ("ResultHQ2", "HQ2"),
    ("ResultHQ3", "HQ3"),
]

MATURITY_ORDER = [
    "SEED",
    "GROWING",
    "ESTABLISHED",
    "MATURE",
    "ADVANCED",
    "FULL",
]


class HQRareAuditError(RuntimeError):
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
        raise HQRareAuditError(
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

        raise HQRareAuditError(
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


def load_db_tables() -> tuple[pd.DataFrame, pd.DataFrame]:
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

    recipe_query = """
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
            Result,
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
        WHERE Desynth = 0
        ORDER BY ID
    """

    item_query = """
        SELECT
            itemid,
            name,
            type,
            stackSize,
            flags,
            aH,
            BaseSell
        FROM item_basic
        ORDER BY itemid
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                recipe_query
            )
            recipes = pd.DataFrame(
                cursor.fetchall()
            )

            cursor.execute(
                item_query
            )
            items = pd.DataFrame(
                cursor.fetchall()
            )

    finally:
        connection.close()

    if recipes.empty:
        raise HQRareAuditError(
            "No synthesis recipes loaded."
        )

    if items.empty:
        raise HQRareAuditError(
            "No item_basic rows loaded."
        )

    return recipes, items


def recipe_skill(
    row: pd.Series,
) -> tuple[int, str]:
    values = {
        column:
            as_int(
                row.get(
                    column
                )
            )
        for column in SKILL_COLUMNS
    }

    max_level = max(
        values.values(),
        default=0,
    )

    skills = [
        name
        for name, level
        in values.items()
        if (
            level == max_level
            and level > 0
        )
    ]

    return (
        max_level,
        "|".join(
            skills
        ),
    )


def maturity_for_skill(
    skill: int,
) -> str:
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


def bump_maturity(
    maturity: str,
    steps: int = 1,
) -> str:
    if maturity not in MATURITY_ORDER:
        return maturity

    index = MATURITY_ORDER.index(
        maturity
    )

    return MATURITY_ORDER[
        min(
            index + steps,
            len(
                MATURITY_ORDER
            )
            - 1,
        )
    ]


def ensure_columns(
    frame: pd.DataFrame,
    columns: list[str],
) -> pd.DataFrame:
    out = frame.copy()

    for column in columns:
        if column not in out.columns:
            out[
                column
            ] = ""

    return out


def main() -> int:
    for path in [
        AUDIT_FILE,
        POLICY_FILE,
        PROVENANCE_FILE,
    ]:
        if not path.exists():
            raise HQRareAuditError(
                f"Missing required input: {path}"
            )

    audit = pd.read_csv(
        AUDIT_FILE,
        low_memory=False,
    )

    policy = pd.read_csv(
        POLICY_FILE,
        low_memory=False,
    )

    provenance = pd.read_csv(
        PROVENANCE_FILE,
        low_memory=False,
    )

    target = audit[
        audit[
            "audit_bucket"
        ].isin(
            [
                "HQ_MATURITY_REVIEW",
                "RARE_ITEM_REVIEW",
            ]
        )
    ].copy()

    if len(
        target
    ) != 2040:
        raise HQRareAuditError(
            "Expected 2040 HQ/Rare audit items, "
            f"found {len(target)}"
        )

    hq_target = target[
        target[
            "audit_bucket"
        ]
        == "HQ_MATURITY_REVIEW"
    ].copy()

    rare_target = target[
        target[
            "audit_bucket"
        ]
        == "RARE_ITEM_REVIEW"
    ].copy()

    if len(
        hq_target
    ) != 2034:
        raise HQRareAuditError(
            f"Expected 2034 HQ items, found {len(hq_target)}"
        )

    if len(
        rare_target
    ) != 6:
        raise HQRareAuditError(
            f"Expected 6 Rare items, found {len(rare_target)}"
        )

    recipes, items = load_db_tables()

    item_name = {
        as_int(
            row[
                "itemid"
            ]
        ):
            clean_text(
                row[
                    "name"
                ]
            )
        for _, row in items.iterrows()
    }

    hq_ids = set(
        hq_target[
            "itemid"
        ].astype(int)
    )

    recipe_rows: list[dict[str, Any]] = []
    evidence_by_item: dict[
        int,
        list[dict[str, Any]],
    ] = defaultdict(
        list
    )

    for _, recipe in recipes.iterrows():
        parent_itemid = as_int(
            recipe.get(
                "Result"
            )
        )

        parent_name = item_name.get(
            parent_itemid,
            "",
        )

        (
            craft_level,
            craft_skill,
        ) = recipe_skill(
            recipe
        )

        base_maturity = maturity_for_skill(
            craft_level
        )

        for column, role in HQ_COLUMNS:
            hq_itemid = as_int(
                recipe.get(
                    column
                )
            )

            if (
                hq_itemid <= 0
                or hq_itemid not in hq_ids
            ):
                continue

            quantity_column = {
                "HQ1":
                    "ResultHQ1Qty",
                "HQ2":
                    "ResultHQ2Qty",
                "HQ3":
                    "ResultHQ3Qty",
            }[
                role
            ]

            evidence = {
                "itemid":
                    hq_itemid,

                "name":
                    item_name.get(
                        hq_itemid,
                        "",
                    ),

                "recipe_id":
                    as_int(
                        recipe.get(
                            "ID"
                        )
                    ),

                "hq_role":
                    role,

                "parent_itemid":
                    parent_itemid,

                "parent_name":
                    parent_name,

                "craft_skill":
                    craft_skill,

                "craft_level":
                    craft_level,

                "base_maturity":
                    base_maturity,

                # Preliminary conservative rule only.
                # All distinct HQ outputs enter one maturity tier later.
                "preliminary_hq_maturity":
                    bump_maturity(
                        base_maturity,
                        1,
                    ),

                "keyitem_id":
                    as_int(
                        recipe.get(
                            "KeyItem"
                        )
                    ),

                "content_tag":
                    clean_text(
                        recipe.get(
                            "content_tag"
                        )
                    ),

                "parent_quantity":
                    as_int(
                        recipe.get(
                            "ResultQty"
                        )
                    ),

                "hq_quantity":
                    as_int(
                        recipe.get(
                            quantity_column
                        )
                    ),

                "same_as_parent_item":
                    int(
                        hq_itemid
                        == parent_itemid
                    ),
            }

            recipe_rows.append(
                evidence
            )

            evidence_by_item[
                hq_itemid
            ].append(
                evidence
            )

    recipe_frame = pd.DataFrame(
        recipe_rows
    )

    if recipe_frame.empty:
        raise HQRareAuditError(
            "No HQ recipe evidence matched target items."
        )

    recipe_frame = recipe_frame.sort_values(
        by=[
            "itemid",
            "recipe_id",
            "hq_role",
        ],
        kind="stable",
    )

    recipe_frame.to_csv(
        RECIPE_FILE,
        index=False,
    )

    policy = ensure_columns(
        policy,
        [
            "itemid",
            "candidate_class",
            "candidate_reason",
            "vendor_item",
            "has_vendor_price_floor",
            "pricing_ready",
            "stack_size",
            "rare",
            "can_equip",
            "mob_source_class",
            "source_override_applied",
            "source_override_types",
        ],
    )

    provenance = ensure_columns(
        provenance,
        [
            "itemid",
            "provenance_tags",
            "craft_nq_output",
            "craft_hq_output",
            "restricted_nq_output",
            "restricted_hq_output",
            "craft_input",
            "fishing",
            "vendor_item",
        ],
    )

    merged = target.merge(
        policy[
            [
                "itemid",
                "candidate_class",
                "candidate_reason",
                "vendor_item",
                "has_vendor_price_floor",
                "pricing_ready",
                "stack_size",
                "rare",
                "can_equip",
                "mob_source_class",
                "source_override_applied",
                "source_override_types",
            ]
        ],
        on="itemid",
        how="left",
        validate="one_to_one",
        suffixes=(
            "",
            "_policy",
        ),
    ).merge(
        provenance[
            [
                "itemid",
                "provenance_tags",
                "craft_nq_output",
                "craft_hq_output",
                "restricted_nq_output",
                "restricted_hq_output",
                "craft_input",
                "fishing",
            ]
        ],
        on="itemid",
        how="left",
        validate="one_to_one",
    )

    output_rows: list[dict[str, Any]] = []

    for _, row in merged.iterrows():
        itemid = as_int(
            row.get(
                "itemid"
            )
        )

        bucket = clean_text(
            row.get(
                "audit_bucket"
            )
        )

        evidence = evidence_by_item.get(
            itemid,
            [],
        )

        if bucket == "HQ_MATURITY_REVIEW":
            recipe_count = len(
                {
                    entry[
                        "recipe_id"
                    ]
                    for entry in evidence
                }
            )

            hq_roles = sorted(
                {
                    entry[
                        "hq_role"
                    ]
                    for entry in evidence
                }
            )

            parent_ids = sorted(
                {
                    entry[
                        "parent_itemid"
                    ]
                    for entry in evidence
                    if entry[
                        "parent_itemid"
                    ] > 0
                }
            )

            parent_names = sorted(
                {
                    entry[
                        "parent_name"
                    ]
                    for entry in evidence
                    if entry[
                        "parent_name"
                    ]
                }
            )

            craft_levels = [
                entry[
                    "craft_level"
                ]
                for entry in evidence
                if entry[
                    "craft_level"
                ] > 0
            ]

            base_maturities = [
                entry[
                    "base_maturity"
                ]
                for entry in evidence
                if entry[
                    "base_maturity"
                ]
            ]

            hq_maturities = [
                entry[
                    "preliminary_hq_maturity"
                ]
                for entry in evidence
                if entry[
                    "preliminary_hq_maturity"
                ]
            ]

            keyitem_ids = sorted(
                {
                    entry[
                        "keyitem_id"
                    ]
                    for entry in evidence
                    if entry[
                        "keyitem_id"
                    ] > 0
                }
            )

            content_tags = sorted(
                {
                    entry[
                        "content_tag"
                    ]
                    for entry in evidence
                    if entry[
                        "content_tag"
                    ]
                }
            )

            if evidence:
                recommended_action = "HQ_MATURITY_RULE_CANDIDATE"
                review_reason = "DISTINCT_HQ_SYNTH_OUTPUT"
            else:
                recommended_action = "MANUAL_REVIEW"
                review_reason = "HQ_RECIPE_EVIDENCE_MISSING"

            preliminary_maturity = ""

            if hq_maturities:
                # Earliest legitimate synthesis route controls availability.
                preliminary_maturity = min(
                    hq_maturities,
                    key=MATURITY_ORDER.index,
                )

            preliminary_class = "SCARCE"

        elif bucket == "RARE_ITEM_REVIEW":
            recipe_count = 0
            hq_roles = []
            parent_ids = []
            parent_names = []
            craft_levels = []
            base_maturities = []
            hq_maturities = []
            keyitem_ids = []
            content_tags = []
            preliminary_maturity = ""
            preliminary_class = "SCARCE"
            recommended_action = "RARE_SOURCE_POLICY_REVIEW"
            review_reason = "RARE_DOES_NOT_IMPLY_BLOCKED"

        else:
            raise HQRareAuditError(
                f"Unexpected bucket {bucket!r}"
            )

        output_rows.append(
            {
                "audit_bucket":
                    bucket,

                "itemid":
                    itemid,

                "name":
                    clean_text(
                        row.get(
                            "name"
                        )
                    ),

                "candidate_class":
                    clean_text(
                        row.get(
                            "candidate_class"
                        )
                    ),

                "candidate_reason":
                    clean_text(
                        row.get(
                            "candidate_reason"
                        )
                    ),

                "preliminary_class":
                    preliminary_class,

                "preliminary_minimum_maturity":
                    preliminary_maturity,

                "recommended_action":
                    recommended_action,

                "review_reason":
                    review_reason,

                "hq_recipe_count":
                    recipe_count,

                "hq_roles":
                    "|".join(
                        hq_roles
                    ),

                "parent_itemids":
                    "|".join(
                        str(
                            value
                        )
                        for value in parent_ids
                    ),

                "parent_names":
                    "|".join(
                        parent_names
                    ),

                "min_craft_level":
                    (
                        min(
                            craft_levels
                        )
                        if craft_levels
                        else 0
                    ),

                "max_craft_level":
                    (
                        max(
                            craft_levels
                        )
                        if craft_levels
                        else 0
                    ),

                "base_maturities":
                    "|".join(
                        sorted(
                            set(
                                base_maturities
                            ),
                            key=MATURITY_ORDER.index,
                        )
                    ),

                "hq_maturities":
                    "|".join(
                        sorted(
                            set(
                                hq_maturities
                            ),
                            key=MATURITY_ORDER.index,
                        )
                    ),

                "restricted_recipe":
                    int(
                        bool(
                            keyitem_ids
                        )
                    ),

                "keyitem_ids":
                    "|".join(
                        str(
                            value
                        )
                        for value in keyitem_ids
                    ),

                "content_tags":
                    "|".join(
                        content_tags
                    ),

                "vendor_item":
                    as_int(
                        row.get(
                            "vendor_item"
                        )
                    ),

                "has_vendor_price_floor":
                    as_int(
                        row.get(
                            "has_vendor_price_floor"
                        )
                    ),

                "pricing_ready":
                    as_int(
                        row.get(
                            "pricing_ready"
                        )
                    ),

                "stack_size":
                    as_int(
                        row.get(
                            "stack_size"
                        )
                    ),

                "rare":
                    as_int(
                        row.get(
                            "rare"
                        )
                    ),

                "can_equip":
                    as_int(
                        row.get(
                            "can_equip"
                        )
                    ),

                "mob_source_class":
                    clean_text(
                        row.get(
                            "mob_source_class"
                        )
                    ),

                "source_override_applied":
                    as_int(
                        row.get(
                            "source_override_applied"
                        )
                    ),

                "source_override_types":
                    clean_text(
                        row.get(
                            "source_override_types"
                        )
                    ),

                "provenance_tags":
                    clean_text(
                        row.get(
                            "provenance_tags"
                        )
                    ),

                "activation_ready":
                    0,

                "auto_live_promotion":
                    0,
            }
        )

    output = pd.DataFrame(
        output_rows
    ).sort_values(
        by=[
            "audit_bucket",
            "itemid",
        ],
        kind="stable",
    )

    if int(
        output[
            "activation_ready"
        ].sum()
    ) != 0:
        raise HQRareAuditError(
            "HQ/Rare audit activated items."
        )

    if int(
        output[
            "auto_live_promotion"
        ].sum()
    ) != 0:
        raise HQRareAuditError(
            "HQ/Rare audit attempted live promotion."
        )

    output.to_csv(
        DETAIL_FILE,
        index=False,
    )

    hq_output = output[
        output[
            "audit_bucket"
        ]
        == "HQ_MATURITY_REVIEW"
    ]

    summary = {
        "status":
            "PASS",

        "total_items":
            int(
                len(
                    output
                )
            ),

        "hq_items":
            int(
                len(
                    hq_output
                )
            ),

        "rare_items":
            int(
                (
                    output[
                        "audit_bucket"
                    ]
                    == "RARE_ITEM_REVIEW"
                ).sum()
            ),

        "hq_items_with_recipe_evidence":
            int(
                (
                    hq_output[
                        "hq_recipe_count"
                    ]
                    > 0
                ).sum()
            ),

        "hq_items_missing_recipe_evidence":
            int(
                (
                    hq_output[
                        "hq_recipe_count"
                    ]
                    == 0
                ).sum()
            ),

        "hq_items_with_restricted_recipe":
            int(
                hq_output[
                    "restricted_recipe"
                ].sum()
            ),

        "preliminary_hq_maturity":
            {
                str(k):
                    int(v)
                for k, v
                in hq_output[
                    "preliminary_minimum_maturity"
                ]
                .replace(
                    "",
                    "NONE",
                )
                .value_counts()
                .to_dict()
                .items()
            },

        "hq_role_patterns":
            {
                str(k):
                    int(v)
                for k, v
                in hq_output[
                    "hq_roles"
                ]
                .replace(
                    "",
                    "NONE",
                )
                .value_counts()
                .to_dict()
                .items()
            },

        "activation_ready":
            0,

        "auto_live_promotions":
            0,
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
        " HQ / Rare Maturity Audit"
    )
    print(
        "======================================"
    )

    print(
        f"Total items:                    "
        f"{summary['total_items']:>6}"
    )

    print(
        f"HQ items:                       "
        f"{summary['hq_items']:>6}"
    )

    print(
        f"Rare items:                     "
        f"{summary['rare_items']:>6}"
    )

    print()
    print(
        f"HQ with recipe evidence:        "
        f"{summary['hq_items_with_recipe_evidence']:>6}"
    )

    print(
        f"HQ missing recipe evidence:     "
        f"{summary['hq_items_missing_recipe_evidence']:>6}"
    )

    print(
        f"HQ with restricted recipe:      "
        f"{summary['hq_items_with_restricted_recipe']:>6}"
    )

    print()
    print(
        "Preliminary HQ maturity:"
    )

    for stage in [
        *MATURITY_ORDER,
        "NONE",
    ]:
        count = summary[
            "preliminary_hq_maturity"
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
        f"Audit:    {DETAIL_FILE}"
    )

    print(
        f"Recipes:  {RECIPE_FILE}"
    )

    print(
        f"Summary:  {SUMMARY_FILE}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
