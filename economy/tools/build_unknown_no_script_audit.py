from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pymysql
import yaml


ROOT = Path.home() / "ffxiahbot"

CONFIG_FILE = ROOT / "bin" / "config.yaml"
CENSUS_FILE = ROOT / "economy" / "reports" / "unknown-provenance-census.csv"

OUTPUT_FILE = ROOT / "economy" / "reports" / "unknown-no-script-audit.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "unknown-no-script-audit-summary.json"


TABLES = [
    "item_equipment",
    "item_weapon",
    "item_usable",
    "item_furnishing",
]


class NoScriptAuditError(RuntimeError):
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
        raise NoScriptAuditError(
            f"Invalid config mapping: {CONFIG_FILE}"
        )

    source = data.get("db")
    if not isinstance(source, dict):
        source = data

    def required(*keys: str):
        for key in keys:
            if key in source and source[key] not in {None, ""}:
                return source[key]
        raise NoScriptAuditError(
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


def load_table_itemids(
    connection: pymysql.connections.Connection,
    table: str,
) -> set[int]:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SHOW COLUMNS FROM `{table}`"
        )
        columns = [
            str(row["Field"])
            for row in cursor.fetchall()
        ]

    id_column = None

    for candidate in [
        "itemid",
        "itemId",
        "itemID",
    ]:
        if candidate in columns:
            id_column = candidate
            break

    if id_column is None:
        return set()

    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT `{id_column}` AS itemid FROM `{table}`"
        )

        return {
            as_int(row["itemid"])
            for row in cursor.fetchall()
            if as_int(row["itemid"]) > 0
        }


def load_semantic_sets() -> dict[str, set[int]]:
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

    try:
        result = {
            table:
                load_table_itemids(
                    connection,
                    table,
                )
            for table in TABLES
        }
    finally:
        connection.close()

    return result


def main() -> int:
    if not CENSUS_FILE.exists():
        raise NoScriptAuditError(
            f"Missing census file: {CENSUS_FILE}"
        )

    census = pd.read_csv(
        CENSUS_FILE,
        low_memory=False,
    )

    target = census[
        census["script_reference_count"] == 0
    ].copy()

    if len(target) != 2749:
        raise NoScriptAuditError(
            f"Expected 2749 no-script items, found {len(target)}"
        )

    semantic_sets = load_semantic_sets()

    rows: list[dict[str, Any]] = []

    for _, row in target.iterrows():
        itemid = as_int(row.get("itemid"))

        semantic_families = [
            table
            for table, itemids
            in semantic_sets.items()
            if itemid in itemids
        ]

        ah_category = as_int(
            row.get("ah_category")
        )

        stack_size = as_int(
            row.get("stack_size")
        )

        base_sell = as_int(
            row.get("base_sell")
        )

        if semantic_families:
            next_step = "DB_SEMANTIC_SOURCE_REVIEW"
            priority = 1
        elif ah_category > 0:
            next_step = "AH_LISTED_SOURCE_GAP"
            priority = 1
        else:
            next_step = "LIKELY_INTERNAL_OR_UNUSED_REVIEW"
            priority = 2

        rows.append(
            {
                "itemid": itemid,
                "name": clean_text(row.get("name")),
                "item_type": as_int(row.get("item_type")),
                "semantic_families": "|".join(
                    semantic_families
                ),
                "stack_size": stack_size,
                "flags": as_int(row.get("flags")),
                "ah_category": ah_category,
                "base_sell": base_sell,
                "rare": as_int(row.get("rare")),
                "can_equip": as_int(row.get("can_equip")),
                "pricing_ready": as_int(row.get("pricing_ready")),
                "missing_item_enum_constant": as_int(
                    row.get("missing_item_enum_constant")
                ),
                "candidate_class": clean_text(
                    row.get("candidate_class")
                ),
                "candidate_reason": clean_text(
                    row.get("candidate_reason")
                ),
                "recommended_next_step": next_step,
                "audit_priority": priority,
                "activation_ready": 0,
                "auto_live_promotion": 0,
            }
        )

    output = pd.DataFrame(rows).sort_values(
        by=[
            "audit_priority",
            "recommended_next_step",
            "item_type",
            "itemid",
        ],
        kind="stable",
    )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    summary = {
        "status": "PASS",
        "total_items": int(len(output)),
        "ah_listed_items": int(
            (output["ah_category"] > 0).sum()
        ),
        "not_ah_listed_items": int(
            (output["ah_category"] == 0).sum()
        ),
        "missing_item_enum_constant": int(
            output["missing_item_enum_constant"].sum()
        ),
        "semantic_family_counts": {
            table: int(
                output["semantic_families"]
                .fillna("")
                .map(
                    lambda value:
                        table in str(value).split("|")
                )
                .sum()
            )
            for table in TABLES
        },
        "item_type_counts": {
            str(k): int(v)
            for k, v in (
                output["item_type"]
                .value_counts()
                .sort_index()
                .to_dict()
                .items()
            )
        },
        "next_step_counts": {
            str(k): int(v)
            for k, v in (
                output["recommended_next_step"]
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
    print("======================================")
    print(" Unknown No-Script Audit")
    print("======================================")
    print(
        f"Total no-script items:          "
        f"{summary['total_items']:>6}"
    )
    print(
        f"AH-listed:                      "
        f"{summary['ah_listed_items']:>6}"
    )
    print(
        f"Not AH-listed:                  "
        f"{summary['not_ah_listed_items']:>6}"
    )
    print(
        f"Missing item enum constant:     "
        f"{summary['missing_item_enum_constant']:>6}"
    )
    print()
    print("Semantic families:")
    for family, count in summary[
        "semantic_family_counts"
    ].items():
        print(
            f"  {family:<24} "
            f"{count:>6}"
        )
    print()
    print("Next steps:")
    for step, count in sorted(
        summary["next_step_counts"].items(),
        key=lambda pair: (-pair[1], pair[0]),
    ):
        print(
            f"  {step:<32} "
            f"{count:>6}"
        )
    print()
    print("Activation ready: 0")
    print("Auto live promotions: 0")
    print()
    print(f"Audit:   {OUTPUT_FILE}")
    print(f"Summary: {SUMMARY_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
