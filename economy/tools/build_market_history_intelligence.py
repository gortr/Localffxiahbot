from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
REPORTS = ROOT / "economy" / "reports"

PLAYER_SALES_CSV = (
    REPORTS / "market-history-player-sales.csv"
)

EVIDENCE_CSV = (
    REPORTS / "market-history-evidence.csv"
)

EXTRACTION_SUMMARY = (
    REPORTS / "market-history-extraction-summary.json"
)

OUTPUT_CSV = (
    REPORTS / "market-history-item-intelligence.csv"
)

SUMMARY_JSON = (
    REPORTS / "market-history-intelligence-summary.json"
)


class MarketHistoryIntelligenceError(RuntimeError):
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


def optional_int(value: Any):
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass

    return int(value)


def optional_float(value: Any):
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass

    return float(value)


def load_summary() -> dict[str, Any]:
    if not EXTRACTION_SUMMARY.exists():
        raise MarketHistoryIntelligenceError(
            f"Missing extraction summary: {EXTRACTION_SUMMARY}"
        )

    summary = json.loads(
        EXTRACTION_SUMMARY.read_text(
            encoding="utf-8"
        )
    )

    if summary.get("status") != "PASS":
        raise MarketHistoryIntelligenceError(
            "2A.3 extraction summary is not PASS."
        )

    safety_fields = (
        "integrity_failures",
        "history_price_influence_ready",
        "history_stock_influence_ready",
        "activation_ready",
        "auto_live_promotions",
    )

    invalid = {
        field: summary.get(field)
        for field in safety_fields
        if summary.get(field) != 0
    }

    if invalid:
        raise MarketHistoryIntelligenceError(
            f"2A.3 safety state invalid: {invalid}"
        )

    return summary


def median_int(series: pd.Series):
    if series.empty:
        return None

    return float(series.median())


def main() -> int:
    extraction_summary = load_summary()

    if not PLAYER_SALES_CSV.exists():
        raise MarketHistoryIntelligenceError(
            f"Missing player sales CSV: {PLAYER_SALES_CSV}"
        )

    if not EVIDENCE_CSV.exists():
        raise MarketHistoryIntelligenceError(
            f"Missing evidence CSV: {EVIDENCE_CSV}"
        )

    sales = pd.read_csv(PLAYER_SALES_CSV)
    evidence = pd.read_csv(EVIDENCE_CSV)

    expected_sales = int(
        extraction_summary[
            "player_involved_transactions"
        ]
    )

    expected_evidence = int(
        extraction_summary[
            "evidence_events"
        ]
    )

    if len(sales) != expected_sales:
        raise MarketHistoryIntelligenceError(
            "Player-sales row count does not match "
            "2A.3 summary."
        )

    if len(evidence) != expected_evidence:
        raise MarketHistoryIntelligenceError(
            "Evidence row count does not match "
            "2A.3 summary."
        )

    if not evidence.empty:
        invalid_types = evidence[
            ~evidence["evidence_type"].isin(
                {"ASK", "BID"}
            )
        ]

        if len(invalid_types):
            raise MarketHistoryIntelligenceError(
                "Unexpected evidence_type values found."
            )

        duplicate_events = int(
            evidence.duplicated(
                subset=[
                    "transaction_id",
                    "evidence_type",
                ]
            ).sum()
        )

        if duplicate_events:
            raise MarketHistoryIntelligenceError(
                f"Duplicate evidence events: "
                f"{duplicate_events}"
            )

        invalid_prices = int(
            (
                evidence["evidence_price"] <= 0
            ).sum()
        )

        if invalid_prices:
            raise MarketHistoryIntelligenceError(
                f"Invalid evidence prices: "
                f"{invalid_prices}"
            )

        synthetic_leaks = int(
            (
                evidence[
                    "transaction_class"
                ]
                == "SYNTHETIC_HISTORY"
            ).sum()
        )

        if synthetic_leaks:
            raise MarketHistoryIntelligenceError(
                f"Synthetic evidence leak: "
                f"{synthetic_leaks}"
            )

    now_ts = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    keys = set()

    for _, row in sales.iterrows():
        keys.add(
            (
                as_int(row["itemid"]),
                as_int(row["stack"]),
            )
        )

    for _, row in evidence.iterrows():
        keys.add(
            (
                as_int(row["itemid"]),
                as_int(row["stack"]),
            )
        )

    rows: list[dict[str, Any]] = []

    for itemid, stack in sorted(keys):
        item_sales = sales[
            (sales["itemid"] == itemid)
            & (sales["stack"] == stack)
        ].copy()

        item_evidence = evidence[
            (evidence["itemid"] == itemid)
            & (evidence["stack"] == stack)
        ].copy()

        asks = item_evidence[
            item_evidence[
                "evidence_type"
            ]
            == "ASK"
        ]

        bids = item_evidence[
            item_evidence[
                "evidence_type"
            ]
            == "BID"
        ]

        organic_sales = item_sales[
            item_sales[
                "transaction_class"
            ]
            == "ORGANIC_PLAYER_TRADE"
        ]

        valid_durations = item_sales[
            item_sales[
                "listing_duration_valid"
            ]
            == 1
        ]["listing_duration_seconds"].dropna()

        latest_event_ts = (
            as_int(
                item_sales[
                    "sold_timestamp"
                ].max()
            )
            if not item_sales.empty
            else 0
        )

        latest_ask_ts = (
            as_int(
                asks[
                    "sold_timestamp"
                ].max()
            )
            if not asks.empty
            else 0
        )

        latest_bid_ts = (
            as_int(
                bids[
                    "sold_timestamp"
                ].max()
            )
            if not bids.empty
            else 0
        )

        def count_transactions_within(
            days: int,
        ) -> int:
            cutoff = (
                now_ts
                - days * 86400
            )

            return int(
                (
                    item_sales[
                        "sold_timestamp"
                    ]
                    >= cutoff
                ).sum()
            )

        def count_evidence_within(
            frame: pd.DataFrame,
            days: int,
        ) -> int:
            if frame.empty:
                return 0

            cutoff = (
                now_ts
                - days * 86400
            )

            return int(
                (
                    frame[
                        "sold_timestamp"
                    ]
                    >= cutoff
                ).sum()
            )

        rows.append({
            "itemid":
                itemid,

            "stack":
                stack,

            "player_involved_transactions":
                int(len(item_sales)),

            "organic_transactions":
                int(len(organic_sales)),

            "ask_evidence_events":
                int(len(asks)),

            "bid_evidence_events":
                int(len(bids)),

            "player_transactions_7d":
                count_transactions_within(7),

            "player_transactions_30d":
                count_transactions_within(30),

            "player_transactions_90d":
                count_transactions_within(90),

            "ask_events_7d":
                count_evidence_within(
                    asks,
                    7,
                ),

            "ask_events_30d":
                count_evidence_within(
                    asks,
                    30,
                ),

            "ask_events_90d":
                count_evidence_within(
                    asks,
                    90,
                ),

            "bid_events_7d":
                count_evidence_within(
                    bids,
                    7,
                ),

            "bid_events_30d":
                count_evidence_within(
                    bids,
                    30,
                ),

            "bid_events_90d":
                count_evidence_within(
                    bids,
                    90,
                ),

            "ask_min":
                (
                    optional_int(
                        asks[
                            "evidence_price"
                        ].min()
                    )
                    if not asks.empty
                    else None
                ),

            "ask_median":
                median_int(
                    asks[
                        "evidence_price"
                    ]
                ),

            "ask_mean":
                (
                    optional_float(
                        asks[
                            "evidence_price"
                        ].mean()
                    )
                    if not asks.empty
                    else None
                ),

            "ask_max":
                (
                    optional_int(
                        asks[
                            "evidence_price"
                        ].max()
                    )
                    if not asks.empty
                    else None
                ),

            "bid_min":
                (
                    optional_int(
                        bids[
                            "evidence_price"
                        ].min()
                    )
                    if not bids.empty
                    else None
                ),

            "bid_median":
                median_int(
                    bids[
                        "evidence_price"
                    ]
                ),

            "bid_mean":
                (
                    optional_float(
                        bids[
                            "evidence_price"
                        ].mean()
                    )
                    if not bids.empty
                    else None
                ),

            "bid_max":
                (
                    optional_int(
                        bids[
                            "evidence_price"
                        ].max()
                    )
                    if not bids.empty
                    else None
                ),

            "valid_player_listing_durations":
                int(
                    len(
                        valid_durations
                    )
                ),

            "listing_duration_min_seconds":
                (
                    optional_int(
                        valid_durations.min()
                    )
                    if not valid_durations.empty
                    else None
                ),

            "listing_duration_median_seconds":
                (
                    median_int(
                        valid_durations
                    )
                    if not valid_durations.empty
                    else None
                ),

            "listing_duration_max_seconds":
                (
                    optional_int(
                        valid_durations.max()
                    )
                    if not valid_durations.empty
                    else None
                ),

            "latest_player_event_timestamp":
                latest_event_ts,

            "latest_ask_timestamp":
                latest_ask_ts,

            "latest_bid_timestamp":
                latest_bid_ts,

            "days_since_latest_player_event":
                (
                    round(
                        (
                            now_ts
                            - latest_event_ts
                        )
                        / 86400,
                        3,
                    )
                    if latest_event_ts > 0
                    else None
                ),

            # 2A.4 remains descriptive only.
            "history_price_influence_ready":
                0,

            "history_stock_influence_ready":
                0,

            "activation_ready":
                0,

            "auto_live_promotion":
                0,
        })

    intelligence = pd.DataFrame(rows)

    total_player_transactions = int(
        intelligence[
            "player_involved_transactions"
        ].sum()
    ) if not intelligence.empty else 0

    total_organic_transactions = int(
        intelligence[
            "organic_transactions"
        ].sum()
    ) if not intelligence.empty else 0

    total_ask_events = int(
        intelligence[
            "ask_evidence_events"
        ].sum()
    ) if not intelligence.empty else 0

    total_bid_events = int(
        intelligence[
            "bid_evidence_events"
        ].sum()
    ) if not intelligence.empty else 0

    integrity_failures = 0

    if total_player_transactions != expected_sales:
        integrity_failures += 1

    if (
        total_ask_events
        != int(
            extraction_summary[
                "ask_evidence_events"
            ]
        )
    ):
        integrity_failures += 1

    if (
        total_bid_events
        != int(
            extraction_summary[
                "bid_evidence_events"
            ]
        )
    ):
        integrity_failures += 1

    if (
        total_ask_events
        + total_bid_events
        != expected_evidence
    ):
        integrity_failures += 1

    safety_columns = [
        "history_price_influence_ready",
        "history_stock_influence_ready",
        "activation_ready",
        "auto_live_promotion",
    ]

    for column in safety_columns:
        if (
            not intelligence.empty
            and (
                intelligence[column]
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
            "2A.4_PRICE_VOLUME_INTELLIGENCE",

        "item_forms_with_player_history":
            int(len(intelligence)),

        "player_involved_transactions":
            total_player_transactions,

        "organic_transactions":
            total_organic_transactions,

        "ask_evidence_events":
            total_ask_events,

        "bid_evidence_events":
            total_bid_events,

        "evidence_events":
            int(
                total_ask_events
                + total_bid_events
            ),

        "transactions_last_7d":
            int(
                intelligence[
                    "player_transactions_7d"
                ].sum()
            )
            if not intelligence.empty
            else 0,

        "transactions_last_30d":
            int(
                intelligence[
                    "player_transactions_30d"
                ].sum()
            )
            if not intelligence.empty
            else 0,

        "transactions_last_90d":
            int(
                intelligence[
                    "player_transactions_90d"
                ].sum()
            )
            if not intelligence.empty
            else 0,

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

    intelligence.to_csv(
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
        " Phase 2A.4 Price / Volume "
        "Intelligence"
    )
    print("=" * 84)

    print()
    print(
        f"Item forms with player history: "
        f"{len(intelligence):>6}"
    )

    print(
        f"Player-involved transactions:   "
        f"{total_player_transactions:>6}"
    )

    print(
        f"Organic transactions:           "
        f"{total_organic_transactions:>6}"
    )

    print()
    print(
        f"ASK evidence events:            "
        f"{total_ask_events:>6}"
    )

    print(
        f"BID evidence events:            "
        f"{total_bid_events:>6}"
    )

    print()
    print(
        f"Transactions last 7d:           "
        f"{summary['transactions_last_7d']:>6}"
    )

    print(
        f"Transactions last 30d:          "
        f"{summary['transactions_last_30d']:>6}"
    )

    print(
        f"Transactions last 90d:          "
        f"{summary['transactions_last_90d']:>6}"
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
        f"Intelligence: {OUTPUT_CSV}"
    )

    print(
        f"Summary:      {SUMMARY_JSON}"
    )

    print()

    if integrity_failures:
        print("FAIL")

        raise MarketHistoryIntelligenceError(
            "2A.4 intelligence aggregation "
            "failed integrity checks."
        )

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
