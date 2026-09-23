from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
REPORTS = ROOT / "economy" / "reports"

CLASSIFIER_SUMMARY = REPORTS / "market-history-classifier-summary.json"
EXTRACTION_SUMMARY = REPORTS / "market-history-extraction-summary.json"
INTELLIGENCE_SUMMARY = REPORTS / "market-history-intelligence-summary.json"
CONFIDENCE_SUMMARY = REPORTS / "market-history-confidence-summary.json"

INTELLIGENCE_CSV = REPORTS / "market-history-item-intelligence.csv"
CONFIDENCE_CSV = REPORTS / "market-history-confidence.csv"

REVIEW_CSV = REPORTS / "market-history-review.csv"
REPORT_MD = REPORTS / "market-history-report.md"
SUMMARY_JSON = REPORTS / "market-history-report-summary.json"


class MarketHistoryReportError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise MarketHistoryReportError(
            f"Missing required report: {path}"
        )

    return json.loads(
        path.read_text(encoding="utf-8")
    )


def require_pass(
    name: str,
    summary: dict[str, Any],
) -> None:
    if summary.get("status") != "PASS":
        raise MarketHistoryReportError(
            f"{name} is not PASS."
        )

    if summary.get("integrity_failures", 0) != 0:
        raise MarketHistoryReportError(
            f"{name} contains integrity failures."
        )

    for field in (
        "history_price_influence_ready",
        "history_stock_influence_ready",
        "activation_ready",
        "auto_live_promotions",
    ):
        if summary.get(field, 0) != 0:
            raise MarketHistoryReportError(
                f"{name} has unsafe {field}="
                f"{summary.get(field)}"
            )


def display_value(value: Any) -> str:
    if value is None:
        return "-"

    try:
        if pd.isna(value):
            return "-"
    except TypeError:
        pass

    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}"

    return str(value)


def main() -> int:
    classifier = load_json(CLASSIFIER_SUMMARY)
    extraction = load_json(EXTRACTION_SUMMARY)
    intelligence_summary = load_json(INTELLIGENCE_SUMMARY)
    confidence_summary = load_json(CONFIDENCE_SUMMARY)

    require_pass("2A.2 classifier", classifier)
    require_pass("2A.3 extraction", extraction)
    require_pass("2A.4 intelligence", intelligence_summary)
    require_pass("2A.5 confidence", confidence_summary)

    if not INTELLIGENCE_CSV.exists():
        raise MarketHistoryReportError(
            f"Missing intelligence CSV: {INTELLIGENCE_CSV}"
        )

    if not CONFIDENCE_CSV.exists():
        raise MarketHistoryReportError(
            f"Missing confidence CSV: {CONFIDENCE_CSV}"
        )

    intelligence = pd.read_csv(INTELLIGENCE_CSV)
    confidence = pd.read_csv(CONFIDENCE_CSV)

    review = confidence.merge(
        intelligence,
        on=["itemid", "stack"],
        how="outer",
        suffixes=("_confidence", "_intelligence"),
        validate="one_to_one",
    )

    if len(review) != int(
        confidence_summary["item_forms_evaluated"]
    ):
        raise MarketHistoryReportError(
            "Review row count does not match 2A.5."
        )

    preferred_columns = [
        "itemid",
        "stack",
        "evidence_maturity",
        "freshness_state",
        "player_involved_transactions_confidence",
        "organic_transactions_confidence",
        "ask_evidence_events_confidence",
        "bid_evidence_events_confidence",
        "valid_player_listing_durations_confidence",
        "ask_min",
        "ask_median",
        "ask_mean",
        "ask_max",
        "bid_min",
        "bid_median",
        "bid_mean",
        "bid_max",
        "listing_duration_min_seconds",
        "listing_duration_median_seconds",
        "listing_duration_max_seconds",
        "days_since_latest_player_event_confidence",
        "sparse_history",
        "no_organic_history",
        "one_sided_evidence",
        "stale_history",
        "trusted_and_recent",
        "trusted_shortfalls",
        "history_price_influence_ready_confidence",
        "history_stock_influence_ready_confidence",
        "activation_ready_confidence",
        "auto_live_promotion_confidence",
    ]

    existing_columns = [
        column
        for column in preferred_columns
        if column in review.columns
    ]

    review = review[
        existing_columns
    ].copy()

    maturity_order = {
        "NO_DATA": 0,
        "SPARSE": 1,
        "DEVELOPING": 2,
        "ESTABLISHED": 3,
        "TRUSTED": 4,
    }

    review["_maturity_sort"] = (
        review["evidence_maturity"]
        .map(maturity_order)
        .fillna(-1)
    )

    review = (
        review
        .sort_values(
            [
                "_maturity_sort",
                "itemid",
                "stack",
            ],
            ascending=[
                False,
                True,
                True,
            ],
        )
        .drop(
            columns=["_maturity_sort"]
        )
    )

    REVIEW_CSV.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    review.to_csv(
        REVIEW_CSV,
        index=False,
    )

    maturity_counts = confidence_summary[
        "maturity_counts"
    ]

    freshness_counts = confidence_summary[
        "freshness_counts"
    ]

    lines: list[str] = []

    lines.append("# FFXI AHBot Market History Intelligence")
    lines.append("")
    lines.append(
        f"Generated: "
        f"{datetime.now(timezone.utc).isoformat()}"
    )
    lines.append("")

    lines.append("## Safety State")
    lines.append("")
    lines.append("- History price influence: **DISABLED**")
    lines.append("- History stock influence: **DISABLED**")
    lines.append("- Runtime activation: **DISABLED**")
    lines.append("- Automatic live promotion: **DISABLED**")
    lines.append("")

    lines.append("## Transaction Census")
    lines.append("")
    lines.append(
        f"- Completed transactions: "
        f"{classifier['completed_transactions']}"
    )
    lines.append(
        f"- Synthetic AHBot history: "
        f"{classifier['synthetic_history_rows']}"
    )
    lines.append(
        f"- Player supply to AHBot: "
        f"{classifier['player_supply_to_bot_rows']}"
    )
    lines.append(
        f"- Player demand from AHBot: "
        f"{classifier['player_demand_from_bot_rows']}"
    )
    lines.append(
        f"- Organic player trades: "
        f"{classifier['organic_player_trade_rows']}"
    )
    lines.append(
        f"- Unclassified transactions: "
        f"{classifier['unclassified_rows']}"
    )
    lines.append("")

    lines.append("## Evidence")
    lines.append("")
    lines.append(
        f"- Player-involved transactions: "
        f"{extraction['player_involved_transactions']}"
    )
    lines.append(
        f"- ASK evidence events: "
        f"{extraction['ask_evidence_events']}"
    )
    lines.append(
        f"- BID evidence events: "
        f"{extraction['bid_evidence_events']}"
    )
    lines.append(
        f"- Organic evidence events: "
        f"{extraction['organic_evidence_events']}"
    )
    lines.append(
        f"- Synthetic history leaks: "
        f"{extraction['synthetic_history_leaks']}"
    )
    lines.append("")

    lines.append("## Maturity")
    lines.append("")

    for tier in (
        "NO_DATA",
        "SPARSE",
        "DEVELOPING",
        "ESTABLISHED",
        "TRUSTED",
    ):
        lines.append(
            f"- {tier}: "
            f"{maturity_counts.get(tier, 0)}"
        )

    lines.append("")
    lines.append("## Freshness")
    lines.append("")

    for state in (
        "RECENT",
        "AGING",
        "STALE",
    ):
        lines.append(
            f"- {state}: "
            f"{freshness_counts.get(state, 0)}"
        )

    lines.append("")
    lines.append("## Item-Form Review")
    lines.append("")

    if review.empty:
        lines.append(
            "No player-derived market history exists yet."
        )
    else:
        for _, row in review.iterrows():
            itemid = int(row["itemid"])
            stack = int(row["stack"])

            maturity = display_value(
                row.get("evidence_maturity")
            )

            freshness = display_value(
                row.get("freshness_state")
            )

            asks = display_value(
                row.get(
                    "ask_evidence_events_confidence"
                )
            )

            bids = display_value(
                row.get(
                    "bid_evidence_events_confidence"
                )
            )

            organic = display_value(
                row.get(
                    "organic_transactions_confidence"
                )
            )

            ask_median = display_value(
                row.get("ask_median")
            )

            bid_median = display_value(
                row.get("bid_median")
            )

            age = display_value(
                row.get(
                    "days_since_latest_player_event_confidence"
                )
            )

            shortfalls = display_value(
                row.get("trusted_shortfalls")
            )

            lines.append(
                f"### Item {itemid} / stack={stack}"
            )
            lines.append("")
            lines.append(
                f"- Maturity: **{maturity}**"
            )
            lines.append(
                f"- Freshness: **{freshness}**"
            )
            lines.append(
                f"- ASK events: {asks}"
            )
            lines.append(
                f"- BID events: {bids}"
            )
            lines.append(
                f"- Organic trades: {organic}"
            )
            lines.append(
                f"- ASK median: {ask_median}"
            )
            lines.append(
                f"- BID median: {bid_median}"
            )
            lines.append(
                f"- Days since latest player event: "
                f"{age}"
            )
            lines.append(
                f"- TRUSTED shortfalls: {shortfalls}"
            )
            lines.append("")

    lines.append("## Interpretation Rules")
    lines.append("")
    lines.append(
        "- AHBot-to-AHBot synthetic history is excluded."
    )
    lines.append(
        "- Player-to-AHBot contributes player ASK/supply evidence only."
    )
    lines.append(
        "- AHBot-to-player contributes player BID/demand evidence only."
    )
    lines.append(
        "- Player-to-player trades contribute both ASK and BID evidence."
    )
    lines.append(
        "- Evidence maturity is observational and does not grant deployment authority."
    )
    lines.append("")

    REPORT_MD.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    integrity_failures = 0

    if classifier["integrity_failures"] != 0:
        integrity_failures += 1

    if extraction["integrity_failures"] != 0:
        integrity_failures += 1

    if intelligence_summary["integrity_failures"] != 0:
        integrity_failures += 1

    if confidence_summary["integrity_failures"] != 0:
        integrity_failures += 1

    summary = {
        "status":
            "PASS"
            if integrity_failures == 0
            else "FAIL",

        "stage":
            "2A.6_HISTORY_INTELLIGENCE_REPORT",

        "completed_transactions":
            classifier[
                "completed_transactions"
            ],

        "synthetic_transactions_excluded":
            extraction[
                "synthetic_transactions_excluded"
            ],

        "player_involved_transactions":
            extraction[
                "player_involved_transactions"
            ],

        "item_forms_reviewed":
            int(len(review)),

        "trusted_and_recent_rows":
            confidence_summary[
                "trusted_and_recent_rows"
            ],

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
        " Phase 2A.6 Market History "
        "Intelligence Report"
    )
    print("=" * 84)

    print()
    print(
        f"Completed transactions:         "
        f"{summary['completed_transactions']:>6}"
    )

    print(
        f"Synthetic transactions excluded:"
        f"{summary['synthetic_transactions_excluded']:>6}"
    )

    print(
        f"Player-involved transactions:   "
        f"{summary['player_involved_transactions']:>6}"
    )

    print(
        f"Item forms reviewed:            "
        f"{summary['item_forms_reviewed']:>6}"
    )

    print(
        f"Trusted + recent:               "
        f"{summary['trusted_and_recent_rows']:>6}"
    )

    print()
    print(
        f"Integrity failures:             "
        f"{integrity_failures:>6}"
    )

    print()
    print("History price influence ready:       0")
    print("History stock influence ready:       0")
    print("Activation ready:                    0")
    print("Auto live promotions:                0")

    print()
    print(f"Review CSV: {REVIEW_CSV}")
    print(f"Report:     {REPORT_MD}")
    print(f"Summary:    {SUMMARY_JSON}")
    print()

    if integrity_failures:
        print("FAIL")
        raise MarketHistoryReportError(
            "2A.6 report failed integrity validation."
        )

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
