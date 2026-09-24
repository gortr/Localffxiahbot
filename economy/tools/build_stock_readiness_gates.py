from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
REPORTS = ROOT / "economy" / "reports"

MATRIX_CSV = REPORTS / "market-maturity-matrix.csv"
MATRIX_SUMMARY = REPORTS / "market-maturity-summary.json"

POLICY_CSV = REPORTS / "safe-stock-policy.csv"
POLICY_SUMMARY = REPORTS / "safe-stock-policy-summary.json"

OUTPUT_CSV = REPORTS / "stock-readiness-gates.csv"
SUMMARY_JSON = REPORTS / "stock-readiness-gates-summary.json"


GATE_STATES = (
    "HOLD_SEED",
    "HOLD_SUPPLY_NOT_PERSISTENT",
    "HOLD_HISTORY_NOT_ESTABLISHED",
    "HOLD_HISTORY_NOT_RECENT",
    "HOLD_STABLE_MARKET",
    "OBSERVE_TRANSIENT_PLAYER_SUPPLY",
    "HOLD_SUSTAINED_PLAYER_SUPPLY",
    "REVIEW_PERSISTENT_BOT_OVERHANG",
)


class StockReadinessError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise StockReadinessError(
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
        raise StockReadinessError(
            f"{name} is not PASS."
        )

    if summary.get(
        "integrity_failures",
        0,
    ) != 0:
        raise StockReadinessError(
            f"{name} contains integrity failures."
        )


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


def as_float(value: Any) -> float:
    if value is None:
        return 0.0

    try:
        if pd.isna(value):
            return 0.0
    except TypeError:
        pass

    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass

    value = str(value).strip()

    if value.lower() in {
        "",
        "none",
        "nan",
        "null",
    }:
        return ""

    return value


def gate_state(
    row: pd.Series,
    *,
    active_listing_review_eligible: int,
) -> str:
    maturity = clean_text(
        row.get("market_maturity")
    )

    pressure = clean_text(
        row.get("pressure_state")
    )

    history = clean_text(
        row.get("evidence_maturity")
    )

    freshness = clean_text(
        row.get("freshness_state")
    )

    persistence_ready = as_int(
        row.get("persistence_ready")
    )

    if maturity == "SEED":
        return "HOLD_SEED"

    if not persistence_ready:
        return "HOLD_SUPPLY_NOT_PERSISTENT"

    if active_listing_review_eligible:
        return "REVIEW_PERSISTENT_BOT_OVERHANG"

    if history not in {
        "ESTABLISHED",
        "TRUSTED",
    }:
        return "HOLD_HISTORY_NOT_ESTABLISHED"

    if freshness != "RECENT":
        return "HOLD_HISTORY_NOT_RECENT"

    if pressure == "TRANSIENT_PLAYER_SUPPLY":
        return "OBSERVE_TRANSIENT_PLAYER_SUPPLY"

    if pressure == "SUSTAINED_PLAYER_SUPPLY":
        return "HOLD_SUSTAINED_PLAYER_SUPPLY"

    if pressure == "NO_PLAYER_PRESSURE":
        return "HOLD_STABLE_MARKET"

    raise StockReadinessError(
        "Unexpected pressure state after "
        "readiness gates: "
        f"{pressure!r}"
    )


def main() -> int:
    matrix_summary = load_json(
        MATRIX_SUMMARY
    )

    policy_summary = load_json(
        POLICY_SUMMARY
    )

    require_pass(
        "2C.1 maturity matrix",
        matrix_summary,
    )

    require_pass(
        "2B.3 safe stock policy",
        policy_summary,
    )

    for summary_name, summary in (
        ("maturity", matrix_summary),
        ("policy", policy_summary),
    ):
        for field in (
            "stock_change_authorized",
            "active_listing_change_authorized",
            "activation_ready",
            "auto_live_promotions",
        ):
            if summary.get(field, 0) != 0:
                raise StockReadinessError(
                    f"{summary_name} unsafe state: "
                    f"{field}={summary.get(field)}"
                )

    if not MATRIX_CSV.exists():
        raise StockReadinessError(
            f"Missing maturity matrix: {MATRIX_CSV}"
        )

    if not POLICY_CSV.exists():
        raise StockReadinessError(
            f"Missing stock policy: {POLICY_CSV}"
        )

    matrix = pd.read_csv(
        MATRIX_CSV
    )

    policy = pd.read_csv(
        POLICY_CSV
    )

    expected_forms = int(
        matrix_summary[
            "configured_item_forms"
        ]
    )

    if len(matrix) != expected_forms:
        raise StockReadinessError(
            "Maturity matrix row count "
            "does not match summary."
        )

    if len(policy) != expected_forms:
        raise StockReadinessError(
            "Stock policy row count "
            "does not match maturity matrix."
        )

    policy_view = policy[
        [
            "itemid",
            "stack",
            "policy_action",
            "current_market_target",
            "recommended_market_target",
            "recommended_target_delta",
            "active_listing_review_eligible",
            "stock_change_authorized",
            "active_listing_change_authorized",
        ]
    ].copy()

    combined = matrix.merge(
        policy_view,
        on=[
            "itemid",
            "stack",
        ],
        how="left",
        validate="one_to_one",
    )

    if combined[
        "policy_action"
    ].isna().any():
        raise StockReadinessError(
            "One or more maturity rows lack "
            "2B.3 policy data."
        )

    rows: list[dict[str, Any]] = []

    for _, row in combined.iterrows():
        persistence_ready = int(
            as_int(
                row.get(
                    "persistence_ready"
                )
            )
            == 1
        )

        market_maturity = clean_text(
            row.get(
                "market_maturity"
            )
        )

        history_maturity = clean_text(
            row.get(
                "evidence_maturity"
            )
        )

        freshness = clean_text(
            row.get(
                "freshness_state"
            )
        )

        pressure = clean_text(
            row.get(
                "pressure_state"
            )
        )

        player_presence_ratio = as_float(
            row.get(
                "player_presence_ratio"
            )
        )

        bot_overhang_persistent = int(
            as_int(
                row.get(
                    "bot_overhang_persistent"
                )
            )
            == 1
        )

        policy_listing_review = int(
            as_int(
                row.get(
                    "active_listing_review_eligible"
                )
            )
            == 1
        )

        matrix_stock_candidate = int(
            as_int(
                row.get(
                    "stock_readiness_candidate"
                )
            )
            == 1
        )

        gate_supply_persistence = (
            persistence_ready
        )

        gate_market_maturity = int(
            market_maturity
            in {
                "ESTABLISHED",
                "TRUSTED_OBSERVATION",
            }
        )

        gate_history_maturity = int(
            history_maturity
            in {
                "ESTABLISHED",
                "TRUSTED",
            }
        )

        gate_recent_history = int(
            freshness == "RECENT"
        )

        gate_matrix_stock_candidate = (
            matrix_stock_candidate
        )

        gate_target_policy_stable = int(
            as_int(
                row.get(
                    "recommended_target_delta"
                )
            )
            == 0
        )

        target_gate_values = (
            gate_supply_persistence,
            gate_market_maturity,
            gate_history_maturity,
            gate_recent_history,
            gate_matrix_stock_candidate,
            gate_target_policy_stable,
        )

        stock_target_signal_review_eligible = int(
            all(
                value == 1
                for value in target_gate_values
            )
        )

        gate_player_supply_present = int(
            player_presence_ratio > 0
        )

        gate_bot_overhang_persistent = (
            bot_overhang_persistent
        )

        gate_policy_listing_review = (
            policy_listing_review
        )

        listing_gate_values = (
            gate_supply_persistence,
            gate_player_supply_present,
            gate_bot_overhang_persistent,
            gate_policy_listing_review,
        )

        active_listing_review_eligible = int(
            all(
                value == 1
                for value in listing_gate_values
            )
        )

        failed_target_gates = []

        for gate_name, value in (
            (
                "SUPPLY_PERSISTENCE",
                gate_supply_persistence,
            ),
            (
                "MARKET_MATURITY",
                gate_market_maturity,
            ),
            (
                "HISTORY_MATURITY",
                gate_history_maturity,
            ),
            (
                "RECENT_HISTORY",
                gate_recent_history,
            ),
            (
                "MATRIX_STOCK_CANDIDATE",
                gate_matrix_stock_candidate,
            ),
            (
                "TARGET_POLICY_STABLE",
                gate_target_policy_stable,
            ),
        ):
            if value != 1:
                failed_target_gates.append(
                    gate_name
                )

        failed_listing_gates = []

        for gate_name, value in (
            (
                "SUPPLY_PERSISTENCE",
                gate_supply_persistence,
            ),
            (
                "PLAYER_SUPPLY_PRESENT",
                gate_player_supply_present,
            ),
            (
                "PERSISTENT_BOT_OVERHANG",
                gate_bot_overhang_persistent,
            ),
            (
                "POLICY_LISTING_REVIEW",
                gate_policy_listing_review,
            ),
        ):
            if value != 1:
                failed_listing_gates.append(
                    gate_name
                )

        # Matrix stock readiness and the detailed
        # stock-target gate must remain equivalent.
        target_readiness_consistent = int(
            stock_target_signal_review_eligible
            == matrix_stock_candidate
        )

        # Policy and detailed active-listing gates
        # must remain equivalent.
        listing_readiness_consistent = int(
            active_listing_review_eligible
            == policy_listing_review
        )

        state = gate_state(
            row,
            active_listing_review_eligible=(
                active_listing_review_eligible
            ),
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

            "current_market_target":
                as_int(
                    row.get(
                        "current_market_target"
                    )
                ),

            "recommended_market_target":
                as_int(
                    row.get(
                        "recommended_market_target"
                    )
                ),

            "market_maturity":
                market_maturity,

            "supply_readiness":
                clean_text(
                    row.get(
                        "supply_readiness"
                    )
                ),

            "pressure_state":
                pressure,

            "evidence_maturity":
                history_maturity,

            "freshness_state":
                freshness,

            "player_presence_ratio":
                player_presence_ratio,

            "player_target_persistent":
                as_int(
                    row.get(
                        "player_target_persistent"
                    )
                ),

            "bot_overhang_persistent":
                bot_overhang_persistent,

            "policy_action":
                clean_text(
                    row.get(
                        "policy_action"
                    )
                ),

            "gate_supply_persistence":
                gate_supply_persistence,

            "gate_market_maturity":
                gate_market_maturity,

            "gate_history_maturity":
                gate_history_maturity,

            "gate_recent_history":
                gate_recent_history,

            "gate_matrix_stock_candidate":
                gate_matrix_stock_candidate,

            "gate_target_policy_stable":
                gate_target_policy_stable,

            "failed_target_gate_count":
                len(
                    failed_target_gates
                ),

            "failed_target_gates":
                ";".join(
                    failed_target_gates
                ),

            "stock_target_signal_review_eligible":
                stock_target_signal_review_eligible,

            "gate_player_supply_present":
                gate_player_supply_present,

            "gate_bot_overhang_persistent":
                gate_bot_overhang_persistent,

            "gate_policy_listing_review":
                gate_policy_listing_review,

            "failed_listing_gate_count":
                len(
                    failed_listing_gates
                ),

            "failed_listing_gates":
                ";".join(
                    failed_listing_gates
                ),

            "active_listing_review_eligible":
                active_listing_review_eligible,

            "stock_gate_state":
                state,

            "target_readiness_consistent":
                target_readiness_consistent,

            "listing_readiness_consistent":
                listing_readiness_consistent,

            # Review eligibility never equals
            # mutation authority.
            "stock_signal_use_authorized":
                0,

            "stock_target_change_authorized":
                0,

            "active_listing_change_authorized":
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
                    "stock_gate_state"
                ]
                == state
            ).sum()
        )
        for state in GATE_STATES
    }

    target_review_forms = int(
        gates[
            "stock_target_signal_review_eligible"
        ].sum()
    )

    listing_review_forms = int(
        gates[
            "active_listing_review_eligible"
        ].sum()
    )

    target_inconsistencies = int(
        (
            gates[
                "target_readiness_consistent"
            ]
            != 1
        ).sum()
    )

    listing_inconsistencies = int(
        (
            gates[
                "listing_readiness_consistent"
            ]
            != 1
        ).sum()
    )

    integrity_failures = (
        target_inconsistencies
        + listing_inconsistencies
    )

    if sum(
        state_counts.values()
    ) != len(gates):
        integrity_failures += 1

    safety_columns = (
        "stock_signal_use_authorized",
        "stock_target_change_authorized",
        "active_listing_change_authorized",
        "activation_ready",
        "auto_live_promotion",
    )

    for column in safety_columns:
        if (
            gates[column]
            != 0
        ).any():
            integrity_failures += 1

    summary = {
        "status":
            (
                "PASS"
                if integrity_failures == 0
                else "FAIL"
            ),

        "stage":
            "2C.3_STOCK_READINESS_GATES",

        "item_forms_evaluated":
            int(len(gates)),

        "stock_gate_state_counts":
            state_counts,

        "stock_target_signal_review_eligible_forms":
            target_review_forms,

        "active_listing_review_eligible_forms":
            listing_review_forms,

        "target_readiness_inconsistent_rows":
            target_inconsistencies,

        "listing_readiness_inconsistent_rows":
            listing_inconsistencies,

        "integrity_failures":
            integrity_failures,

        "stock_signal_use_authorized":
            0,

        "stock_target_change_authorized":
            0,

        "active_listing_change_authorized":
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
        " Phase 2C.3 Stock-Readiness Gates"
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
            f"  {state:<36}"
            f"{state_counts[state]:>6}"
        )

    print()
    print(
        f"Stock-target signal review:      "
        f"{target_review_forms:>6}"
    )

    print(
        f"Active-listing review:           "
        f"{listing_review_forms:>6}"
    )

    print()
    print(
        f"Target readiness inconsistencies:"
        f"{target_inconsistencies:>6}"
    )

    print(
        f"Listing readiness inconsistencies:"
        f"{listing_inconsistencies:>5}"
    )

    print()
    print(
        f"Integrity failures:              "
        f"{integrity_failures:>6}"
    )

    print()
    print(
        "Stock signal use authorized:        0"
    )

    print(
        "Stock target change authorized:     0"
    )

    print(
        "Active listing change authorized:   0"
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

        raise StockReadinessError(
            "2C.3 stock-readiness "
            "validation failed."
        )

    print("PASS")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
