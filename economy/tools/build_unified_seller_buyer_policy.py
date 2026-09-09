from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

CANONICAL_FILE = ROOT / "economy" / "generated" / "canonical-market-policy.csv"
CANDIDATE_FILE = ROOT / "economy" / "generated" / "candidate-market-policy.csv"
VENDOR_POLICY_FILE = ROOT / "economy" / "generated" / "vendor-policy.csv"

OUTPUT_FILE = ROOT / "economy" / "generated" / "unified-seller-buyer-policy.csv"
AUDIT_FILE = ROOT / "economy" / "reports" / "unified-seller-buyer-policy-audit.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "unified-seller-buyer-policy-summary.json"


EXPECTED_TOTAL = 23534
EXPECTED_LIVE_SELL = 167
EXPECTED_LIVE_BUY = 159


class UnifiedPolicyError(RuntimeError):
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


def as_bool_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0

    if isinstance(value, bool):
        return int(value)

    text = str(value).strip().lower()

    if text in {
        "1", "true", "yes", "y", "on",
        "allowed", "eligible", "enabled",
    }:
        return 1

    if text in {
        "0", "false", "no", "n", "off",
        "blocked", "ineligible", "disabled", "",
    }:
        return 0

    try:
        return int(float(text) != 0)
    except ValueError:
        return 0


def normalize(value: str) -> str:
    return re.sub(
        r"[^a-z0-9]",
        "",
        value.lower(),
    )


def find_column(
    frame: pd.DataFrame,
    preferred: list[str],
    contains: list[str] | None = None,
) -> str | None:
    normalized = {
        normalize(column): column
        for column in frame.columns
    }

    for candidate in preferred:
        found = normalized.get(
            normalize(candidate)
        )
        if found:
            return found

    if contains:
        for column in frame.columns:
            norm = normalize(column)
            if all(
                normalize(part) in norm
                for part in contains
            ):
                return column

    return None


def require_column(
    frame: pd.DataFrame,
    preferred: list[str],
    label: str,
    contains: list[str] | None = None,
) -> str:
    column = find_column(
        frame,
        preferred,
        contains,
    )

    if column is None:
        raise UnifiedPolicyError(
            f"Missing {label}. Columns: {list(frame.columns)}"
        )

    return column


def load_unique(
    path: Path,
    label: str,
) -> pd.DataFrame:
    if not path.exists():
        raise UnifiedPolicyError(
            f"Missing {label}: {path}"
        )

    frame = pd.read_csv(
        path,
        low_memory=False,
    )

    itemid_col = require_column(
        frame,
        ["itemid", "item_id", "id"],
        f"{label} itemid",
    )

    if itemid_col != "itemid":
        frame = frame.rename(
            columns={
                itemid_col: "itemid"
            }
        )

    frame["itemid"] = frame[
        "itemid"
    ].map(
        as_int
    )

    if frame["itemid"].duplicated().any():
        duplicates = (
            frame.loc[
                frame["itemid"].duplicated(
                    keep=False
                ),
                "itemid",
            ]
            .head(20)
            .tolist()
        )

        raise UnifiedPolicyError(
            f"{label} contains duplicate itemids: "
            f"{duplicates}"
        )

    return frame


def append_blocker(
    blockers: list[str],
    condition: bool,
    reason: str,
) -> None:
    if condition:
        blockers.append(
            reason
        )


def main() -> int:
    canonical = load_unique(
        CANONICAL_FILE,
        "canonical market policy",
    )

    candidate = load_unique(
        CANDIDATE_FILE,
        "candidate market policy",
    )

    vendor_policy = load_unique(
        VENDOR_POLICY_FILE,
        "vendor policy",
    )

    if len(canonical) != EXPECTED_TOTAL:
        raise UnifiedPolicyError(
            f"Expected {EXPECTED_TOTAL} canonical rows, "
            f"found {len(canonical)}"
        )

    if len(candidate) != EXPECTED_TOTAL:
        raise UnifiedPolicyError(
            f"Expected {EXPECTED_TOTAL} candidate rows, "
            f"found {len(candidate)}"
        )

    canonical_ids = set(
        canonical["itemid"].astype(int)
    )

    candidate_ids = set(
        candidate["itemid"].astype(int)
    )

    if canonical_ids != candidate_ids:
        raise UnifiedPolicyError(
            "Canonical and candidate item sets differ."
        )

    actual_vendor_col = require_column(
        vendor_policy,
        ["actual_vendor_source"],
        "vendor policy actual_vendor_source",
    )

    buyer_vendor_block_col = require_column(
        vendor_policy,
        ["buyer_vendor_block"],
        "vendor policy buyer_vendor_block",
    )

    seller_vendor_floor_required_col = require_column(
        vendor_policy,
        ["seller_vendor_floor_required"],
        "vendor policy seller_vendor_floor_required",
    )

    seller_vendor_ready_col = require_column(
        vendor_policy,
        ["seller_vendor_ready"],
        "vendor policy seller_vendor_ready",
    )

    hard_floor_col = require_column(
        vendor_policy,
        ["has_hard_floor"],
        "vendor policy hard floor",
    )

    pricing_ready_col = find_column(
        candidate,
        [
            "pricing_ready",
            "pricingready",
        ],
        contains=[
            "pricing",
            "ready",
        ],
    )

    if pricing_ready_col is None:
        raise UnifiedPolicyError(
            "Candidate policy has no pricing-ready column."
        )

    candidate_by_item = {
        int(row["itemid"]):
            row.to_dict()
        for _, row in candidate.iterrows()
    }

    vendor_by_item = {
        int(row["itemid"]):
            row.to_dict()
        for _, row in vendor_policy.iterrows()
    }

    rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []

    seller_block_counts: Counter[str] = Counter()
    buyer_block_counts: Counter[str] = Counter()

    for _, row in canonical.iterrows():
        itemid = as_int(
            row.get("itemid")
        )

        candidate_row = candidate_by_item[
            itemid
        ]

        vendor_row = vendor_by_item.get(
            itemid,
            {},
        )

        market_class = clean_text(
            row.get("canonical_class")
        ).upper()

        source_family = clean_text(
            row.get("source_policy_family")
        )

        maturity = clean_text(
            row.get("maturity")
        )

        canonical_seller = as_bool_int(
            row.get("seller_policy_eligible")
        )

        canonical_buyer = as_bool_int(
            row.get("buyer_policy_eligible")
        )

        live_seller = as_bool_int(
            row.get("live_seller_baseline")
        )

        live_buyer = as_bool_int(
            row.get("live_buyer_baseline")
        )

        # The frozen Phase-0 CSVs are the authoritative live baseline.
        # Canonical seller/buyer eligibility describes future policy
        # capability and therefore must not retroactively invalidate an
        # already-approved baseline row. Baseline rows are grandfathered
        # through THIS ONE capability gate only; they still pass through
        # class, source, vendor, and pricing safety checks below.
        effective_canonical_seller = int(
            bool(
                canonical_seller
                or live_seller
            )
        )

        effective_canonical_buyer = int(
            bool(
                canonical_buyer
                or live_buyer
            )
        )

        vendor_item = as_bool_int(
            vendor_row.get(
                actual_vendor_col
            )
        )

        buyer_vendor_block = as_bool_int(
            vendor_row.get(
                buyer_vendor_block_col
            )
        )

        seller_vendor_floor_required = as_bool_int(
            vendor_row.get(
                seller_vendor_floor_required_col
            )
        )

        seller_vendor_ready = as_bool_int(
            vendor_row.get(
                seller_vendor_ready_col
            )
        )

        has_vendor_floor = as_bool_int(
            vendor_row.get(
                hard_floor_col
            )
        )

        pricing_ready = as_bool_int(
            candidate_row.get(
                pricing_ready_col
            )
        )

        # The current Phase-0 seller/buyer CSVs already contain valid
        # operational prices. Their presence therefore satisfies generic
        # price availability for the frozen live baseline only. This does
        # NOT waive stronger pricing rules such as an NPC-vendor hard floor.
        live_baseline_price_available = int(
            bool(
                live_seller
                or live_buyer
            )
        )

        effective_pricing_ready = int(
            bool(
                pricing_ready
                or live_baseline_price_available
            )
        )

        source_gate_open = as_bool_int(
            row.get("source_gate_open")
        )

        source_supply_allowed = as_bool_int(
            row.get(
                "synthetic_supply_source_allowed"
            )
        )

        source_keep_protected = as_bool_int(
            row.get("source_keep_protected")
        )

        pricing_gate_required = as_bool_int(
            row.get("pricing_gate_required")
        )

        seller_blockers: list[str] = []
        buyer_blockers: list[str] = []

        # Shared hard-class safety.
        hard_class_block = market_class in {
            "BLOCKED",
            "PROTECTED",
        }

        append_blocker(
            seller_blockers,
            hard_class_block,
            f"CLASS_{market_class}",
        )

        append_blocker(
            buyer_blockers,
            hard_class_block,
            f"CLASS_{market_class}",
        )

        # DEMAND_ONLY is intentionally buy-only.
        append_blocker(
            seller_blockers,
            market_class == "DEMAND_ONLY",
            "CLASS_DEMAND_ONLY",
        )

        # Canonical sidecars are the authoritative pre-maturity capability.
        append_blocker(
            seller_blockers,
            not effective_canonical_seller,
            "CANONICAL_SELLER_INELIGIBLE",
        )

        append_blocker(
            buyer_blockers,
            not effective_canonical_buyer,
            "CANONICAL_BUYER_INELIGIBLE",
        )

        # Source safety.
        append_blocker(
            seller_blockers,
            not source_gate_open,
            "SOURCE_GATE_CLOSED",
        )

        append_blocker(
            seller_blockers,
            not source_supply_allowed,
            "SYNTHETIC_SOURCE_NOT_ALLOWED",
        )

        append_blocker(
            buyer_blockers,
            source_keep_protected,
            "SOURCE_POLICY_PROTECTED",
        )

        # Global NPC-vendor rules.
        append_blocker(
            buyer_blockers,
            bool(buyer_vendor_block),
            "NPC_VENDOR_BUYER_BLOCK",
        )

        append_blocker(
            seller_blockers,
            bool(
                seller_vendor_floor_required
                and not seller_vendor_ready
            ),
            "NPC_VENDOR_NO_TRUSTED_FLOOR",
        )

        # Generic price availability. Current frozen Phase-0 entries
        # satisfy this through their existing runtime CSV prices.
        append_blocker(
            seller_blockers,
            not effective_pricing_ready,
            "PRICING_NOT_READY",
        )

        append_blocker(
            buyer_blockers,
            not effective_pricing_ready,
            "PRICING_NOT_READY",
        )

        append_blocker(
            seller_blockers,
            bool(
                pricing_gate_required
                and not pricing_ready
            ),
            "REQUIRED_PRICING_GATE_CLOSED",
        )

        append_blocker(
            buyer_blockers,
            bool(
                pricing_gate_required
                and not pricing_ready
            ),
            "REQUIRED_PRICING_GATE_CLOSED",
        )

        seller_capable = int(
            len(
                seller_blockers
            )
            == 0
        )

        buyer_capable = int(
            len(
                buyer_blockers
            )
            == 0
        )

        # No maturity-based activation here. 1B.5 owns maturity.
        seller_maturity_pending = int(
            seller_capable
            and bool(
                maturity
            )
        )

        buyer_maturity_pending = int(
            buyer_capable
            and bool(
                maturity
            )
        )

        seller_future_candidate = seller_capable
        buyer_future_candidate = buyer_capable

        for blocker in seller_blockers:
            seller_block_counts[
                blocker
            ] += 1

        for blocker in buyer_blockers:
            buyer_block_counts[
                blocker
            ] += 1

        rows.append(
            {
                "itemid": itemid,
                "name": clean_text(
                    row.get("name")
                ),
                "canonical_class": market_class,
                "maturity": maturity,
                "source_policy_family": source_family,
                "vendor_item": vendor_item,
                "buyer_vendor_block": buyer_vendor_block,
                "seller_vendor_floor_required":
                    seller_vendor_floor_required,
                "seller_vendor_ready": seller_vendor_ready,
                "has_vendor_price_floor": has_vendor_floor,
                "canonical_seller_policy_eligible":
                    canonical_seller,
                "canonical_buyer_policy_eligible":
                    canonical_buyer,
                "live_baseline_capability_override_seller":
                    int(
                        bool(
                            live_seller
                            and not canonical_seller
                        )
                    ),
                "live_baseline_capability_override_buyer":
                    int(
                        bool(
                            live_buyer
                            and not canonical_buyer
                        )
                    ),
                "effective_canonical_seller":
                    effective_canonical_seller,
                "effective_canonical_buyer":
                    effective_canonical_buyer,
                "pricing_ready": pricing_ready,
                "live_baseline_price_available":
                    live_baseline_price_available,
                "effective_pricing_ready":
                    effective_pricing_ready,
                "source_gate_open": source_gate_open,
                "synthetic_supply_source_allowed":
                    source_supply_allowed,
                "seller_capable": seller_capable,
                "buyer_capable": buyer_capable,
                "seller_future_candidate":
                    seller_future_candidate,
                "buyer_future_candidate":
                    buyer_future_candidate,
                "seller_maturity_pending":
                    seller_maturity_pending,
                "buyer_maturity_pending":
                    buyer_maturity_pending,
                "seller_blockers":
                    "|".join(
                        seller_blockers
                    ),
                "buyer_blockers":
                    "|".join(
                        buyer_blockers
                    ),
                "live_seller_baseline": live_seller,
                "live_buyer_baseline": live_buyer,
                "activation_ready": int(
                    bool(
                        live_seller
                        or live_buyer
                    )
                ),
                "auto_live_promotion": 0,
            }
        )

        if (
            live_seller
            and not seller_capable
        ):
            audit_rows.append(
                {
                    "itemid": itemid,
                    "name": clean_text(
                        row.get("name")
                    ),
                    "severity": "ERROR",
                    "audit_type":
                        "LIVE_SELLER_NOT_CAPABLE",
                    "details":
                        "|".join(
                            seller_blockers
                        ),
                }
            )

        if (
            live_buyer
            and not buyer_capable
        ):
            audit_rows.append(
                {
                    "itemid": itemid,
                    "name": clean_text(
                        row.get("name")
                    ),
                    "severity": "ERROR",
                    "audit_type":
                        "LIVE_BUYER_NOT_CAPABLE",
                    "details":
                        "|".join(
                            buyer_blockers
                        ),
                }
            )

        if (
            buyer_vendor_block
            and buyer_capable
        ):
            audit_rows.append(
                {
                    "itemid": itemid,
                    "name": clean_text(
                        row.get("name")
                    ),
                    "severity": "ERROR",
                    "audit_type":
                        "VENDOR_BUYER_ARBITRAGE_LEAK",
                    "details":
                        "Vendor item became buyer-capable.",
                }
            )

        if (
            seller_vendor_floor_required
            and seller_capable
            and not seller_vendor_ready
        ):
            audit_rows.append(
                {
                    "itemid": itemid,
                    "name": clean_text(
                        row.get("name")
                    ),
                    "severity": "ERROR",
                    "audit_type":
                        "VENDOR_SELLER_WITHOUT_FLOOR",
                    "details":
                        "Vendor item seller-capable without trusted floor.",
                }
            )

        if (
            market_class
            in {
                "BLOCKED",
                "PROTECTED",
            }
            and (
                seller_capable
                or buyer_capable
            )
        ):
            audit_rows.append(
                {
                    "itemid": itemid,
                    "name": clean_text(
                        row.get("name")
                    ),
                    "severity": "ERROR",
                    "audit_type":
                        "HARD_CLASS_CAPABILITY_LEAK",
                    "details":
                        (
                            f"class={market_class}, "
                            f"seller={seller_capable}, "
                            f"buyer={buyer_capable}"
                        ),
                }
            )

    output = pd.DataFrame(
        rows
    ).sort_values(
        by=[
            "itemid"
        ],
        kind="stable",
    )

    audit = pd.DataFrame(
        audit_rows,
        columns=[
            "itemid",
            "name",
            "severity",
            "audit_type",
            "details",
        ],
    )

    if len(output) != EXPECTED_TOTAL:
        raise UnifiedPolicyError(
            f"Expected {EXPECTED_TOTAL} output rows, "
            f"found {len(output)}"
        )

    if output["itemid"].duplicated().any():
        raise UnifiedPolicyError(
            "Unified seller/buyer policy has duplicate itemids."
        )

    live_sell_count = int(
        output[
            "live_seller_baseline"
        ].sum()
    )

    live_buy_count = int(
        output[
            "live_buyer_baseline"
        ].sum()
    )

    if live_sell_count != EXPECTED_LIVE_SELL:
        raise UnifiedPolicyError(
            f"Live seller baseline changed: "
            f"{live_sell_count} != {EXPECTED_LIVE_SELL}"
        )

    if live_buy_count != EXPECTED_LIVE_BUY:
        raise UnifiedPolicyError(
            f"Live buyer baseline changed: "
            f"{live_buy_count} != {EXPECTED_LIVE_BUY}"
        )

    if int(
        output[
            "auto_live_promotion"
        ].sum()
    ) != 0:
        raise UnifiedPolicyError(
            "Unified policy attempted automatic live promotion."
        )

    if not audit.empty:
        error_count = int(
            (
                audit[
                    "severity"
                ]
                == "ERROR"
            ).sum()
        )

        audit.to_csv(
            AUDIT_FILE,
            index=False,
        )

        if error_count:
            raise UnifiedPolicyError(
                f"Unified policy failed {error_count} hard audits. "
                f"See {AUDIT_FILE}"
            )

    else:
        audit.to_csv(
            AUDIT_FILE,
            index=False,
        )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    summary = {
        "status": "PASS",
        "total_items": int(
            len(
                output
            )
        ),
        "seller_capable": int(
            output[
                "seller_capable"
            ].sum()
        ),
        "buyer_capable": int(
            output[
                "buyer_capable"
            ].sum()
        ),
        "seller_future_candidates": int(
            output[
                "seller_future_candidate"
            ].sum()
        ),
        "buyer_future_candidates": int(
            output[
                "buyer_future_candidate"
            ].sum()
        ),
        "seller_with_maturity_metadata": int(
            output[
                "seller_maturity_pending"
            ].sum()
        ),
        "buyer_with_maturity_metadata": int(
            output[
                "buyer_maturity_pending"
            ].sum()
        ),
        "live_seller_baseline":
            live_sell_count,
        "live_buyer_baseline":
            live_buy_count,
        "baseline_price_override_items": int(
            output[
                "live_baseline_price_available"
            ].sum()
        ),
        "baseline_seller_capability_overrides": int(
            output[
                "live_baseline_capability_override_seller"
            ].sum()
        ),
        "baseline_buyer_capability_overrides": int(
            output[
                "live_baseline_capability_override_buyer"
            ].sum()
        ),
        "vendor_items": int(
            output[
                "vendor_item"
            ].sum()
        ),
        "vendor_buyer_capable": int(
            (
                (
                    output[
                        "vendor_item"
                    ]
                    == 1
                )
                & (
                    output[
                        "buyer_capable"
                    ]
                    == 1
                )
            ).sum()
        ),
        "vendor_seller_without_floor": int(
            (
                (
                    output[
                        "vendor_item"
                    ]
                    == 1
                )
                & (
                    output[
                        "seller_capable"
                    ]
                    == 1
                )
                & (
                    output[
                        "has_vendor_price_floor"
                    ]
                    == 0
                )
            ).sum()
        ),
        "class_capability_counts": {
            market_class: {
                "items": int(
                    len(
                        group
                    )
                ),
                "seller_capable": int(
                    group[
                        "seller_capable"
                    ].sum()
                ),
                "buyer_capable": int(
                    group[
                        "buyer_capable"
                    ].sum()
                ),
            }
            for market_class, group
            in output.groupby(
                "canonical_class"
            )
        },
        "top_seller_blockers": dict(
            seller_block_counts.most_common(
                30
            )
        ),
        "top_buyer_blockers": dict(
            buyer_block_counts.most_common(
                30
            )
        ),
        "hard_audit_errors": 0,
        "activation_ready_union": int(
            (
                (
                    output[
                        "live_seller_baseline"
                    ]
                    == 1
                )
                | (
                    output[
                        "live_buyer_baseline"
                    ]
                    == 1
                )
            ).sum()
        ),
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
    print("=" * 54)
    print(" Unified Seller / Buyer Policy")
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
        f"Live seller baseline:                "
        f"{summary['live_seller_baseline']:>6}"
    )
    print(
        f"Live buyer baseline:                 "
        f"{summary['live_buyer_baseline']:>6}"
    )
    print(
        f"Baseline price override items:       "
        f"{summary['baseline_price_override_items']:>6}"
    )
    print(
        f"Baseline seller capability overrides:"
        f"{summary['baseline_seller_capability_overrides']:>6}"
    )
    print(
        f"Baseline buyer capability overrides: "
        f"{summary['baseline_buyer_capability_overrides']:>6}"
    )
    print(
        f"Vendor buyer capable:                "
        f"{summary['vendor_buyer_capable']:>6}"
    )
    print(
        f"Vendor seller without floor:         "
        f"{summary['vendor_seller_without_floor']:>6}"
    )
    print(
        "Hard audit errors:                        0"
    )
    print(
        "Auto live promotions:                     0"
    )
    print()
    print("Capability by class:")
    for market_class, values in sorted(
        summary[
            "class_capability_counts"
        ].items(),
        key=lambda pair: pair[0],
    ):
        print(
            f"  {market_class:<16} "
            f"items={values['items']:>6} "
            f"sell={values['seller_capable']:>6} "
            f"buy={values['buyer_capable']:>6}"
        )
    print()
    print(f"Generated: {OUTPUT_FILE}")
    print(f"Audit:     {AUDIT_FILE}")
    print(f"Summary:   {SUMMARY_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
