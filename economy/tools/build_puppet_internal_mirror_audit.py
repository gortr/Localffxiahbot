from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pymysql
import yaml


ROOT = Path.home() / "ffxiahbot"

CONFIG_FILE = ROOT / "bin" / "config.yaml"
AUDIT_FILE = ROOT / "economy" / "reports" / "candidate-market-audit.csv"

OUTPUT_FILE = ROOT / "economy" / "reports" / "puppet-internal-mirror-audit.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "puppet-internal-mirror-audit-summary.json"


class PuppetMirrorAuditError(RuntimeError):
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
        raise PuppetMirrorAuditError(
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

        raise PuppetMirrorAuditError(
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


def load_items() -> pd.DataFrame:
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
            itemid,
            name,
            sortname,
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
                query
            )

            rows = cursor.fetchall()

    finally:
        connection.close()

    frame = pd.DataFrame(
        rows
    )

    if frame.empty:
        raise PuppetMirrorAuditError(
            "item_basic returned no rows."
        )

    return frame


def main() -> int:
    if not AUDIT_FILE.exists():
        raise PuppetMirrorAuditError(
            f"Missing audit file: {AUDIT_FILE}"
        )

    audit = pd.read_csv(
        AUDIT_FILE,
        low_memory=False,
    )

    puppet = audit[
        audit[
            "audit_bucket"
        ]
        == "PUPPET_POLICY"
    ][
        [
            "itemid",
            "name",
        ]
    ].copy()

    if len(
        puppet
    ) != 127:
        raise PuppetMirrorAuditError(
            "Expected 127 PUPPET_POLICY items, "
            f"found {len(puppet)}"
        )

    items = load_items()

    by_name: dict[
        str,
        pd.DataFrame,
    ] = {
        clean_text(name):
            group.copy()
        for name, group
        in items.groupby(
            "name",
            dropna=False,
        )
    }

    rows: list[dict[str, Any]] = []

    for _, target in puppet.iterrows():
        itemid = as_int(
            target.get(
                "itemid"
            )
        )

        target_db = items[
            items[
                "itemid"
            ] == itemid
        ]

        if len(
            target_db
        ) != 1:
            raise PuppetMirrorAuditError(
                f"Could not uniquely load target item {itemid}"
            )

        target_row = target_db.iloc[
            0
        ]

        name = clean_text(
            target_row.get(
                "name"
            )
        )

        same_name = by_name.get(
            name,
            pd.DataFrame(),
        )

        if same_name.empty:
            candidates = pd.DataFrame(
                columns=items.columns
            )
        else:
            candidates = same_name[
                same_name[
                    "itemid"
                ] != itemid
            ].copy()

        preferred = candidates[
            (
                candidates[
                    "type"
                ] != 4
            )
            &
            (
                candidates[
                    "itemid"
                ] < itemid
            )
        ].copy()

        if not preferred.empty:
            preferred = preferred.sort_values(
                by=[
                    "itemid"
                ]
            )

            mirror = preferred.iloc[
                0
            ]

        elif not candidates.empty:
            candidates = candidates.sort_values(
                by=[
                    "itemid"
                ]
            )

            mirror = candidates.iloc[
                0
            ]

        else:
            mirror = None

        has_mirror = int(
            mirror is not None
        )

        strong_internal_mirror = 0

        if mirror is not None:
            strong_internal_mirror = int(
                as_int(
                    target_row.get(
                        "type"
                    )
                )
                == 4
                and as_int(
                    mirror.get(
                        "type"
                    )
                )
                != 4
                and as_int(
                    mirror.get(
                        "itemid"
                    )
                )
                < itemid
            )

        rows.append(
            {
                "internal_itemid":
                    itemid,

                "name":
                    name,

                "internal_type":
                    as_int(
                        target_row.get(
                            "type"
                        )
                    ),

                "internal_stack_size":
                    as_int(
                        target_row.get(
                            "stackSize"
                        )
                    ),

                "internal_flags":
                    as_int(
                        target_row.get(
                            "flags"
                        )
                    ),

                "internal_ah_category":
                    as_int(
                        target_row.get(
                            "aH"
                        )
                    ),

                "internal_base_sell":
                    as_int(
                        target_row.get(
                            "BaseSell"
                        )
                    ),

                "same_name_other_records":
                    int(
                        len(
                            candidates
                        )
                    ),

                "mirror_itemid":
                    (
                        as_int(
                            mirror.get(
                                "itemid"
                            )
                        )
                        if mirror is not None
                        else 0
                    ),

                "mirror_type":
                    (
                        as_int(
                            mirror.get(
                                "type"
                            )
                        )
                        if mirror is not None
                        else 0
                    ),

                "mirror_stack_size":
                    (
                        as_int(
                            mirror.get(
                                "stackSize"
                            )
                        )
                        if mirror is not None
                        else 0
                    ),

                "mirror_flags":
                    (
                        as_int(
                            mirror.get(
                                "flags"
                            )
                        )
                        if mirror is not None
                        else 0
                    ),

                "mirror_ah_category":
                    (
                        as_int(
                            mirror.get(
                                "aH"
                            )
                        )
                        if mirror is not None
                        else 0
                    ),

                "mirror_base_sell":
                    (
                        as_int(
                            mirror.get(
                                "BaseSell"
                            )
                        )
                        if mirror is not None
                        else 0
                    ),

                "has_same_name_mirror":
                    has_mirror,

                "strong_internal_mirror":
                    strong_internal_mirror,

                "recommended_action":
                    (
                        "BLOCK_INTERNAL_PUPPET_RECORD"
                        if strong_internal_mirror
                        else
                        "MANUAL_REVIEW"
                    ),

                "auto_live_promotion":
                    0,
            }
        )

    output = pd.DataFrame(
        rows
    ).sort_values(
        by=[
            "internal_itemid"
        ],
        kind="stable",
    )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    strong = int(
        output[
            "strong_internal_mirror"
        ].sum()
    )

    mirrored = int(
        output[
            "has_same_name_mirror"
        ].sum()
    )

    review = int(
        (
            output[
                "recommended_action"
            ]
            == "MANUAL_REVIEW"
        ).sum()
    )

    summary = {
        "status":
            "PASS",

        "puppet_policy_items":
            int(
                len(
                    output
                )
            ),

        "items_with_same_name_mirror":
            mirrored,

        "strong_internal_mirrors":
            strong,

        "manual_review":
            review,

        "internal_itemid_min":
            int(
                output[
                    "internal_itemid"
                ].min()
            ),

        "internal_itemid_max":
            int(
                output[
                    "internal_itemid"
                ].max()
            ),

        "internal_type_counts":
            {
                str(k):
                    int(v)
                for k, v
                in output[
                    "internal_type"
                ]
                .value_counts()
                .to_dict()
                .items()
            },

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
        " Puppet Internal-Mirror Audit"
    )
    print(
        "======================================"
    )

    print(
        f"Puppet-policy items:          "
        f"{summary['puppet_policy_items']:>6}"
    )

    print(
        f"Same-name mirrors found:      "
        f"{summary['items_with_same_name_mirror']:>6}"
    )

    print(
        f"Strong internal mirrors:      "
        f"{summary['strong_internal_mirrors']:>6}"
    )

    print(
        f"Manual review:                "
        f"{summary['manual_review']:>6}"
    )

    print(
        f"Internal item ID range:       "
        f"{summary['internal_itemid_min']} - "
        f"{summary['internal_itemid_max']}"
    )

    print()
    print(
        "Internal type counts:"
    )

    for item_type, count in sorted(
        summary[
            "internal_type_counts"
        ].items()
    ):
        print(
            f"  type {item_type:<5} "
            f"{count:>6}"
        )

    print()
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
    raise SystemExit(
        main()
    )
