from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
REPORTS = ROOT / "economy" / "reports"

CLASSIFIER_CSV = (
    REPORTS
    / "market-history-transactions.csv"
)

CLASSIFIER_SUMMARY = (
    REPORTS
    / "market-history-classifier-summary.json"
)

PLAYER_SALES_CSV = (
    REPORTS
    / "market-history-player-sales.csv"
)

EVIDENCE_CSV = (
    REPORTS
    / "market-history-evidence.csv"
)

SUMMARY_JSON = (
    REPORTS
    / "market-history-extraction-summary.json"
)


CLASS_SYNTHETIC = "SYNTHETIC_HISTORY"
CLASS_PLAYER_SUPPLY = "PLAYER_SUPPLY_TO_BOT"
CLASS_PLAYER_DEMAND = "PLAYER_DEMAND_FROM_BOT"
CLASS_ORGANIC = "ORGANIC_PLAYER_TRADE"


class MarketHistoryEvidenceError(
    RuntimeError
):
    pass


def as_int(
    value: Any,
) -> int:
    if value is None:
        return 0

    try:
        if pd.isna(value):
            return 0
    except TypeError:
        pass

    try:
        return int(value)
    except (
        TypeError,
        ValueError,
    ):
        return 0


def clean_text(
    value: Any,
) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass

    return str(value).strip()


def load_classifier_summary() -> dict[str, Any]:
    if not CLASSIFIER_SUMMARY.exists():
        raise MarketHistoryEvidenceError(
            "Missing classifier summary: "
            f"{CLASSIFIER_SUMMARY}"
        )

    summary = json.loads(
        CLASSIFIER_SUMMARY.read_text(
            encoding="utf-8",
        )
    )

    if summary.get("status") != "PASS":
        raise MarketHistoryEvidenceError(
            "Classifier summary is not PASS."
        )

    required_zero = {
        "unclassified_rows":
            summary.get(
                "unclassified_rows",
                -1,
            ),

        "integrity_failures":
            summary.get(
                "integrity_failures",
                -1,
            ),

        "history_price_influence_ready":
            summary.get(
                "history_price_influence_ready",
                -1,
            ),

        "history_stock_influence_ready":
            summary.get(
                "history_stock_influence_ready",
                -1,
            ),

        "activation_ready":
            summary.get(
                "activation_ready",
                -1,
            ),

        "auto_live_promotions":
            summary.get(
                "auto_live_promotions",
                -1,
            ),
    }

    invalid = {
        key: value
        for key, value in required_zero.items()
        if value != 0
    }

    if invalid:
        raise MarketHistoryEvidenceError(
            "Classifier safety state invalid: "
            f"{invalid}"
        )

    return summary


def append_evidence(
    evidence: list[dict[str, Any]],
    row: pd.Series,
    *,
    evidence_type: str,
    evidence_price: int,
    evidence_role: str,
) -> None:
    evidence.append({
        "transaction_id":
            as_int(
                row.get("id")
            ),

        "itemid":
            as_int(
                row.get("itemid")
            ),

        "stack":
            as_int(
                row.get("stack")
            ),

        "transaction_class":
            clean_text(
                row.get(
                    "transaction_class"
                )
            ),

        "evidence_type":
            evidence_type,

        "evidence_role":
            evidence_role,

        "evidence_price":
            evidence_price,

        "ask_price":
            as_int(
                row.get("ask_price")
            ),

        "bid_price":
            as_int(
                row.get("bid_price")
            ),

        "seller_name":
            clean_text(
                row.get("seller_name")
            ),

        "buyer_name":
            clean_text(
                row.get("buyer_name")
            ),

        "listed_timestamp":
            as_int(
                row.get(
                    "listed_timestamp"
                )
            ),

        "sold_timestamp":
            as_int(
                row.get(
                    "sold_timestamp"
                )
            ),

        "listing_duration_valid":
            as_int(
                row.get(
                    "listing_duration_valid"
                )
            ),

        "listing_duration_seconds":
            (
                None
                if pd.isna(
                    row.get(
                        "listing_duration_seconds"
                    )
                )
                else as_int(
                    row.get(
                        "listing_duration_seconds"
                    )
                )
            ),

        "organic_trade":
            as_int(
                row.get(
                    "organic_trade"
                )
            ),

        # Explicit downstream safety firewall.
        "price_influence_ready":
            0,

        "stock_influence_ready":
            0,

        "activation_ready":
            0,

        "auto_live_promotion":
            0,
    })


def main() -> int:
    classifier_summary = (
        load_classifier_summary()
    )

    if not CLASSIFIER_CSV.exists():
        raise MarketHistoryEvidenceError(
            "Missing classifier CSV: "
            f"{CLASSIFIER_CSV}"
        )

    transactions = pd.read_csv(
        CLASSIFIER_CSV
    )

    if len(transactions) != int(
        classifier_summary[
            "completed_transactions"
        ]
    ):
        raise MarketHistoryEvidenceError(
            "Classifier CSV row count does "
            "not match classifier summary."
        )

    synthetic = transactions[
        transactions[
            "transaction_class"
        ]
        == CLASS_SYNTHETIC
    ]

    player_sales = transactions[
        transactions[
            "transaction_class"
        ].isin({
            CLASS_PLAYER_SUPPLY,
            CLASS_PLAYER_DEMAND,
            CLASS_ORGANIC,
        })
    ].copy()

    excluded_count = len(
        transactions
    ) - len(
        player_sales
    )

    if excluded_count != len(
        synthetic
    ):
        raise MarketHistoryEvidenceError(
            "Unexpected completed transaction "
            "class encountered during extraction."
        )

    evidence: list[
        dict[str, Any]
    ] = []

    for _, row in (
        player_sales
        .sort_values(
            [
                "sold_timestamp",
                "id",
            ]
        )
        .iterrows()
    ):
        transaction_class = clean_text(
            row.get(
                "transaction_class"
            )
        )

        ask_price = as_int(
            row.get("ask_price")
        )

        bid_price = as_int(
            row.get("bid_price")
        )

        if (
            transaction_class
            == CLASS_PLAYER_SUPPLY
        ):
            if as_int(
                row.get(
                    "ask_evidence_valid"
                )
            ) != 1:
                raise MarketHistoryEvidenceError(
                    "PLAYER_SUPPLY_TO_BOT "
                    "row lacks valid ask "
                    f"evidence: id={row.get('id')}"
                )

            append_evidence(
                evidence,
                row,
                evidence_type="ASK",
                evidence_price=ask_price,
                evidence_role=(
                    "PLAYER_SUPPLY"
                ),
            )

        elif (
            transaction_class
            == CLASS_PLAYER_DEMAND
        ):
            if as_int(
                row.get(
                    "bid_evidence_valid"
                )
            ) != 1:
                raise MarketHistoryEvidenceError(
                    "PLAYER_DEMAND_FROM_BOT "
                    "row lacks valid bid "
                    f"evidence: id={row.get('id')}"
                )

            append_evidence(
                evidence,
                row,
                evidence_type="BID",
                evidence_price=bid_price,
                evidence_role=(
                    "PLAYER_DEMAND"
                ),
            )

        elif (
            transaction_class
            == CLASS_ORGANIC
        ):
            if (
                as_int(
                    row.get(
                        "ask_evidence_valid"
                    )
                )
                != 1
                or as_int(
                    row.get(
                        "bid_evidence_valid"
                    )
                )
                != 1
            ):
                raise MarketHistoryEvidenceError(
                    "ORGANIC_PLAYER_TRADE "
                    "does not contain both "
                    "valid ask and bid evidence: "
                    f"id={row.get('id')}"
                )

            append_evidence(
                evidence,
                row,
                evidence_type="ASK",
                evidence_price=ask_price,
                evidence_role=(
                    "ORGANIC_SUPPLY"
                ),
            )

            append_evidence(
                evidence,
                row,
                evidence_type="BID",
                evidence_price=bid_price,
                evidence_role=(
                    "ORGANIC_DEMAND"
                ),
            )

        else:
            raise MarketHistoryEvidenceError(
                "Unexpected transaction class "
                f"during evidence extraction: "
                f"{transaction_class}"
            )

    evidence_frame = pd.DataFrame(
        evidence
    )

    if evidence_frame.empty:
        evidence_frame = pd.DataFrame(
            columns=[
                "transaction_id",
                "itemid",
                "stack",
                "transaction_class",
                "evidence_type",
                "evidence_role",
                "evidence_price",
                "ask_price",
                "bid_price",
                "seller_name",
                "buyer_name",
                "listed_timestamp",
                "sold_timestamp",
                "listing_duration_valid",
                "listing_duration_seconds",
                "organic_trade",
                "price_influence_ready",
                "stock_influence_ready",
                "activation_ready",
                "auto_live_promotion",
            ]
        )

    invalid_prices = int(
        (
            evidence_frame[
                "evidence_price"
            ]
            <= 0
        ).sum()
    )

    synthetic_leaks = int(
        (
            evidence_frame[
                "transaction_class"
            ]
            == CLASS_SYNTHETIC
        ).sum()
    )

    invalid_price_flags = int(
        (
            evidence_frame[
                "price_influence_ready"
            ]
            != 0
        ).sum()
    )

    invalid_stock_flags = int(
        (
            evidence_frame[
                "stock_influence_ready"
            ]
            != 0
        ).sum()
    )

    invalid_activation_flags = int(
        (
            evidence_frame[
                "activation_ready"
            ]
            != 0
        ).sum()
    )

    invalid_promotion_flags = int(
        (
            evidence_frame[
                "auto_live_promotion"
            ]
            != 0
        ).sum()
    )

    integrity_failures = (
        invalid_prices
        + synthetic_leaks
        + invalid_price_flags
        + invalid_stock_flags
        + invalid_activation_flags
        + invalid_promotion_flags
    )

    ask_events = int(
        (
            evidence_frame[
                "evidence_type"
            ]
            == "ASK"
        ).sum()
    )

    bid_events = int(
        (
            evidence_frame[
                "evidence_type"
            ]
            == "BID"
        ).sum()
    )

    organic_events = int(
        (
            evidence_frame[
                "organic_trade"
            ]
            == 1
        ).sum()
    )

    summary = {
        "status":
            (
                "PASS"
                if integrity_failures == 0
                else "FAIL"
            ),

        "stage":
            "2A.3_COMPLETED_SALE_EVIDENCE_EXTRACTION",

        "classifier_completed_transactions":
            int(
                len(
                    transactions
                )
            ),

        "synthetic_transactions_excluded":
            int(
                len(
                    synthetic
                )
            ),

        "player_involved_transactions":
            int(
                len(
                    player_sales
                )
            ),

        "evidence_events":
            int(
                len(
                    evidence_frame
                )
            ),

        "ask_evidence_events":
            ask_events,

        "bid_evidence_events":
            bid_events,

        "organic_evidence_events":
            organic_events,

        "organic_transactions":
            int(
                (
                    player_sales[
                        "transaction_class"
                    ]
                    == CLASS_ORGANIC
                ).sum()
            ),

        "invalid_evidence_prices":
            invalid_prices,

        "synthetic_history_leaks":
            synthetic_leaks,

        "integrity_failures":
            int(
                integrity_failures
            ),

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

    player_sales.to_csv(
        PLAYER_SALES_CSV,
        index=False,
    )

    evidence_frame.to_csv(
        EVIDENCE_CSV,
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
        " Phase 2A.3 Completed-Sale "
        "Evidence Extraction"
    )
    print("=" * 84)

    print()
    print(
        f"Classifier transactions:         "
        f"{len(transactions):>6}"
    )

    print(
        f"Synthetic rows excluded:         "
        f"{len(synthetic):>6}"
    )

    print(
        f"Player-involved transactions:    "
        f"{len(player_sales):>6}"
    )

    print()
    print(
        f"Evidence events:                 "
        f"{len(evidence_frame):>6}"
    )

    print(
        f"  ASK evidence:                  "
        f"{ask_events:>6}"
    )

    print(
        f"  BID evidence:                  "
        f"{bid_events:>6}"
    )

    print(
        f"  Organic evidence events:       "
        f"{organic_events:>6}"
    )

    print()
    print("Integrity:")

    print(
        f"  Invalid evidence prices:       "
        f"{invalid_prices:>6}"
    )

    print(
        f"  Synthetic history leaks:       "
        f"{synthetic_leaks:>6}"
    )

    print(
        f"  Total integrity failures:      "
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
        f"Player sales: {PLAYER_SALES_CSV}"
    )

    print(
        f"Evidence:     {EVIDENCE_CSV}"
    )

    print(
        f"Summary:      {SUMMARY_JSON}"
    )

    print()

    if integrity_failures:
        print("FAIL")

        raise MarketHistoryEvidenceError(
            "Evidence extraction found "
            f"{integrity_failures} "
            "integrity failures."
        )

    print("PASS")

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
