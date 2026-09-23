from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
REPORTS = ROOT / "economy" / "reports"

INTELLIGENCE_CSV = (
    REPORTS
    / "market-history-item-intelligence.csv"
)

INTELLIGENCE_SUMMARY = (
    REPORTS
    / "market-history-intelligence-summary.json"
)

OUTPUT_CSV = (
    REPORTS
    / "market-history-confidence.csv"
)

SUMMARY_JSON = (
    REPORTS
    / "market-history-confidence-summary.json"
)


MATURITY_ORDER = (
    "NO_DATA",
    "SPARSE",
    "DEVELOPING",
    "ESTABLISHED",
    "TRUSTED",
)

FRESHNESS_ORDER = (
    "RECENT",
    "AGING",
    "STALE",
)


class MarketHistoryConfidenceError(RuntimeError):
    pass


def as_int(value: Any) -> int:
    if value is None:
        return 0

    try:
        if pd.isna(value):
            return 0
    except TypeError:
        pass

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def as_float_or_none(value: Any):
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_upstream_summary() -> dict[str, Any]:
    if not INTELLIGENCE_SUMMARY.exists():
        raise MarketHistoryConfidenceError(
            "Missing 2A.4 summary: "
            f"{INTELLIGENCE_SUMMARY}"
        )

    summary = json.loads(
        INTELLIGENCE_SUMMARY.read_text(
            encoding="utf-8",
        )
    )

    if summary.get("status") != "PASS":
        raise MarketHistoryConfidenceError(
            "2A.4 summary is not PASS."
        )

    required_zero = (
        "integrity_failures",
        "history_price_influence_ready",
        "history_stock_influence_ready",
        "activation_ready",
        "auto_live_promotions",
    )

    invalid = {
        field: summary.get(field)
        for field in required_zero
        if summary.get(field) != 0
    }

    if invalid:
        raise MarketHistoryConfidenceError(
            "Unsafe upstream state: "
            f"{invalid}"
        )

    return summary


def maturity_state(
    *,
    transactions: int,
    organic: int,
    asks: int,
    bids: int,
    durations: int,
) -> str:
    if transactions <= 0:
        return "NO_DATA"

    if (
        transactions >= 25
        and organic >= 10
        and asks >= 8
        and bids >= 8
        and durations >= 5
    ):
        return "TRUSTED"

    if (
        transactions >= 12
        and organic >= 4
        and asks >= 4
        and bids >= 4
        and durations >= 3
    ):
        return "ESTABLISHED"

    if (
        transactions >= 5
        and organic >= 1
        and asks >= 2
        and bids >= 2
    ):
        return "DEVELOPING"

    return "SPARSE"


def freshness_state(
    days_since_latest: float | None,
) -> str:
    if days_since_latest is None:
        return "STALE"

    if days_since_latest <= 30:
        return "RECENT"

    if days_since_latest <= 90:
        return "AGING"

    return "STALE"


def trusted_shortfalls(
    *,
    transactions: int,
    organic: int,
    asks: int,
    bids: int,
    durations: int,
) -> str:
    shortfalls = []

    requirements = (
        ("transactions", transactions, 25),
        ("organic", organic, 10),
        ("asks", asks, 8),
        ("bids", bids, 8),
        ("durations", durations, 5),
    )

    for name, value, required in requirements:
        if value < required:
            shortfalls.append(
                f"{name}:{value}/{required}"
            )

    return ";".join(shortfalls)


def main() -> int:
    upstream = load_upstream_summary()

    if not INTELLIGENCE_CSV.exists():
        raise MarketHistoryConfidenceError(
            "Missing intelligence CSV: "
            f"{INTELLIGENCE_CSV}"
        )

    intelligence = pd.read_csv(
        INTELLIGENCE_CSV
    )

    expected_rows = int(
        upstream[
            "item_forms_with_player_history"
        ]
    )

    if len(intelligence) != expected_rows:
        raise MarketHistoryConfidenceError(
            "2A.4 intelligence row count "
            "does not match its summary."
        )

    rows: list[dict[str, Any]] = []

    for _, row in intelligence.iterrows():
        itemid = as_int(
            row.get("itemid")
        )

        stack = as_int(
            row.get("stack")
        )

        transactions = as_int(
            row.get(
                "player_involved_transactions"
            )
        )

        organic = as_int(
            row.get(
                "organic_transactions"
            )
        )

        asks = as_int(
            row.get(
                "ask_evidence_events"
            )
        )

        bids = as_int(
            row.get(
                "bid_evidence_events"
            )
        )

        durations = as_int(
            row.get(
                "valid_player_listing_durations"
            )
        )

        days_since_latest = as_float_or_none(
            row.get(
                "days_since_latest_player_event"
            )
        )

        maturity = maturity_state(
            transactions=transactions,
            organic=organic,
            asks=asks,
            bids=bids,
            durations=durations,
        )

        freshness = freshness_state(
            days_since_latest
        )

        sparse_history = int(
            maturity == "SPARSE"
        )

        no_organic_history = int(
            organic == 0
        )

        one_sided_evidence = int(
            (asks == 0 and bids > 0)
            or (bids == 0 and asks > 0)
        )

        stale_history = int(
            freshness == "STALE"
        )

        trusted_and_recent = int(
            maturity == "TRUSTED"
            and freshness == "RECENT"
        )

        rows.append({
            "itemid":
                itemid,

            "stack":
                stack,

            "player_involved_transactions":
                transactions,

            "organic_transactions":
                organic,

            "ask_evidence_events":
                asks,

            "bid_evidence_events":
                bids,

            "valid_player_listing_durations":
                durations,

            "transactions_7d":
                as_int(
                    row.get(
                        "player_transactions_7d"
                    )
                ),

            "transactions_30d":
                as_int(
                    row.get(
                        "player_transactions_30d"
                    )
                ),

            "transactions_90d":
                as_int(
                    row.get(
                        "player_transactions_90d"
                    )
                ),

            "days_since_latest_player_event":
                days_since_latest,

            "evidence_maturity":
                maturity,

            "freshness_state":
                freshness,

            "sparse_history":
                sparse_history,

            "no_organic_history":
                no_organic_history,

            "one_sided_evidence":
                one_sided_evidence,

            "stale_history":
                stale_history,

            "trusted_and_recent":
                trusted_and_recent,

            "trusted_shortfalls":
                trusted_shortfalls(
                    transactions=transactions,
                    organic=organic,
                    asks=asks,
                    bids=bids,
                    durations=durations,
                ),

            # Observation maturity is NOT
            # deployment authority.
            "history_price_influence_ready":
                0,

            "history_stock_influence_ready":
                0,

            "activation_ready":
                0,

            "auto_live_promotion":
                0,
        })

    confidence = pd.DataFrame(rows)

    maturity_counts = {
        tier: int(
            (
                confidence[
                    "evidence_maturity"
                ]
                == tier
            ).sum()
        )
        for tier in MATURITY_ORDER
    }

    freshness_counts = {
        state: int(
            (
                confidence[
                    "freshness_state"
                ]
                == state
            ).sum()
        )
        for state in FRESHNESS_ORDER
    }

    sparse_rows = int(
        confidence[
            "sparse_history"
        ].sum()
    )

    no_organic_rows = int(
        confidence[
            "no_organic_history"
        ].sum()
    )

    one_sided_rows = int(
        confidence[
            "one_sided_evidence"
        ].sum()
    )

    stale_rows = int(
        confidence[
            "stale_history"
        ].sum()
    )

    trusted_recent_rows = int(
        confidence[
            "trusted_and_recent"
        ].sum()
    )

    integrity_failures = 0

    if int(
        sum(
            maturity_counts.values()
        )
    ) != len(confidence):
        integrity_failures += 1

    if int(
        sum(
            freshness_counts.values()
        )
    ) != len(confidence):
        integrity_failures += 1

    safety_columns = (
        "history_price_influence_ready",
        "history_stock_influence_ready",
        "activation_ready",
        "auto_live_promotion",
    )

    for column in safety_columns:
        if (
            not confidence.empty
            and (
                confidence[column]
                != 0
            ).any()
        ):
            integrity_failures += 1

    summary = {
        "status":
            (
                "PASS"
                if integrity_failures == 0
                else "FAIL"
            ),

        "stage":
            "2A.5_CONFIDENCE_AND_SPARSE_MARKET_RULES",

        "item_forms_evaluated":
            int(len(confidence)),

        "maturity_counts":
            maturity_counts,

        "freshness_counts":
            freshness_counts,

        "sparse_market_rows":
            sparse_rows,

        "no_organic_history_rows":
            no_organic_rows,

        "one_sided_evidence_rows":
            one_sided_rows,

        "stale_history_rows":
            stale_rows,

        "trusted_and_recent_rows":
            trusted_recent_rows,

        "thresholds": {
            "DEVELOPING": {
                "player_transactions": 5,
                "organic_transactions": 1,
                "ask_events": 2,
                "bid_events": 2,
            },

            "ESTABLISHED": {
                "player_transactions": 12,
                "organic_transactions": 4,
                "ask_events": 4,
                "bid_events": 4,
                "valid_player_listing_durations": 3,
            },

            "TRUSTED": {
                "player_transactions": 25,
                "organic_transactions": 10,
                "ask_events": 8,
                "bid_events": 8,
                "valid_player_listing_durations": 5,
            },

            "RECENT_MAX_DAYS": 30,
            "AGING_MAX_DAYS": 90,
        },

        "integrity_failures":
            integrity_failures,

        "history_price_influence_ready":
            0,

        "history_stock_influence_ready":
            0,

        "activation_ready":
            0,

        "auto_live_promotions":
            0,

        "generated_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),
    }

    REPORTS.mkdir(
        parents=True,
        exist_ok=True,
    )

    confidence.to_csv(
        OUTPUT_CSV,
        index=False,
    )

    SUMMARY_JSON.write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 84)
    print(
        " Phase 2A.5 Confidence + "
        "Sparse-Market Rules"
    )
    print("=" * 84)

    print()
    print(
        f"Item forms evaluated:           "
        f"{len(confidence):>6}"
    )

    print()
    print("Evidence maturity:")

    for tier in MATURITY_ORDER:
        print(
            f"  {tier:<18} "
            f"{maturity_counts[tier]:>6}"
        )

    print()
    print("Freshness:")

    for state in FRESHNESS_ORDER:
        print(
            f"  {state:<18} "
            f"{freshness_counts[state]:>6}"
        )

    print()
    print(
        f"Sparse markets:                 "
        f"{sparse_rows:>6}"
    )

    print(
        f"No organic history:             "
        f"{no_organic_rows:>6}"
    )

    print(
        f"One-sided evidence:             "
        f"{one_sided_rows:>6}"
    )

    print(
        f"Stale history:                  "
        f"{stale_rows:>6}"
    )

    print(
        f"Trusted + recent:               "
        f"{trusted_recent_rows:>6}"
    )

    print()
    print(
        f"Integrity failures:             "
        f"{integrity_failures:>6}"
    )

    print()
    print(
        "History price influence ready:       0"
    )

    print(
        "History stock influence ready:       0"
    )

    print(
        "Activation ready:                    0"
    )

    print(
        "Auto live promotions:                0"
    )

    print()
    print(
        f"Confidence: {OUTPUT_CSV}"
    )

    print(
        f"Summary:    {SUMMARY_JSON}"
    )

    print()

    if integrity_failures:
        print("FAIL")

        raise MarketHistoryConfidenceError(
            "2A.5 confidence classification "
            "failed integrity checks."
        )

    print("PASS")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
