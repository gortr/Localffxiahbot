from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

TRUE_UNKNOWN_FILE = ROOT / "economy" / "reports" / "true-unknown-acquisition-audit.csv"
KNOWN_RESOLUTION_FILE = ROOT / "economy" / "reports" / "known-provenance-acquisition-resolution.csv"

OUTPUT_FILE = ROOT / "economy" / "reports" / "acquisition-gap-cohort-audit.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "acquisition-gap-cohort-audit-summary.json"


class AcquisitionGapCohortError(RuntimeError):
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


def main() -> int:
    for path in [
        TRUE_UNKNOWN_FILE,
        KNOWN_RESOLUTION_FILE,
    ]:
        if not path.exists():
            raise AcquisitionGapCohortError(
                f"Missing required input: {path}"
            )

    true_unknown = pd.read_csv(
        TRUE_UNKNOWN_FILE,
        low_memory=False,
    )

    known = pd.read_csv(
        KNOWN_RESOLUTION_FILE,
        low_memory=False,
    )

    usage_only = known[
        known["source_status"]
        == "UNKNOWN_ACQUISITION"
    ].copy()

    if len(true_unknown) != 2846:
        raise AcquisitionGapCohortError(
            f"Expected 2846 true unknown rows, found {len(true_unknown)}"
        )

    rows: list[dict[str, Any]] = []

    for _, row in true_unknown.iterrows():
        rows.append(
            {
                "itemid": as_int(row.get("itemid")),
                "name": clean_text(row.get("name")),
                "item_type": as_int(row.get("item_type")),
                "ah_category": as_int(row.get("ah_category")),
                "stack_size": as_int(row.get("stack_size")),
                "semantic_families": clean_text(
                    row.get("semantic_families")
                ),
                "missing_item_enum_constant": as_int(
                    row.get("missing_item_enum_constant")
                ),
                "rare": as_int(row.get("rare")),
                "can_equip": as_int(row.get("can_equip")),
                "base_sell": as_int(row.get("base_sell")),
                "gap_origin": "TRUE_UNKNOWN_ACQUISITION",
                "reference_state": clean_text(
                    row.get("acquisition_gap")
                ),
            }
        )

    for _, row in usage_only.iterrows():
        rows.append(
            {
                "itemid": as_int(row.get("itemid")),
                "name": clean_text(row.get("name")),
                "item_type": as_int(row.get("item_type")),
                "ah_category": as_int(row.get("ah_category")),
                "stack_size": as_int(row.get("stack_size")),
                "semantic_families": "",
                "missing_item_enum_constant": 0,
                "rare": 0,
                "can_equip": 0,
                "base_sell": 0,
                "gap_origin": "USAGE_ONLY_PROVENANCE",
                "reference_state": "SOURCE_NOT_ESTABLISHED",
            }
        )

    output = pd.DataFrame(rows).drop_duplicates(
        subset=["itemid"],
        keep="first",
    )

    output["id_band"] = (
        output["itemid"].astype(int) // 1000 * 1000
    ).map(
        lambda value: f"{value:05d}-{value + 999:05d}"
    )

    def cohort(row: pd.Series) -> str:
        semantic = clean_text(
            row.get("semantic_families")
        )
        item_type = as_int(
            row.get("item_type")
        )

        if "item_equipment" in semantic:
            return "EQUIPMENT"
        if "item_weapon" in semantic:
            return "WEAPON"
        if "item_usable" in semantic:
            return "USABLE"
        if "item_furnishing" in semantic:
            return "FURNISHING"
        if item_type == 1:
            return "GOODS_TYPE1"
        if item_type == 5:
            return "ITEM_TYPE5"
        if item_type == 6:
            return "EQUIPMENT_TYPE6_UNMAPPED"
        if item_type == 7:
            return "WEAPON_TYPE7_UNMAPPED"
        return f"OTHER_TYPE_{item_type}"

    output["cohort"] = output.apply(
        cohort,
        axis=1,
    )

    output = output.sort_values(
        by=[
            "cohort",
            "ah_category",
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
        "total_acquisition_gaps": int(len(output)),
        "gap_origin_counts": {
            str(k): int(v)
            for k, v in (
                output["gap_origin"]
                .value_counts()
                .to_dict()
                .items()
            )
        },
        "cohort_counts": {
            str(k): int(v)
            for k, v in (
                output["cohort"]
                .value_counts()
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
    print("=" * 48)
    print(" Acquisition-Gap Cohort Audit")
    print("=" * 48)
    print(
        f"Total acquisition gaps:          "
        f"{summary['total_acquisition_gaps']:>6}"
    )
    print(
        f"AH-listed:                       "
        f"{summary['ah_listed']:>6}"
    )
    print(
        f"Not AH-listed:                   "
        f"{summary['not_ah_listed']:>6}"
    )
    print()
    print("Cohorts:")
    for label, count in sorted(
        summary["cohort_counts"].items(),
        key=lambda pair: (-pair[1], pair[0]),
    ):
        print(
            f"  {label:<30} "
            f"{count:>6}"
        )
    print()
    print("Gap origins:")
    for label, count in sorted(
        summary["gap_origin_counts"].items(),
        key=lambda pair: (-pair[1], pair[0]),
    ):
        print(
            f"  {label:<30} "
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
