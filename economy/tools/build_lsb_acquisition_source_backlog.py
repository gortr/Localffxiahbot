from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

COHORT_FILE = ROOT / "economy" / "reports" / "acquisition-gap-cohort-audit.csv"

OUTPUT_FILE = ROOT / "economy" / "reports" / "lsb-acquisition-source-backlog.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "lsb-acquisition-source-backlog-summary.json"


class BacklogError(RuntimeError):
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


def canonical_family(item_type: int, current: str) -> str:
    if item_type == 6:
        return "EQUIPMENT"
    if item_type == 7:
        return "WEAPON"
    if item_type == 1:
        return "GOODS"
    if item_type == 5:
        return "ITEM_TYPE5"
    if current == "USABLE":
        return "USABLE"
    if current == "FURNISHING":
        return "FURNISHING"
    return current or f"TYPE_{item_type}"


def main() -> int:
    if not COHORT_FILE.exists():
        raise BacklogError(
            f"Missing cohort audit: {COHORT_FILE}"
        )

    frame = pd.read_csv(
        COHORT_FILE,
        low_memory=False,
    )

    if len(frame) != 3076:
        raise BacklogError(
            f"Expected 3076 acquisition gaps, found {len(frame)}"
        )

    output = frame.copy()

    output["source_backlog_family"] = [
        canonical_family(
            as_int(item_type),
            clean_text(cohort),
        )
        for item_type, cohort
        in zip(
            output["item_type"],
            output["cohort"],
        )
    ]

    output["backlog_priority"] = output.apply(
        lambda row:
            1
            if (
                as_int(row.get("ah_category")) > 0
                and clean_text(
                    row.get("reference_state")
                )
                in {
                    "AH_LISTED_NO_SOURCE_ANYWHERE",
                    "SOURCE_NOT_ESTABLISHED",
                }
            )
            else 2
            if as_int(row.get("ah_category")) > 0
            else 3,
        axis=1,
    )

    output["ahbot_policy"] = "KEEP_PROTECTED"
    output["activation_ready"] = 0
    output["auto_live_promotion"] = 0

    output = output.sort_values(
        by=[
            "backlog_priority",
            "source_backlog_family",
            "id_band",
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
        "total_backlog_items": int(len(output)),
        "family_counts": {
            str(k): int(v)
            for k, v in (
                output["source_backlog_family"]
                .value_counts()
                .to_dict()
                .items()
            )
        },
        "priority_counts": {
            str(k): int(v)
            for k, v in (
                output["backlog_priority"]
                .value_counts()
                .sort_index()
                .to_dict()
                .items()
            )
        },
        "ah_listed": int(
            (output["ah_category"] > 0).sum()
        ),
        "not_ah_listed": int(
            (output["ah_category"] == 0).sum()
        ),
        "top_id_bands": {
            str(k): int(v)
            for k, v in (
                output["id_band"]
                .value_counts()
                .head(40)
                .to_dict()
                .items()
            )
        },
        "ahbot_policy": "KEEP_PROTECTED",
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
    print("=" * 52)
    print(" LSB Acquisition-Source Backlog")
    print("=" * 52)
    print(
        f"Backlog items:                       "
        f"{summary['total_backlog_items']:>6}"
    )
    print(
        f"AH-listed:                           "
        f"{summary['ah_listed']:>6}"
    )
    print(
        f"Not AH-listed:                       "
        f"{summary['not_ah_listed']:>6}"
    )
    print()
    print("Families:")
    for label, count in sorted(
        summary["family_counts"].items(),
        key=lambda pair: (-pair[1], pair[0]),
    ):
        print(
            f"  {label:<24} "
            f"{count:>6}"
        )
    print()
    print("AHBot policy: KEEP_PROTECTED")
    print("Activation ready: 0")
    print("Auto live promotions: 0")
    print()
    print(f"Backlog: {OUTPUT_FILE}")
    print(f"Summary: {SUMMARY_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
