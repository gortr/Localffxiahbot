from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

LEDGER_FILE = ROOT / "economy" / "reports" / "unknown-provenance-resolution-ledger-v2.csv"

OUTPUT_FILE = ROOT / "economy" / "reports" / "unknown-residual-audit.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "unknown-residual-audit-summary.json"


class ResidualAuditError(RuntimeError):
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
    if not LEDGER_FILE.exists():
        raise ResidualAuditError(
            f"Missing ledger v2: {LEDGER_FILE}"
        )

    ledger = pd.read_csv(
        LEDGER_FILE,
        low_memory=False,
    )

    residual = ledger[
        ledger["source_status"]
        .str.startswith("UNRESOLVED")
    ].copy()

    rows: list[dict[str, Any]] = []

    for _, row in residual.iterrows():
        ah_category = as_int(row.get("ah_category"))
        script_refs = as_int(
            row.get("script_reference_count")
        )
        repo_refs = as_int(
            row.get("repository_reference_count")
        )
        source_types = clean_text(
            row.get("source_types")
        )

        if (
            ah_category > 0
            and script_refs == 0
            and repo_refs == 0
        ):
            residual_bucket = "AH_LISTED_ZERO_SOURCE_EVIDENCE"
            priority = 1
        elif (
            ah_category > 0
            and (
                script_refs > 0
                or repo_refs > 0
                or source_types
            )
        ):
            residual_bucket = "AH_LISTED_REFERENCE_ONLY"
            priority = 2
        elif (
            ah_category == 0
            and clean_text(
                row.get("source_status")
            )
            == "UNRESOLVED_INTERNAL"
        ):
            residual_bucket = "INTERNAL_UNUSED_CANDIDATE"
            priority = 2
        elif (
            ah_category == 0
            and script_refs == 0
            and repo_refs == 0
        ):
            residual_bucket = "NON_AH_ZERO_SOURCE_EVIDENCE"
            priority = 3
        else:
            residual_bucket = "NON_AH_REFERENCE_ONLY"
            priority = 3

        rows.append(
            {
                "itemid": as_int(row.get("itemid")),
                "name": clean_text(row.get("name")),
                "item_type": as_int(row.get("item_type")),
                "ah_category": ah_category,
                "stack_size": as_int(row.get("stack_size")),
                "candidate_class": clean_text(
                    row.get("candidate_class")
                ),
                "candidate_reason": clean_text(
                    row.get("candidate_reason")
                ),
                "semantic_families": clean_text(
                    row.get("semantic_families")
                ),
                "path_families": clean_text(
                    row.get("path_families")
                ),
                "source_types": source_types,
                "script_reference_count": script_refs,
                "repository_reference_count": repo_refs,
                "repository_source_families": clean_text(
                    row.get("repository_source_families")
                ),
                "repository_confidence": clean_text(
                    row.get("repository_confidence")
                ),
                "prior_resolution": clean_text(
                    row.get("prior_resolution")
                ),
                "current_resolution": clean_text(
                    row.get("resolution")
                ),
                "residual_bucket": residual_bucket,
                "audit_priority": priority,
                "activation_ready": 0,
                "auto_live_promotion": 0,
            }
        )

    output = pd.DataFrame(rows).sort_values(
        by=[
            "audit_priority",
            "residual_bucket",
            "item_type",
            "itemid",
        ],
        kind="stable",
    )

    output.to_csv(OUTPUT_FILE, index=False)

    summary = {
        "status": "PASS",
        "total_residual_items": int(len(output)),
        "residual_bucket_counts": {
            str(k): int(v)
            for k, v in (
                output["residual_bucket"]
                .value_counts()
                .to_dict()
                .items()
            )
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
        "ah_listed": int(
            (output["ah_category"] > 0).sum()
        ),
        "not_ah_listed": int(
            (output["ah_category"] == 0).sum()
        ),
        "top_candidate_reasons": {
            str(k): int(v)
            for k, v in (
                output["candidate_reason"]
                .replace("", "NONE")
                .value_counts()
                .head(30)
                .to_dict()
                .items()
            )
        },
        "activation_ready": 0,
        "auto_live_promotions": 0,
    }

    SUMMARY_FILE.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 44)
    print(" Unknown Residual Audit")
    print("=" * 44)
    print(
        f"Residual items:                 "
        f"{summary['total_residual_items']:>6}"
    )
    print(
        f"AH-listed:                      "
        f"{summary['ah_listed']:>6}"
    )
    print(
        f"Not AH-listed:                  "
        f"{summary['not_ah_listed']:>6}"
    )
    print()
    print("Residual buckets:")
    for label, count in sorted(
        summary["residual_bucket_counts"].items(),
        key=lambda pair: (-pair[1], pair[0]),
    ):
        print(
            f"  {label:<34} "
            f"{count:>6}"
        )
    print()
    print("Item types:")
    for label, count in sorted(
        summary["item_type_counts"].items(),
        key=lambda pair: int(pair[0]),
    ):
        print(
            f"  type {label:<4} "
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
