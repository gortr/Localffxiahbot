from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pymysql
import yaml


ROOT = Path.home() / "ffxiahbot"

CONFIG_FILE = ROOT / "bin" / "config.yaml"
NO_SCRIPT_FILE = ROOT / "economy" / "reports" / "unknown-no-script-audit.csv"

OUTPUT_FILE = ROOT / "economy" / "reports" / "unknown-db-item-source-census.csv"
TABLE_FILE = ROOT / "economy" / "reports" / "unknown-db-item-source-tables.csv"
VISIBLE_FILE = ROOT / "economy" / "reports" / "unknown-db-visible-tables.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "unknown-db-item-source-census-summary.json"


# These are already consumed by existing provenance builders or describe
# the item itself rather than where the player obtains it.
EXCLUDED_TABLES = {
    "item_basic",
    "item_equipment",
    "item_weapon",
    "item_usable",
    "item_furnishing",
    "item_mods",
    "item_latents",
    "item_puppet",
    "synth_recipes",
    "mob_droplist",
    "fishing_fish",
    "auction_house",
}

# Character/account/runtime state is not acquisition provenance.
EXCLUDED_PREFIXES = (
    "char_",
    "account",
    "delivery",
    "audit_",
)

NUMERIC_TYPES = {
    "tinyint",
    "smallint",
    "mediumint",
    "int",
    "integer",
    "bigint",
    "decimal",
}

# Strong item-column shapes. Normalization removes punctuation/case.
ITEM_COLUMN_EXACT = {
    "item",
    "itemid",
    "itemno",
    "itemnum",
    "itemnumber",
    "rewarditem",
    "rewarditemid",
    "lootitem",
    "lootitemid",
    "dropitem",
    "dropitemid",
}


class DBSourceCensusError(RuntimeError):
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


def normalize_identifier(value: str) -> str:
    return re.sub(
        r"[^a-z0-9]",
        "",
        value.lower(),
    )


def looks_like_item_column(column_name: str) -> bool:
    normalized = normalize_identifier(
        column_name
    )

    if normalized in ITEM_COLUMN_EXACT:
        return True

    # item1, item2, itemid1, rewarditem3, etc.
    if re.fullmatch(
        r"(?:reward|loot|drop)?item(?:id|no|num|number)?\d+",
        normalized,
    ):
        return True

    return False


def excluded_table(table_name: str) -> bool:
    low = table_name.lower()

    if low in EXCLUDED_TABLES:
        return True

    return any(
        low.startswith(prefix)
        for prefix in EXCLUDED_PREFIXES
    )


def load_db_settings() -> dict[str, Any]:
    data = yaml.safe_load(
        CONFIG_FILE.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(data, dict):
        raise DBSourceCensusError(
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

        raise DBSourceCensusError(
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


def main() -> int:
    if not NO_SCRIPT_FILE.exists():
        raise DBSourceCensusError(
            f"Missing no-script audit: {NO_SCRIPT_FILE}"
        )

    target = pd.read_csv(
        NO_SCRIPT_FILE,
        low_memory=False,
    )

    if len(
        target
    ) != 2749:
        raise DBSourceCensusError(
            f"Expected 2749 no-script items, found {len(target)}"
        )

    target_ids = set(
        target[
            "itemid"
        ].astype(int)
    )

    name_by_id = {
        int(itemid):
            clean_text(name)
        for itemid, name
        in zip(
            target[
                "itemid"
            ],
            target[
                "name"
            ],
        )
    }

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

    visible_rows: list[
        dict[
            str,
            Any,
        ]
    ] = []

    candidate_columns: list[
        tuple[
            str,
            str,
            str,
        ]
    ] = []

    hits: list[
        dict[
            str,
            Any,
        ]
    ] = []

    try:
        # SHOW TABLES only returns objects the current MariaDB account can see.
        with connection.cursor() as cursor:
            cursor.execute(
                "SHOW TABLES"
            )

            table_result = cursor.fetchall()

        visible_tables = []

        for row in table_result:
            values = list(
                row.values()
            )

            if not values:
                continue

            visible_tables.append(
                clean_text(
                    values[0]
                )
            )

        for table_name in sorted(
            set(
                visible_tables
            )
        ):
            is_excluded = int(
                excluded_table(
                    table_name
                )
            )

            columns_seen = 0
            numeric_columns = 0
            item_columns = 0
            show_columns_error = ""

            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SHOW COLUMNS FROM `{table_name}`"
                    )

                    columns = cursor.fetchall()

            except Exception as exc:
                columns = []
                show_columns_error = str(
                    exc
                )

            for column in columns:
                column_name = clean_text(
                    column.get(
                        "Field"
                    )
                )

                raw_type = clean_text(
                    column.get(
                        "Type"
                    )
                )

                data_type = raw_type.split(
                    "(",
                    1,
                )[0].lower()

                columns_seen += 1

                if data_type in NUMERIC_TYPES:
                    numeric_columns += 1

                if (
                    not is_excluded
                    and data_type in NUMERIC_TYPES
                    and looks_like_item_column(
                        column_name
                    )
                ):
                    item_columns += 1

                    candidate_columns.append(
                        (
                            table_name,
                            column_name,
                            data_type,
                        )
                    )

            visible_rows.append(
                {
                    "table_name":
                        table_name,

                    "excluded":
                        is_excluded,

                    "columns_seen":
                        columns_seen,

                    "numeric_columns":
                        numeric_columns,

                    "candidate_item_columns":
                        item_columns,

                    "show_columns_error":
                        show_columns_error,
                }
            )

        visible_frame = pd.DataFrame(
            visible_rows
        )

        visible_frame.to_csv(
            VISIBLE_FILE,
            index=False,
        )

        # If visibility is too restricted, return a diagnostic summary rather
        # than pretending the database has no acquisition tables.
        if not candidate_columns:
            summary = {
                "status":
                    "NEEDS_DB_READ_ACCESS",

                "target_items":
                    2749,

                "db_host":
                    str(
                        cfg[
                            "host"
                        ]
                    ),

                "db_user":
                    str(
                        cfg[
                            "user"
                        ]
                    ),

                "visible_tables":
                    int(
                        len(
                            visible_frame
                        )
                    ),

                "excluded_visible_tables":
                    int(
                        visible_frame[
                            "excluded"
                        ].sum()
                    )
                    if not visible_frame.empty
                    else 0,

                "nonexcluded_visible_tables":
                    int(
                        (
                            visible_frame[
                                "excluded"
                            ]
                            == 0
                        ).sum()
                    )
                    if not visible_frame.empty
                    else 0,

                "candidate_item_columns":
                    0,

                "message":
                    (
                        "The configured database account can see no eligible "
                        "item-source columns after known semantic/runtime tables "
                        "are excluded. This usually means the account has "
                        "restricted SELECT visibility rather than the database "
                        "having no source tables."
                    ),

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
                " Unknown DB Item-Source Census"
            )
            print(
                "======================================"
            )
            print(
                "Status: NEEDS_DB_READ_ACCESS"
            )
            print(
                f"Visible tables:                  "
                f"{summary['visible_tables']:>6}"
            )
            print(
                f"Excluded visible tables:         "
                f"{summary['excluded_visible_tables']:>6}"
            )
            print(
                f"Non-excluded visible tables:     "
                f"{summary['nonexcluded_visible_tables']:>6}"
            )
            print(
                "Candidate item columns:                0"
            )
            print()
            print(
                f"Visible-table report: {VISIBLE_FILE}"
            )
            print(
                f"Summary:              {SUMMARY_FILE}"
            )

            return 2

        table_rows: list[
            dict[
                str,
                Any,
            ]
        ] = []

        for (
            table_name,
            column_name,
            data_type,
        ) in candidate_columns:
            query = (
                f"SELECT DISTINCT `{column_name}` AS itemid "
                f"FROM `{table_name}` "
                f"WHERE `{column_name}` IS NOT NULL "
                f"AND `{column_name}` > 0"
            )

            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        query
                    )

                    values = cursor.fetchall()

            except Exception as exc:
                table_rows.append(
                    {
                        "table_name":
                            table_name,

                        "column_name":
                            column_name,

                        "data_type":
                            data_type,

                        "distinct_positive_values":
                            0,

                        "target_matches":
                            0,

                        "scan_status":
                            "ERROR",

                        "error":
                            str(
                                exc
                            ),
                    }
                )

                continue

            distinct_values = {
                as_int(
                    row.get(
                        "itemid"
                    )
                )
                for row in values
                if as_int(
                    row.get(
                        "itemid"
                    )
                ) > 0
            }

            matches = sorted(
                distinct_values
                & target_ids
            )

            table_rows.append(
                {
                    "table_name":
                        table_name,

                    "column_name":
                        column_name,

                    "data_type":
                        data_type,

                    "distinct_positive_values":
                        len(
                            distinct_values
                        ),

                    "target_matches":
                        len(
                            matches
                        ),

                    "scan_status":
                        "PASS",

                    "error":
                        "",
                }
            )

            for itemid in matches:
                hits.append(
                    {
                        "itemid":
                            itemid,

                        "name":
                            name_by_id.get(
                                itemid,
                                "",
                            ),

                        "table_name":
                            table_name,

                        "column_name":
                            column_name,

                        "evidence_type":
                            "DB_ITEM_REFERENCE",
                    }
                )

    finally:
        connection.close()

    table_frame = pd.DataFrame(
        table_rows
    ).sort_values(
        by=[
            "target_matches",
            "table_name",
            "column_name",
        ],
        ascending=[
            False,
            True,
            True,
        ],
        kind="stable",
    )

    table_frame.to_csv(
        TABLE_FILE,
        index=False,
    )

    hits_frame = pd.DataFrame(
        hits
    )

    if hits_frame.empty:
        hits_frame = pd.DataFrame(
            columns=[
                "itemid",
                "name",
                "table_name",
                "column_name",
                "evidence_type",
            ]
        )

    hit_groups = {
        int(
            itemid
        ):
            group.copy()
        for itemid, group
        in hits_frame.groupby(
            "itemid"
        )
    } if not hits_frame.empty else {}

    rows: list[
        dict[
            str,
            Any,
        ]
    ] = []

    for _, row in target.iterrows():
        itemid = as_int(
            row.get(
                "itemid"
            )
        )

        group = hit_groups.get(
            itemid
        )

        if group is None:
            tables: list[str] = []
            columns: list[str] = []
        else:
            tables = sorted(
                set(
                    group[
                        "table_name"
                    ].map(
                        clean_text
                    )
                )
            )

            columns = sorted(
                {
                    f"{clean_text(t)}.{clean_text(c)}"
                    for t, c
                    in zip(
                        group[
                            "table_name"
                        ],
                        group[
                            "column_name"
                        ],
                    )
                }
            )

        rows.append(
            {
                "itemid":
                    itemid,

                "name":
                    name_by_id.get(
                        itemid,
                        "",
                    ),

                "item_type":
                    as_int(
                        row.get(
                            "item_type"
                        )
                    ),

                "ah_category":
                    as_int(
                        row.get(
                            "ah_category"
                        )
                    ),

                "db_source_table_count":
                    len(
                        tables
                    ),

                "db_source_tables":
                    "|".join(
                        tables
                    ),

                "db_source_columns":
                    "|".join(
                        columns
                    ),

                "recommended_next_step":
                    (
                        "DB_REFERENCE_CONTEXT_CLASSIFICATION"
                        if tables
                        else
                        "NO_DB_ITEM_REFERENCE_FOUND"
                    ),

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

    matching_tables = table_frame[
        table_frame[
            "target_matches"
        ] > 0
    ]

    summary = {
        "status":
            "PASS",

        "target_items":
            2749,

        "visible_tables":
            int(
                len(
                    visible_rows
                )
            ),

        "candidate_item_columns_scanned":
            int(
                len(
                    table_frame
                )
            ),

        "matching_table_columns":
            int(
                len(
                    matching_tables
                )
            ),

        "items_with_db_reference":
            int(
                (
                    output[
                        "db_source_table_count"
                    ]
                    > 0
                ).sum()
            ),

        "items_without_db_reference":
            int(
                (
                    output[
                        "db_source_table_count"
                    ]
                    == 0
                ).sum()
            ),

        "top_matching_table_columns":
            [
                {
                    "table_name":
                        clean_text(
                            entry[
                                "table_name"
                            ]
                        ),

                    "column_name":
                        clean_text(
                            entry[
                                "column_name"
                            ]
                        ),

                    "target_matches":
                        as_int(
                            entry[
                                "target_matches"
                            ]
                        ),
                }
                for _, entry
                in matching_tables.head(
                    30
                ).iterrows()
            ],

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
        " Unknown DB Item-Source Census"
    )
    print(
        "======================================"
    )
    print(
        f"Visible tables:                 "
        f"{summary['visible_tables']:>6}"
    )
    print(
        f"Candidate item columns scanned: "
        f"{summary['candidate_item_columns_scanned']:>6}"
    )
    print(
        f"Matching table columns:         "
        f"{summary['matching_table_columns']:>6}"
    )
    print(
        f"Items with DB reference:        "
        f"{summary['items_with_db_reference']:>6}"
    )
    print(
        f"Items without DB reference:     "
        f"{summary['items_without_db_reference']:>6}"
    )
    print()
    print(
        "Top matching DB columns:"
    )

    for entry in summary[
        "top_matching_table_columns"
    ]:
        print(
            f"  {entry['table_name']}.{entry['column_name']:<28} "
            f"{entry['target_matches']:>6}"
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
        f"Census:  {OUTPUT_FILE}"
    )
    print(
        f"Tables:  {TABLE_FILE}"
    )
    print(
        f"Visible: {VISIBLE_FILE}"
    )
    print(
        f"Summary: {SUMMARY_FILE}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
