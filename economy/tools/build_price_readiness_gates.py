from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
REPORTS = ROOT / "economy" / "reports"

MATRIX_CSV = (
    REPORTS
    / "market-maturity-matrix.csv"
)

MATRIX_SUMMARY = (
    REPORTS
    / "market-maturity-summary.json"
)

CONFIDENCE_SUMMARY = (
    REPORTS
    / "market-history-confidence-summary.json"
)

OUTPUT_CSV = (
    REPORTS
    / "price-readiness-gates.csv"
)

SUMMARY_JSON = (
    REPORTS
    / "price-readiness-gates-summary.json"
)


GATE_STATES = (
    "HOLD_SEED",
    "HOLD_NO_HISTORY",
    "HOLD_SPARSE_HISTORY",
    "HOLD_HISTORY_NOT_RECENT",
    "HOLD_DEVELOPING",
    "HOLD_ESTABLISHED",
    "REVIEW_TRUSTED_SIGNAL",
)


class PriceReadinessError(RuntimeError):
    pass


def load_json(
    path: Path,
) -> dict[str, Any]:
    if not path.exists():
        raise PriceReadinessError(
            f"Missing required summary: {path}"
        )

    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


def require_pass(
    name: str,
    summary: dict[str, Any],
) -> None:
    if summary.get("status") != "PASS":
        raise PriceReadinessError(
            f"{name} is not PASS."
        )

    if summary.get(
        "integrity_failures",
        0,
    ) != 0:
        raise PriceReadinessError(
            f"{name} contains integrity failures."
        )


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
    except (TypeError, ValueError):
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

    text = str(value).strip()

    if text.lower() in {
        "",
        "none",
        "nan",
        "null",
    }:
        return ""

    return text


def gate_state(
    row: pd.Series,
) -> str:
    maturity = clean_text(
        row.get("market_maturity")
    )

    history = clean_text(
        row.get("evidence_maturity")
    )

    freshness = clean_text(
        row.get("freshness_state")
    )

    if maturity == "SEED":
        return "HOLD_SEED"

    if history in {
        "",
        "NO_DATA",
    }:
        return "HOLD_NO_HISTORY"

    if history == "SPARSE":
        return "HOLD_SPARSE_HISTORY"

    if freshness != "RECENT":
        return "HOLD_HISTORY_NOT_RECENT"

    if maturity == "DEVELOPING":
        return "HOLD_DEVELOPING"

    if maturity == "ESTABLISHED":
        return "HOLD_ESTABLISHED"

    if maturity == "TRUSTED_OBSERVATION":
        return "REVIEW_TRUSTED_SIGNAL"

    if maturity == "OBSERVING":
        return "HOLD_NO_HISTORY"

    raise PriceReadinessError(
        "Unexpected market maturity: "
        f"{maturity!r}"
    )


def main() -> int:
    matrix_summary = load_json(
        MATRIX_SUMMARY
    )

    confidence_summary = load_json(
        CONFIDENCE_SUMMARY
    )

    require_pass(
        "2C.1 maturity matrix",
        matrix_summary,
    )

    require_pass(
        "2A.5 confidence",
        confidence_summary,
    )

    for field in (
        "price_change_authorized",
        "stock_change_authorized",
        "active_listing_change_authorized",
        "activation_ready",
        "auto_live_promotions",
    ):
        if matrix_summary.get(
            field,
            0,
        ) != 0:
            raise PriceReadinessError(
                "Unsafe maturity matrix state: "
                f"{field}="
                f"{matrix_summary.get(field)}"
            )

    if not MATRIX_CSV.exists():
        raise PriceReadinessError(
            f"Missing maturity matrix: {MATRIX_CSV}"
        )

    matrix = pd.read_csv(
        MATRIX_CSV
    )

    expected_forms = int(
        matrix_summary[
            "configured_item_forms"
        ]
    )

    if len(matrix) != expected_forms:
        raise PriceReadinessError(
            "Maturity matrix row count "
            "does not match summary."
        )

    thresholds = confidence_summary.get(
        "thresholds",
        {},
    )

    trusted = thresholds.get(
        "TRUSTED",
        {},
    )

    required_transactions = int(
        trusted.get(
            "player_transactions",
            25,
        )
    )

    required_organic = int(
        trusted.get(
            "organic_transactions",
            10,
        )
    )

    required_asks = int(
        trusted.get(
            "ask_events",
            8,
        )
    )

    required_bids = int(
        trusted.get(
            "bid_events",
            8,
        )
    )

    required_durations = int(
        trusted.get(
            "valid_player_listing_durations",
            5,
        )
    )

    rows: list[dict[str, Any]] = []

    for _, row in matrix.iterrows():
        player_transactions = as_int(
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

        gate_supply_persistence = int(
            as_int(
                row.get(
                    "persistence_ready"
                )
            )
            == 1
        )

        gate_trusted_history = int(
            clean_text(
                row.get(
                    "evidence_maturity"
                )
            )
            == "TRUSTED"
        )

        gate_recent_history = int(
            clean_text(
                row.get(
                    "freshness_state"
                )
            )
            == "RECENT"
        )

        gate_player_transactions = int(
            player_transactions
            >= required_transactions
        )

        gate_organic_transactions = int(
            organic
            >= required_organic
        )

        gate_ask_events = int(
            asks
            >= required_asks
        )

        gate_bid_events = int(
            bids
            >= required_bids
        )

        gate_listing_durations = int(
            durations
            >= required_durations
        )

        gate_trusted_and_recent = int(
            as_int(
                row.get(
                    "trusted_and_recent"
                )
            )
            == 1
        )

        gate_market_maturity = int(
            clean_text(
                row.get(
                    "market_maturity"
                )
            )
            == "TRUSTED_OBSERVATION"
        )

        gate_matrix_candidate = int(
            as_int(
                row.get(
                    "price_readiness_candidate"
                )
            )
            == 1
        )

        gate_values = (
            gate_supply_persistence,
            gate_trusted_history,
            gate_recent_history,
            gate_player_transactions,
            gate_organic_transactions,
            gate_ask_events,
            gate_bid_events,
            gate_listing_durations,
            gate_trusted_and_recent,
            gate_market_maturity,
            gate_matrix_candidate,
        )

        failed_gate_count = int(
            sum(
                1
                for value in gate_values
                if value != 1
            )
        )

        review_eligible = int(
            failed_gate_count == 0
        )

        state = gate_state(
            row
        )

        # TRUSTED_OBSERVATION should be internally
        # equivalent to passing all readiness gates.
        readiness_consistent = int(
            (
                state
                == "REVIEW_TRUSTED_SIGNAL"
            )
            == bool(
                review_eligible
            )
        )

        failed_names = []

        named_gates = (
            (
                "SUPPLY_PERSISTENCE",
                gate_supply_persistence,
            ),
            (
                "TRUSTED_HISTORY",
                gate_trusted_history,
            ),
            (
                "RECENT_HISTORY",
                gate_recent_history,
            ),
            (
                "PLAYER_TRANSACTION_COUNT",
                gate_player_transactions,
            ),
            (
                "ORGANIC_TRANSACTION_COUNT",
                gate_organic_transactions,
            ),
            (
                "ASK_EVIDENCE_COUNT",
                gate_ask_events,
            ),
            (
                "BID_EVIDENCE_COUNT",
                gate_bid_events,
            ),
            (
                "LISTING_DURATION_COUNT",
                gate_listing_durations,
            ),
            (
                "TRUSTED_AND_RECENT",
                gate_trusted_and_recent,
            ),
            (
                "TRUSTED_OBSERVATION",
                gate_market_maturity,
            ),
            (
                "MATRIX_PRICE_CANDIDATE",
                gate_matrix_candidate,
            ),
        )

        for name, value in named_gates:
            if value != 1:
                failed_names.append(
                    name
                )

        rows.append({
            "itemid":
                as_int(
                    row.get("itemid")
                ),

            "name":
                clean_text(
                    row.get("name")
                ),

            "stack":
                as_int(
                    row.get("stack")
                ),

            "market_maturity":
                clean_text(
                    row.get(
                        "market_maturity"
                    )
                ),

            "supply_readiness":
                clean_text(
                    row.get(
                        "supply_readiness"
                    )
                ),

            "evidence_maturity":
                clean_text(
                    row.get(
                        "evidence_maturity"
                    )
                ),

            "freshness_state":
                clean_text(
                    row.get(
                        "freshness_state"
                    )
                ),

            "player_involved_transactions":
                player_transactions,

            "organic_transactions":
                organic,

            "ask_evidence_events":
                asks,

            "bid_evidence_events":
                bids,

            "valid_player_listing_durations":
                durations,

            "gate_supply_persistence":
                gate_supply_persistence,

            "gate_trusted_history":
                gate_trusted_history,

            "gate_recent_history":
                gate_recent_history,

            "gate_player_transactions":
                gate_player_transactions,

            "gate_organic_transactions":
                gate_organic_transactions,

            "gate_ask_events":
                gate_ask_events,

            "gate_bid_events":
                gate_bid_events,

            "gate_listing_durations":
                gate_listing_durations,

            "gate_trusted_and_recent":
                gate_trusted_and_recent,

            "gate_market_maturity":
                gate_market_maturity,

            "gate_matrix_candidate":
                gate_matrix_candidate,

            "failed_gate_count":
                failed_gate_count,

            "failed_gates":
                ";".join(
                    failed_names
                ),

            "price_gate_state":
                state,

            "price_signal_review_eligible":
                review_eligible,

            "readiness_consistent":
                readiness_consistent,

            # Eligibility never equals authority.
            "price_signal_use_authorized":
                0,

            "price_change_authorized":
                0,

            "activation_ready":
                0,

            "auto_live_promotion":
                0,
        })

    gates = pd.DataFrame(
        rows
    )

    state_counts = {
        state: int(
            (
                gates[
                    "price_gate_state"
                ]
                == state
            ).sum()
        )
        for state in GATE_STATES
    }

    review_eligible_forms = int(
        gates[
            "price_signal_review_eligible"
        ].sum()
    )

    inconsistent_rows = int(
        (
            gates[
                "readiness_consistent"
            ]
            != 1
        ).sum()
    )

    integrity_failures = (
        inconsistent_rows
    )

    safety_columns = (
        "price_signal_use_authorized",
        "price_change_authorized",
        "activation_ready",
        "auto_live_promotion",
    )

    for column in safety_columns:
        if (
            gates[column]
            != 0
        ).any():
            integrity_failures += 1

    if sum(
        state_counts.values()
    ) != len(gates):
        integrity_failures += 1

    summary = {
        "status":
            (
                "PASS"
                if integrity_failures == 0
                else "FAIL"
            ),

        "stage":
            "2C.2_PRICE_READINESS_GATES",

        "item_forms_evaluated":
            int(len(gates)),

        "price_gate_state_counts":
            state_counts,

        "price_signal_review_eligible_forms":
            review_eligible_forms,

        "readiness_inconsistent_rows":
            inconsistent_rows,

        "trusted_thresholds": {
            "player_transactions":
                required_transactions,

            "organic_transactions":
                required_organic,

            "ask_events":
                required_asks,

            "bid_events":
                required_bids,

            "valid_player_listing_durations":
                required_durations,
        },

        "integrity_failures":
            integrity_failures,

        "price_signal_use_authorized":
            0,

        "price_change_authorized":
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

    gates.to_csv(
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
        " Phase 2C.2 Price-Readiness Gates"
    )
    print("=" * 84)

    print()
    print(
        f"Item forms evaluated:            "
        f"{len(gates):>6}"
    )

    print()
    print("Gate states:")

    for state in GATE_STATES:
        print(
            f"  {state:<30}"
            f"{state_counts[state]:>6}"
        )

    print()
    print(
        f"Price-signal review eligible:    "
        f"{review_eligible_forms:>6}"
    )

    print(
        f"Readiness inconsistencies:       "
        f"{inconsistent_rows:>6}"
    )

    print()
    print("Trusted thresholds:")

    print(
        f"  Player transactions:           "
        f"{required_transactions:>6}"
    )

    print(
        f"  Organic transactions:          "
        f"{required_organic:>6}"
    )

    print(
        f"  ASK events:                    "
        f"{required_asks:>6}"
    )

    print(
        f"  BID events:                    "
        f"{required_bids:>6}"
    )

    print(
        f"  Listing durations:             "
        f"{required_durations:>6}"
    )

    print()
    print(
        f"Integrity failures:              "
        f"{integrity_failures:>6}"
    )

    print()
    print(
        "Price signal use authorized:        0"
    )

    print(
        "Price change authorized:            0"
    )

    print(
        "Activation ready:                   0"
    )

    print(
        "Auto live promotions:               0"
    )

    print()
    print(
        f"Gates:   {OUTPUT_CSV}"
    )

    print(
        f"Summary: {SUMMARY_JSON}"
    )

    print()

    if integrity_failures:
        print("FAIL")

        raise PriceReadinessError(
            "2C.2 price-readiness "
            "validation failed."
        )

    print("PASS")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
