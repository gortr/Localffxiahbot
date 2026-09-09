from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

UNIFIED_FILE = ROOT / "economy" / "generated" / "unified-seller-buyer-policy.csv"
CANONICAL_FILE = ROOT / "economy" / "generated" / "canonical-market-policy.csv"
CANDIDATE_FILE = ROOT / "economy" / "generated" / "candidate-market-policy.csv"

OUTPUT_FILE = ROOT / "economy" / "reports" / "economy-maturity-coverage-audit.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "economy-maturity-coverage-summary.json"


EXPECTED_TOTAL = 23534
EXPECTED_LIVE_SELL = 167
EXPECTED_LIVE_BUY = 159


class MaturityCoverageError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def as_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def load_unique(path: Path, label: str) -> pd.DataFrame:
    if not path.exists():
        raise MaturityCoverageError(
            f"Missing {label}: {path}"
        )

    frame = pd.read_csv(
        path,
        low_memory=False,
    )

    if "itemid" not in frame.columns:
        raise MaturityCoverageError(
            f"{label} has no itemid column."
        )

    frame = frame.copy()
    frame["itemid"] = frame["itemid"].map(as_int)

    if frame["itemid"].duplicated().any():
        raise MaturityCoverageError(
            f"{label} contains duplicate itemids."
        )

    return frame


def infer_audit_bucket(row: pd.Series) -> str:
    seller = as_int(row.get("seller_capable"))
    buyer = as_int(row.get("buyer_capable"))
    maturity = clean_text(row.get("maturity"))
    live_seller = as_int(row.get("live_seller_baseline"))
    live_buyer = as_int(row.get("live_buyer_baseline"))

    if live_seller or live_buyer:
        if maturity:
            return "LIVE_WITH_MATURITY"
        return "LIVE_MISSING_MATURITY"

    if not seller and not buyer:
        return "NOT_CAPABLE"

    if maturity:
        return "FUTURE_CAPABLE_WITH_MATURITY"

    if seller and buyer:
        return "FUTURE_BOTH_MISSING_MATURITY"
    if seller:
        return "FUTURE_SELLER_MISSING_MATURITY"
    return "FUTURE_BUYER_MISSING_MATURITY"


def suggested_maturity_action(row: pd.Series) -> str:
    bucket = clean_text(row.get("audit_bucket"))
    market_class = clean_text(row.get("canonical_class")).upper()
    current_seed_sell = as_int(row.get("current_seed_sell_allowed"))
    current_seed_buy = as_int(row.get("current_seed_buy_allowed"))
    live = (
        as_int(row.get("live_seller_baseline"))
        or as_int(row.get("live_buyer_baseline"))
    )

    if live or current_seed_sell or current_seed_buy:
        return "ASSIGN_SEED_BASELINE"

    if bucket == "NOT_CAPABLE":
        return "NO_MATURITY_REQUIRED_YET"

    if market_class == "STAPLE":
        return "DEFAULT_STAGE_REVIEW_STAPLE"
    if market_class == "NORMAL":
        return "DEFAULT_STAGE_REVIEW_NORMAL"
    if market_class == "SCARCE":
        return "DEFAULT_STAGE_REVIEW_SCARCE"

    return "MANUAL_STAGE_RULE_REQUIRED"


def main() -> int:
    unified = load_unique(
        UNIFIED_FILE,
        "unified seller/buyer policy",
    )
    canonical = load_unique(
        CANONICAL_FILE,
        "canonical market policy",
    )
    candidate = load_unique(
        CANDIDATE_FILE,
        "candidate market policy",
    )

    for label, frame in [
        ("unified seller/buyer policy", unified),
        ("canonical market policy", canonical),
        ("candidate market policy", candidate),
    ]:
        if len(frame) != EXPECTED_TOTAL:
            raise MaturityCoverageError(
                f"Expected {EXPECTED_TOTAL} rows in {label}, "
                f"found {len(frame)}"
            )

    canonical_cols = [
        column
        for column in [
            "itemid",
            "applied_overlays",
            "maturity_source",
            "policy_ready",
        ]
        if column in canonical.columns
    ]

    candidate_cols = [
        column
        for column in [
            "itemid",
            "item_type",
            "current_seed_sell_allowed",
            "current_seed_buy_allowed",
            "candidate_reason",
            "candidate_sell_capable",
            "candidate_buy_capable",
            "vendor_item",
            "has_vendor_price_floor",
            "mob_source_class",
            "craft_nq_output",
            "craft_hq_output",
            "restricted_nq_output",
            "restricted_hq_output",
            "fishing",
            "rare",
            "can_equip",
            "stack_size",
            "base_sell",
        ]
        if column in candidate.columns
    ]

    merged = (
        unified.merge(
            canonical[canonical_cols],
            on="itemid",
            how="left",
            validate="one_to_one",
        )
        .merge(
            candidate[candidate_cols],
            on="itemid",
            how="left",
            validate="one_to_one",
        )
    )

    merged["audit_bucket"] = merged.apply(
        infer_audit_bucket,
        axis=1,
    )

    merged["suggested_maturity_action"] = merged.apply(
        suggested_maturity_action,
        axis=1,
    )

    merged["maturity_present"] = merged[
        "maturity"
    ].map(
        lambda value: int(bool(clean_text(value)))
    )

    output_columns = [
        "itemid",
        "name",
        "canonical_class",
        "seller_capable",
        "buyer_capable",
        "live_seller_baseline",
        "live_buyer_baseline",
        "maturity",
        "maturity_present",
        "maturity_source",
        "audit_bucket",
        "suggested_maturity_action",
        "source_policy_family",
        "vendor_item",
        "has_vendor_price_floor",
        "item_type",
        "stack_size",
        "base_sell",
        "applied_overlays",
        "candidate_reason",
        "current_seed_sell_allowed",
        "current_seed_buy_allowed",
        "mob_source_class",
        "craft_nq_output",
        "craft_hq_output",
        "restricted_nq_output",
        "restricted_hq_output",
        "fishing",
        "rare",
        "can_equip",
    ]

    output_columns = [
        column
        for column in output_columns
        if column in merged.columns
    ]

    output = merged[
        output_columns
    ].sort_values(
        by=[
            "audit_bucket",
            "canonical_class",
            "itemid",
        ],
        kind="stable",
    )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    capable = merged[
        (merged["seller_capable"] == 1)
        | (merged["buyer_capable"] == 1)
    ].copy()

    missing = capable[
        capable["maturity_present"] == 0
    ].copy()

    live = merged[
        (merged["live_seller_baseline"] == 1)
        | (merged["live_buyer_baseline"] == 1)
    ].copy()

    live_missing = live[
        live["maturity_present"] == 0
    ].copy()

    def counts(frame: pd.DataFrame, column: str) -> dict[str, int]:
        if column not in frame.columns:
            return {}
        return {
            str(k): int(v)
            for k, v in (
                frame[column]
                .fillna("NONE")
                .replace("", "NONE")
                .value_counts()
                .to_dict()
                .items()
            )
        }

    summary = {
        "status": "PASS",
        "total_items": int(len(merged)),
        "seller_capable": int(
            merged["seller_capable"].sum()
        ),
        "buyer_capable": int(
            merged["buyer_capable"].sum()
        ),
        "capable_union": int(len(capable)),
        "capable_with_maturity": int(
            capable["maturity_present"].sum()
        ),
        "capable_missing_maturity": int(len(missing)),
        "live_union": int(len(live)),
        "live_missing_maturity": int(len(live_missing)),
        "live_seller_baseline": int(
            merged["live_seller_baseline"].sum()
        ),
        "live_buyer_baseline": int(
            merged["live_buyer_baseline"].sum()
        ),
        "missing_by_class": counts(
            missing,
            "canonical_class",
        ),
        "missing_by_item_type": counts(
            missing,
            "item_type",
        ),
        "missing_by_source_policy": counts(
            missing,
            "source_policy_family",
        ),
        "missing_by_overlay": counts(
            missing,
            "applied_overlays",
        ),
        "audit_bucket_counts": counts(
            merged,
            "audit_bucket",
        ),
        "existing_maturity_counts": counts(
            capable[capable["maturity_present"] == 1],
            "maturity",
        ),
        "suggested_action_counts": counts(
            missing,
            "suggested_maturity_action",
        ),
        "activation_ready": 0,
        "auto_live_promotions": 0,
    }

    if summary["live_seller_baseline"] != EXPECTED_LIVE_SELL:
        raise MaturityCoverageError(
            "Live seller baseline changed."
        )

    if summary["live_buyer_baseline"] != EXPECTED_LIVE_BUY:
        raise MaturityCoverageError(
            "Live buyer baseline changed."
        )

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
    print("=" * 54)
    print(" Economy Maturity Coverage Audit")
    print("=" * 54)
    print(
        f"Total items:                         "
        f"{summary['total_items']:>6}"
    )
    print(
        f"Seller capable:                      "
        f"{summary['seller_capable']:>6}"
    )
    print(
        f"Buyer capable:                       "
        f"{summary['buyer_capable']:>6}"
    )
    print(
        f"Capable union:                       "
        f"{summary['capable_union']:>6}"
    )
    print(
        f"Capable with maturity:               "
        f"{summary['capable_with_maturity']:>6}"
    )
    print(
        f"Capable missing maturity:            "
        f"{summary['capable_missing_maturity']:>6}"
    )
    print(
        f"Live union:                          "
        f"{summary['live_union']:>6}"
    )
    print(
        f"Live missing maturity:               "
        f"{summary['live_missing_maturity']:>6}"
    )
    print()
    print("Missing maturity by class:")
    for label, count in sorted(
        summary["missing_by_class"].items(),
        key=lambda pair: (-pair[1], pair[0]),
    ):
        print(
            f"  {label:<20} "
            f"{count:>6}"
        )
    print()
    print("Existing maturity stages:")
    for label, count in sorted(
        summary["existing_maturity_counts"].items(),
        key=lambda pair: (-pair[1], pair[0]),
    ):
        print(
            f"  {label:<20} "
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
    raise SystemExit(
        main()
    )
