from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pymysql
import yaml


ROOT = Path.home() / "ffxiahbot"

CONFIG_FILE = ROOT / "bin" / "config.yaml"
HQ_AUDIT_FILE = ROOT / "economy" / "reports" / "hq-rare-maturity-audit.csv"
FALSE_HQ_FILE = ROOT / "economy" / "reports" / "false-hq-nq-recipe-audit.csv"

OUTPUT_FILE = ROOT / "economy" / "generated" / "hq-rare-maturity-policy.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "hq-rare-maturity-policy-summary.json"


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


class HQRarePolicyError(RuntimeError):
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


def load_db_settings() -> dict[str, Any]:
    data = yaml.safe_load(
        CONFIG_FILE.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(data, dict):
        raise HQRarePolicyError(
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

        raise HQRarePolicyError(
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


def load_nq_recipe_maturity() -> dict[int, str]:
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
            Wood,
            Smith,
            Gold,
            Cloth,
            Leather,
            Bone,
            Alchemy,
            Cook,
            Result
        FROM synth_recipes
        WHERE Desynth = 0
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

    earliest_skill: dict[
        int,
        int,
    ] = {}

    for recipe in rows:
        itemid = as_int(
            recipe.get(
                "Result"
            )
        )

        if itemid <= 0:
            continue

        skill = max(
            [
                as_int(
                    recipe.get(
                        column
                    )
                )
                for column in SKILL_COLUMNS
            ],
            default=0,
        )

        if itemid not in earliest_skill:
            earliest_skill[
                itemid
            ] = skill

        else:
            earliest_skill[
                itemid
            ] = min(
                earliest_skill[
                    itemid
                ],
                skill,
            )

    return {
        itemid:
            maturity_for_skill(
                skill
            )
        for itemid, skill
        in earliest_skill.items()
    }


def main() -> int:
    for path in [
        HQ_AUDIT_FILE,
        FALSE_HQ_FILE,
    ]:
        if not path.exists():
            raise HQRarePolicyError(
                f"Missing required input: {path}"
            )

    audit = pd.read_csv(
        HQ_AUDIT_FILE,
        low_memory=False,
    )

    false_hq = pd.read_csv(
        FALSE_HQ_FILE,
        low_memory=False,
    )

    if len(
        audit
    ) != 2040:
        raise HQRarePolicyError(
            f"Expected 2040 HQ/Rare audit rows, found {len(audit)}"
        )

    false_hq_item = (
        false_hq
        .sort_values(
            by=[
                "itemid",
                "craft_level",
                "recipe_id",
            ],
            kind="stable",
        )
        .drop_duplicates(
            subset=[
                "itemid"
            ],
            keep="first",
        )
        .set_index(
            "itemid"
        )
        .to_dict(
            orient="index"
        )
    )

    if len(
        false_hq_item
    ) != 42:
        raise HQRarePolicyError(
            "Expected exactly 42 unique false-HQ items."
        )

    nq_maturity = load_nq_recipe_maturity()

    rows: list[
        dict[
            str,
            Any,
        ]
    ] = []

    for _, row in audit.iterrows():
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

        vendor_item = as_int(
            row.get(
                "vendor_item"
            )
        )

        provenance_tags = clean_text(
            row.get(
                "provenance_tags"
            )
        )

        if bucket == "HQ_MATURITY_REVIEW":
            policy_family = "HQ"

            if as_int(
                row.get(
                    "hq_recipe_count"
                )
            ) > 0:
                recommended_class = "SCARCE"
                policy_reason = "RECIPE_BACKED_HQ_OUTPUT"
                minimum_maturity = clean_text(
                    row.get(
                        "preliminary_minimum_maturity"
                    )
                )
                maturity_rule = "HQ_RECIPE_STAGE_PLUS_ONE"

            elif itemid in false_hq_item:
                evidence = false_hq_item[
                    itemid
                ]

                recommended_class = "SCARCE"
                policy_reason = "INDEPENDENT_NQ_PLUS_ONE_RECIPE"
                minimum_maturity = clean_text(
                    evidence.get(
                        "minimum_maturity"
                    )
                )
                maturity_rule = "OWN_NQ_RECIPE_SKILL"

            else:
                raise HQRarePolicyError(
                    f"HQ item {itemid} is neither recipe-backed "
                    "nor in the false-HQ resolution set."
                )

            seller_eligible = 1
            buyer_eligible = int(
                not vendor_item
            )
            manual_review_required = 0

        elif bucket == "RARE_ITEM_REVIEW":
            policy_family = "RARE"
            recommended_class = "SCARCE"
            seller_eligible = 1
            buyer_eligible = int(
                not vendor_item
            )
            manual_review_required = 0

            if "CRAFT_NQ_OUTPUT" in provenance_tags:
                minimum_maturity = nq_maturity.get(
                    itemid,
                    "",
                )

                if not minimum_maturity:
                    raise HQRarePolicyError(
                        f"Rare crafted item {itemid} has no NQ recipe maturity."
                    )

                policy_reason = "RARE_CRAFTED_ITEM"
                maturity_rule = "OWN_NQ_RECIPE_SKILL"

            elif "FISHING" in provenance_tags:
                minimum_maturity = ""
                policy_reason = "RARE_FISHING_ITEM"
                maturity_rule = "FISHING_MATURITY_PENDING"

            else:
                raise HQRarePolicyError(
                    f"Rare item {itemid} lacks expected craft/fishing provenance."
                )

        else:
            raise HQRarePolicyError(
                f"Unexpected audit bucket {bucket!r} for item {itemid}"
            )

        if vendor_item:
            buyer_eligible = 0

        rows.append(
            {
                "itemid":
                    itemid,

                "name":
                    clean_text(
                        row.get(
                            "name"
                        )
                    ),

                "policy_family":
                    policy_family,

                "recommended_class":
                    recommended_class,

                "policy_reason":
                    policy_reason,

                "minimum_maturity":
                    minimum_maturity,

                "maturity_rule":
                    maturity_rule,

                "seller_eligible":
                    int(
                        seller_eligible
                    ),

                "buyer_eligible":
                    int(
                        buyer_eligible
                    ),

                "manual_review_required":
                    int(
                        manual_review_required
                    ),

                "vendor_item":
                    vendor_item,

                "pricing_ready":
                    as_int(
                        row.get(
                            "pricing_ready"
                        )
                    ),

                "provenance_tags":
                    provenance_tags,

                "activation_ready":
                    0,

                "auto_live_promotion":
                    0,
            }
        )

    output = pd.DataFrame(
        rows
    ).sort_values(
        by=[
            "policy_family",
            "itemid",
        ],
        kind="stable",
    )

    if len(
        output
    ) != 2040:
        raise HQRarePolicyError(
            f"Expected 2040 final rows, found {len(output)}"
        )

    if int(
        output[
            "manual_review_required"
        ].sum()
    ) != 0:
        raise HQRarePolicyError(
            "Final HQ/Rare policy still has manual review rows."
        )

    if int(
        output[
            "activation_ready"
        ].sum()
    ) != 0:
        raise HQRarePolicyError(
            "Final HQ/Rare policy activated items."
        )

    if int(
        output[
            "auto_live_promotion"
        ].sum()
    ) != 0:
        raise HQRarePolicyError(
            "Final HQ/Rare policy attempted live promotion."
        )

    hq = output[
        output[
            "policy_family"
        ]
        == "HQ"
    ]

    rare = output[
        output[
            "policy_family"
        ]
        == "RARE"
    ]

    if len(
        hq
    ) != 2034:
        raise HQRarePolicyError(
            f"Expected 2034 HQ-family rows, found {len(hq)}"
        )

    if len(
        rare
    ) != 6:
        raise HQRarePolicyError(
            f"Expected 6 Rare rows, found {len(rare)}"
        )

    false_hq_resolved = int(
        (
            output[
                "policy_reason"
            ]
            == "INDEPENDENT_NQ_PLUS_ONE_RECIPE"
        ).sum()
    )

    if false_hq_resolved != 42:
        raise HQRarePolicyError(
            f"Expected 42 false-HQ resolutions, found {false_hq_resolved}"
        )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    summary = {
        "status":
            "PASS",

        "total_items":
            int(
                len(
                    output
                )
            ),

        "hq_scarce":
            int(
                len(
                    hq
                )
            ),

        "rare_scarce":
            int(
                len(
                    rare
                )
            ),

        "recipe_backed_hq":
            int(
                (
                    output[
                        "policy_reason"
                    ]
                    == "RECIPE_BACKED_HQ_OUTPUT"
                ).sum()
            ),

        "false_hq_resolved":
            false_hq_resolved,

        "rare_crafted":
            int(
                (
                    output[
                        "policy_reason"
                    ]
                    == "RARE_CRAFTED_ITEM"
                ).sum()
            ),

        "rare_fishing":
            int(
                (
                    output[
                        "policy_reason"
                    ]
                    == "RARE_FISHING_ITEM"
                ).sum()
            ),

        "minimum_maturity":
            {
                str(k):
                    int(v)
                for k, v
                in output[
                    "minimum_maturity"
                ]
                .replace(
                    "",
                    "NONE",
                )
                .value_counts()
                .to_dict()
                .items()
            },

        "seller_eligible":
            int(
                output[
                    "seller_eligible"
                ].sum()
            ),

        "buyer_eligible":
            int(
                output[
                    "buyer_eligible"
                ].sum()
            ),

        "manual_review_required":
            0,

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
        " Final HQ / Rare Maturity Policy"
    )
    print(
        "======================================"
    )

    print(
        f"Total items:              "
        f"{summary['total_items']:>6}"
    )

    print(
        f"HQ SCARCE:               "
        f"{summary['hq_scarce']:>6}"
    )

    print(
        f"Rare SCARCE:             "
        f"{summary['rare_scarce']:>6}"
    )

    print(
        f"Recipe-backed HQ:        "
        f"{summary['recipe_backed_hq']:>6}"
    )

    print(
        f"False-HQ resolved:       "
        f"{summary['false_hq_resolved']:>6}"
    )

    print(
        f"Rare crafted:            "
        f"{summary['rare_crafted']:>6}"
    )

    print(
        f"Rare fishing:            "
        f"{summary['rare_fishing']:>6}"
    )

    print()
    print(
        f"Seller eligible:         "
        f"{summary['seller_eligible']:>6}"
    )

    print(
        f"Buyer eligible:          "
        f"{summary['buyer_eligible']:>6}"
    )

    print(
        "Manual review required:      0"
    )

    print(
        "Activation ready:            0"
    )

    print(
        "Auto live promotions:        0"
    )

    print()
    print(
        f"Generated: {OUTPUT_FILE}"
    )

    print(
        f"Summary:   {SUMMARY_FILE}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
