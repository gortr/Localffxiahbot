from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

GENERATED = (
    ROOT
    / "economy"
    / "generated"
)

OBS_ROOT = (
    ROOT
    / "economy"
    / "runtime-observations"
    / "gil-flow"
)

MODEL_ROOT = (
    OBS_ROOT
    / "model"
)

SAFETY_ROOT = (
    OBS_ROOT
    / "safety"
)

BASELINE_JSON = (
    OBS_ROOT
    / "baseline.json"
)

MODEL_SUMMARY = (
    MODEL_ROOT
    / "current-exposure-summary.json"
)

SELLER_RUNTIME = (
    GENERATED
    / "market-sell.csv"
)

BUYER_RUNTIME = (
    GENERATED
    / "market-buy.csv"
)


# --------------------------------------------------
# Phase 3E.5 policy thresholds
#
# These are review thresholds only.
# They do not grant mutation authority.
# --------------------------------------------------

GLOBAL_STRESS_WATCH = 0.25
GLOBAL_STRESS_REVIEW = 1.0 / 3.0

ITEM_SHARE_WATCH = 0.15
ITEM_SHARE_REVIEW = 0.25

TOP5_SHARE_WATCH = 0.50
TOP5_SHARE_REVIEW = 0.70

HHI_WATCH = 0.10
HHI_REVIEW = 0.18

DIRECT_SPREAD_REVIEW = 0.90
DIRECT_SPREAD_BLOCK = 1.00

MIN_ACTUAL_WINDOW_HOURS = 72.0
MIN_ACTUAL_TRANSACTIONS = 10

ACTUAL_NET_INJECTION_WATCH = 0.10
ACTUAL_NET_INJECTION_REVIEW = 0.25


class SafetyGateError(RuntimeError):
    pass


def as_int(value: Any) -> int:
    if value is None:
        return 0

    try:
        if pd.isna(value):
            return 0
    except TypeError:
        pass

    return int(float(value))


def as_float(value: Any) -> float:
    if value is None:
        return 0.0

    try:
        if pd.isna(value):
            return 0.0
    except TypeError:
        pass

    return float(value)


def append_gate(
    gates: list[dict],
    *,
    gate_id: str,
    severity: str,
    scope: str,
    observed,
    threshold,
    reason: str,
) -> None:
    gates.append({
        "gate_id":
            gate_id,

        "severity":
            severity,

        "scope":
            scope,

        "observed":
            observed,

        "threshold":
            threshold,

        "reason":
            reason,

        "automatic_mutation_authority":
            0,
    })


def form_policy(
    frame: pd.DataFrame,
    itemid: int,
    stack: int,
    prefix: str,
):
    if itemid not in set(
        frame["itemid"].astype(int)
    ):
        return None

    row = frame[
        frame["itemid"].astype(int)
        == itemid
    ].iloc[0]

    enabled = as_int(
        row[
            f"{prefix}_stacks"
            if stack
            else f"{prefix}_single"
        ]
    )

    price = as_int(
        row[
            "price_stacks"
            if stack
            else "price_single"
        ]
    )

    rate = as_float(
        row[
            f"{prefix}_rate_stacks"
            if stack
            else f"{prefix}_rate_single"
        ]
    )

    return {
        "enabled":
            enabled,

        "price":
            price,

        "rate":
            rate,
    }


def main() -> int:
    required = [
        BASELINE_JSON,
        MODEL_SUMMARY,
        SELLER_RUNTIME,
        BUYER_RUNTIME,
    ]

    for path in required:
        if not path.exists():
            raise SafetyGateError(
                f"Missing required input: {path}"
            )

    baseline = json.loads(
        BASELINE_JSON.read_text(
            encoding="utf-8",
        )
    )

    model = json.loads(
        MODEL_SUMMARY.read_text(
            encoding="utf-8",
        )
    )

    if baseline.get("status") != "PASS":
        raise SafetyGateError(
            "3E.1 baseline is not PASS."
        )

    if model.get("status") != "PASS":
        raise SafetyGateError(
            "3E.4 model is not PASS."
        )

    if (
        model.get("model_state")
        != "EXPOSURE_MODEL_READY"
    ):
        raise SafetyGateError(
            "3E.4 exposure model is not ready."
        )

    item_path_text = (
        model.get(
            "files",
            {},
        )
        .get(
            "item_exposure_model"
        )
    )

    if not item_path_text:
        raise SafetyGateError(
            "3E.4 item model path missing."
        )

    item_path = Path(
        item_path_text
    )

    if not item_path.exists():
        raise SafetyGateError(
            f"Missing item model: {item_path}"
        )

    items = pd.read_csv(
        item_path,
        low_memory=False,
    )

    seller = pd.read_csv(
        SELLER_RUNTIME,
        low_memory=False,
    )

    buyer = pd.read_csv(
        BUYER_RUNTIME,
        low_memory=False,
    )

    actual = model.get(
        "actual_flow",
        {},
    )

    stress = model.get(
        "buyer_stress_exposure",
        {},
    )

    seller_ref = model.get(
        "seller_inventory_reference",
        {},
    )

    ratios = model.get(
        "scale_ratios",
        {},
    )

    buyer_concentration = model.get(
        "buyer_exposure_concentration",
        {},
    )

    baseline_epoch = as_int(
        baseline.get(
            "baseline_epoch"
        )
    )

    now_epoch = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    observation_hours = round(
        (
            now_epoch
            - baseline_epoch
        )
        / 3600,
        6,
    )

    gates: list[dict] = []

    # ==================================================
    # GLOBAL STRESS SCALE
    # ==================================================

    stress_turnover_ratio = as_float(
        ratios.get(
            "expected_faucet_share_of_"
            "notional_target_turnover_reference"
        )
    )

    if (
        stress_turnover_ratio
        > GLOBAL_STRESS_REVIEW
    ):
        append_gate(
            gates,
            gate_id=(
                "GLOBAL_STRESS_EXPOSURE"
            ),
            severity="REVIEW",
            scope="GLOBAL",
            observed=stress_turnover_ratio,
            threshold=GLOBAL_STRESS_REVIEW,
            reason=(
                "EXPECTED_CONTINUOUS_SUPPLY_"
                "FAUCET_EXCEEDS_REVIEW_SHARE_"
                "OF_NOTIONAL_SELLER_TURNOVER"
            ),
        )

    elif (
        stress_turnover_ratio
        > GLOBAL_STRESS_WATCH
    ):
        append_gate(
            gates,
            gate_id=(
                "GLOBAL_STRESS_EXPOSURE"
            ),
            severity="WATCH",
            scope="GLOBAL",
            observed=stress_turnover_ratio,
            threshold=GLOBAL_STRESS_WATCH,
            reason=(
                "EXPECTED_CONTINUOUS_SUPPLY_"
                "FAUCET_EXCEEDS_WATCH_SHARE_"
                "OF_NOTIONAL_SELLER_TURNOVER"
            ),
        )

    # ==================================================
    # BUYER CONCENTRATION
    # ==================================================

    largest_share = as_float(
        buyer_concentration.get(
            "largest_item_share"
        )
    )

    top5_share = as_float(
        buyer_concentration.get(
            "top_5_item_share"
        )
    )

    hhi = as_float(
        buyer_concentration.get(
            "hhi"
        )
    )

    if largest_share > ITEM_SHARE_REVIEW:
        append_gate(
            gates,
            gate_id="LARGEST_BUYER_ITEM_SHARE",
            severity="REVIEW",
            scope="GLOBAL",
            observed=largest_share,
            threshold=ITEM_SHARE_REVIEW,
            reason=(
                "SINGLE_ITEM_DOMINATES_"
                "BUYER_STRESS_EXPOSURE"
            ),
        )

    elif largest_share > ITEM_SHARE_WATCH:
        append_gate(
            gates,
            gate_id="LARGEST_BUYER_ITEM_SHARE",
            severity="WATCH",
            scope="GLOBAL",
            observed=largest_share,
            threshold=ITEM_SHARE_WATCH,
            reason=(
                "SINGLE_ITEM_BUYER_EXPOSURE_"
                "CONCENTRATION_WATCH"
            ),
        )

    if top5_share > TOP5_SHARE_REVIEW:
        append_gate(
            gates,
            gate_id="TOP5_BUYER_SHARE",
            severity="REVIEW",
            scope="GLOBAL",
            observed=top5_share,
            threshold=TOP5_SHARE_REVIEW,
            reason=(
                "TOP5_ITEMS_DOMINATE_"
                "BUYER_EXPOSURE"
            ),
        )

    elif top5_share > TOP5_SHARE_WATCH:
        append_gate(
            gates,
            gate_id="TOP5_BUYER_SHARE",
            severity="WATCH",
            scope="GLOBAL",
            observed=top5_share,
            threshold=TOP5_SHARE_WATCH,
            reason=(
                "TOP5_BUYER_EXPOSURE_"
                "CONCENTRATION_WATCH"
            ),
        )

    if hhi > HHI_REVIEW:
        append_gate(
            gates,
            gate_id="BUYER_EXPOSURE_HHI",
            severity="REVIEW",
            scope="GLOBAL",
            observed=hhi,
            threshold=HHI_REVIEW,
            reason=(
                "BUYER_EXPOSURE_HIGHLY_"
                "CONCENTRATED"
            ),
        )

    elif hhi > HHI_WATCH:
        append_gate(
            gates,
            gate_id="BUYER_EXPOSURE_HHI",
            severity="WATCH",
            scope="GLOBAL",
            observed=hhi,
            threshold=HHI_WATCH,
            reason=(
                "BUYER_EXPOSURE_"
                "CONCENTRATION_WATCH"
            ),
        )

    # ==================================================
    # ITEM EXPOSURE
    # ==================================================

    item_budget_rows = []

    for _, row in items.iterrows():
        iid = as_int(
            row["itemid"]
        )

        name = str(
            row.get(
                "name",
                "",
            )
        )

        exposure = as_float(
            row.get(
                "buyer_expected_faucet_per_day"
            )
        )

        share = as_float(
            row.get(
                "buyer_expected_exposure_share"
            )
        )

        seller_target = as_float(
            row.get(
                "seller_configured_target_ask_value"
            )
        )

        if share > ITEM_SHARE_REVIEW:
            item_state = "REVIEW"
            item_reason = (
                "ITEM_EXPOSURE_SHARE_"
                "ABOVE_REVIEW_BUDGET"
            )

            append_gate(
                gates,
                gate_id=(
                    f"ITEM_EXPOSURE_{iid}"
                ),
                severity="REVIEW",
                scope=f"ITEM:{iid}",
                observed=share,
                threshold=ITEM_SHARE_REVIEW,
                reason=item_reason,
            )

        elif share > ITEM_SHARE_WATCH:
            item_state = "WATCH"
            item_reason = (
                "ITEM_EXPOSURE_SHARE_"
                "ABOVE_WATCH_BUDGET"
            )

            append_gate(
                gates,
                gate_id=(
                    f"ITEM_EXPOSURE_{iid}"
                ),
                severity="WATCH",
                scope=f"ITEM:{iid}",
                observed=share,
                threshold=ITEM_SHARE_WATCH,
                reason=item_reason,
            )

        elif (
            exposure > 0
            and seller_target <= 0
        ):
            item_state = "OBSERVE"
            item_reason = (
                "BUYER_ONLY_ITEM_NO_SELLER_"
                "COUNTERWEIGHT_REFERENCE"
            )

        else:
            item_state = "NORMAL"
            item_reason = (
                "WITHIN_ITEM_EXPOSURE_BUDGET"
            )

        item_budget_rows.append({
            "itemid":
                iid,

            "name":
                name,

            "buyer_expected_faucet_per_day":
                exposure,

            "buyer_expected_exposure_share":
                share,

            "seller_target_ask_value":
                seller_target,

            "budget_state":
                item_state,

            "budget_reason":
                item_reason,

            "automatic_change_authority":
                0,
        })

    item_budgets = pd.DataFrame(
        item_budget_rows
    ).sort_values(
        [
            "buyer_expected_faucet_per_day",
            "itemid",
        ],
        ascending=[
            False,
            True,
        ],
    )

    # ==================================================
    # DIRECT BUYER / SELLER SPREAD SAFETY
    #
    # This protects direct item flips.
    # It does NOT claim complete recipe-path coverage.
    # ==================================================

    union_ids = sorted(
        set(
            seller["itemid"]
            .astype(int)
        )
        | set(
            buyer["itemid"]
            .astype(int)
        )
    )

    form_rows = []

    spread_blocks = 0
    spread_reviews = 0

    for iid in union_ids:
        name = ""

        seller_match = seller[
            seller["itemid"]
            .astype(int)
            == iid
        ]

        buyer_match = buyer[
            buyer["itemid"]
            .astype(int)
            == iid
        ]

        if len(seller_match):
            name = str(
                seller_match.iloc[0][
                    "name"
                ]
            )

        elif len(buyer_match):
            name = str(
                buyer_match.iloc[0][
                    "name"
                ]
            )

        for stack in [0, 1]:
            sp = form_policy(
                seller,
                iid,
                stack,
                "sell",
            )

            bp = form_policy(
                buyer,
                iid,
                stack,
                "buy",
            )

            seller_enabled = (
                sp is not None
                and sp["enabled"] == 1
            )

            buyer_enabled = (
                bp is not None
                and bp["enabled"] == 1
            )

            seller_price = (
                sp["price"]
                if seller_enabled
                else None
            )

            buyer_bid = (
                bp["price"]
                if buyer_enabled
                else None
            )

            ratio = None
            spread_gil = None
            state = "NOT_COMPARABLE"
            reason = (
                "FORM_NOT_ENABLED_ON_BOTH_SIDES"
            )

            if (
                seller_enabled
                and buyer_enabled
                and seller_price
                and seller_price > 0
            ):
                ratio = (
                    buyer_bid
                    / seller_price
                )

                spread_gil = (
                    seller_price
                    - buyer_bid
                )

                if (
                    ratio
                    >= DIRECT_SPREAD_BLOCK
                ):
                    state = "BLOCK"
                    reason = (
                        "BUYER_BID_MEETS_OR_EXCEEDS_"
                        "SELLER_ASK"
                    )

                    spread_blocks += 1

                    append_gate(
                        gates,
                        gate_id=(
                            f"DIRECT_SPREAD_{iid}_"
                            f"{stack}"
                        ),
                        severity="BLOCK",
                        scope=(
                            f"FORM:{iid}:"
                            f"{stack}"
                        ),
                        observed=round(
                            ratio,
                            6,
                        ),
                        threshold=(
                            DIRECT_SPREAD_BLOCK
                        ),
                        reason=reason,
                    )

                elif (
                    ratio
                    >= DIRECT_SPREAD_REVIEW
                ):
                    state = "REVIEW"
                    reason = (
                        "BUYER_SELLER_DIRECT_"
                        "SPREAD_TOO_NARROW"
                    )

                    spread_reviews += 1

                    append_gate(
                        gates,
                        gate_id=(
                            f"DIRECT_SPREAD_{iid}_"
                            f"{stack}"
                        ),
                        severity="REVIEW",
                        scope=(
                            f"FORM:{iid}:"
                            f"{stack}"
                        ),
                        observed=round(
                            ratio,
                            6,
                        ),
                        threshold=(
                            DIRECT_SPREAD_REVIEW
                        ),
                        reason=reason,
                    )

                else:
                    state = "NORMAL"
                    reason = (
                        "DIRECT_SPREAD_WITHIN_"
                        "SAFETY_MARGIN"
                    )

            form_rows.append({
                "itemid":
                    iid,

                "name":
                    name,

                "stack":
                    stack,

                "form":
                    (
                        "STACK"
                        if stack
                        else "SINGLE"
                    ),

                "seller_enabled":
                    int(
                        seller_enabled
                    ),

                "seller_price":
                    seller_price,

                "buyer_enabled":
                    int(
                        buyer_enabled
                    ),

                "buyer_bid":
                    buyer_bid,

                "buyer_to_seller_ratio":
                    (
                        round(
                            ratio,
                            6,
                        )
                        if ratio
                        is not None
                        else None
                    ),

                "spread_gil":
                    spread_gil,

                "spread_state":
                    state,

                "spread_reason":
                    reason,

                "automatic_change_authority":
                    0,
            })

    form_gates = pd.DataFrame(
        form_rows
    ).sort_values(
        [
            "itemid",
            "stack",
        ]
    )

    # ==================================================
    # ACTUAL FLOW EVIDENCE GATE
    # ==================================================

    post_faucet = as_int(
        actual.get(
            "postbaseline_faucet_gil"
        )
    )

    post_sink = as_int(
        actual.get(
            "postbaseline_sink_gil"
        )
    )

    faucet_summary_path = Path(
        model[
            "source_summaries"
        ][
            "faucet"
        ]
    )

    sink_summary_path = Path(
        model[
            "source_summaries"
        ][
            "sink"
        ]
    )

    faucet_summary = json.loads(
        faucet_summary_path.read_text(
            encoding="utf-8",
        )
    )

    sink_summary = json.loads(
        sink_summary_path.read_text(
            encoding="utf-8",
        )
    )

    actual_transactions = (
        as_int(
            faucet_summary.get(
                "postbaseline_purchases"
            )
        )
        + as_int(
            sink_summary.get(
                "postbaseline_sales"
            )
        )
    )

    actual_evidence_mature = int(
        observation_hours
        >= MIN_ACTUAL_WINDOW_HOURS
        and actual_transactions
        >= MIN_ACTUAL_TRANSACTIONS
    )

    gross_actual_flow = (
        post_faucet
        + post_sink
    )

    actual_net_injection = max(
        post_faucet
        - post_sink,
        0,
    )

    actual_net_injection_share = (
        actual_net_injection
        / gross_actual_flow
        if gross_actual_flow > 0
        else 0.0
    )

    if not actual_evidence_mature:
        actual_flow_gate = (
            "OBSERVE"
        )

        actual_flow_reason = (
            "ACTUAL_FLOW_EVIDENCE_NOT_MATURE"
        )

    elif (
        actual_net_injection_share
        > ACTUAL_NET_INJECTION_REVIEW
    ):
        actual_flow_gate = (
            "REVIEW"
        )

        actual_flow_reason = (
            "MATURE_ACTUAL_FLOW_SHOWS_"
            "NET_INJECTION_ABOVE_REVIEW_BUDGET"
        )

        append_gate(
            gates,
            gate_id="ACTUAL_NET_GIL_INJECTION",
            severity="REVIEW",
            scope="GLOBAL",
            observed=round(
                actual_net_injection_share,
                6,
            ),
            threshold=(
                ACTUAL_NET_INJECTION_REVIEW
            ),
            reason=actual_flow_reason,
        )

    elif (
        actual_net_injection_share
        > ACTUAL_NET_INJECTION_WATCH
    ):
        actual_flow_gate = (
            "WATCH"
        )

        actual_flow_reason = (
            "MATURE_ACTUAL_FLOW_SHOWS_"
            "NET_INJECTION_ABOVE_WATCH_BUDGET"
        )

        append_gate(
            gates,
            gate_id="ACTUAL_NET_GIL_INJECTION",
            severity="WATCH",
            scope="GLOBAL",
            observed=round(
                actual_net_injection_share,
                6,
            ),
            threshold=(
                ACTUAL_NET_INJECTION_WATCH
            ),
            reason=actual_flow_reason,
        )

    else:
        actual_flow_gate = (
            "NORMAL"
        )

        actual_flow_reason = (
            "MATURE_ACTUAL_FLOW_WITHIN_BUDGET"
        )

    # ==================================================
    # STRUCTURAL SAFETY
    # ==================================================

    structural_failures = []

    if as_int(
        faucet_summary.get(
            "integrity_failures"
        )
    ) != 0:
        structural_failures.append(
            "Faucet ledger integrity failures."
        )

    if as_int(
        sink_summary.get(
            "integrity_failures"
        )
    ) != 0:
        structural_failures.append(
            "Sink ledger integrity failures."
        )

    if as_int(
        faucet_summary.get(
            "one_roll_per_item_form_per_cycle"
        )
    ) != 1:
        structural_failures.append(
            "Buyer one-roll hardening absent."
        )

    if spread_blocks:
        structural_failures.append(
            "Direct buyer/seller arbitrage "
            "spread inversion detected."
        )

    block_count = sum(
        1
        for gate in gates
        if gate["severity"] == "BLOCK"
    )

    review_count = sum(
        1
        for gate in gates
        if gate["severity"] == "REVIEW"
    )

    watch_count = sum(
        1
        for gate in gates
        if gate["severity"] == "WATCH"
    )

    if structural_failures:
        budget_state = (
            "BLOCKED"
        )

        status = "FAIL"

    elif review_count:
        budget_state = (
            "REVIEW_REQUIRED"
        )

        status = "PASS"

    elif watch_count:
        budget_state = (
            "WATCH"
        )

        status = "PASS"

    else:
        budget_state = (
            "WITHIN_CURRENT_BUDGETS"
        )

        status = "PASS"

    # Recipe/craft-path safety remains deliberately
    # fail-closed for future catalog expansion.
    repeatability_state = (
        "PARTIAL_STRUCTURAL_PROTECTION"
    )

    full_recipe_path_reaudit_required_for_expansion = 1

    SAFETY_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    item_file = (
        SAFETY_ROOT
        / "current-item-budgets.csv"
    )

    form_file = (
        SAFETY_ROOT
        / "current-form-spread-gates.csv"
    )

    gates_file = (
        SAFETY_ROOT
        / "current-review-gates.csv"
    )

    summary_file = (
        SAFETY_ROOT
        / "current-safety-summary.json"
    )

    item_budgets.to_csv(
        item_file,
        index=False,
    )

    form_gates.to_csv(
        form_file,
        index=False,
    )

    pd.DataFrame(
        gates,
        columns=[
            "gate_id",
            "severity",
            "scope",
            "observed",
            "threshold",
            "reason",
            "automatic_mutation_authority",
        ],
    ).to_csv(
        gates_file,
        index=False,
    )

    summary = {
        "status":
            status,

        "phase":
            "3E.5",

        "budget_state":
            budget_state,

        "observation_hours":
            observation_hours,

        "actual_flow_evidence_mature":
            actual_evidence_mature,

        "actual_postbaseline_transactions":
            actual_transactions,

        "actual_postbaseline_faucet_gil":
            post_faucet,

        "actual_postbaseline_sink_gil":
            post_sink,

        "actual_net_injection_gil":
            actual_net_injection,

        "actual_net_injection_share_of_gross":
            round(
                actual_net_injection_share,
                6,
            ),

        "actual_flow_gate":
            actual_flow_gate,

        "actual_flow_reason":
            actual_flow_reason,

        "global_stress_ratio":
            stress_turnover_ratio,

        "expected_continuous_supply_faucet_per_day":
            as_float(
                stress.get(
                    "expected_gil_per_day_continuous_supply"
                )
            ),

        "seller_notional_turnover_reference_per_day":
            as_float(
                seller_ref.get(
                    "notional_target_turnover_reference_per_day"
                )
            ),

        "largest_buyer_item_share":
            largest_share,

        "buyer_top5_share":
            top5_share,

        "buyer_hhi":
            hhi,

        "watch_count":
            watch_count,

        "review_count":
            review_count,

        "block_count":
            block_count,

        "direct_spread_reviews":
            spread_reviews,

        "direct_spread_blocks":
            spread_blocks,

        "repeatability_protection_state":
            repeatability_state,

        "full_recipe_path_reaudit_required_for_expansion":
            full_recipe_path_reaudit_required_for_expansion,

        "policy_thresholds": {
            "global_stress_watch":
                GLOBAL_STRESS_WATCH,

            "global_stress_review":
                GLOBAL_STRESS_REVIEW,

            "item_share_watch":
                ITEM_SHARE_WATCH,

            "item_share_review":
                ITEM_SHARE_REVIEW,

            "top5_share_watch":
                TOP5_SHARE_WATCH,

            "top5_share_review":
                TOP5_SHARE_REVIEW,

            "hhi_watch":
                HHI_WATCH,

            "hhi_review":
                HHI_REVIEW,

            "direct_spread_review":
                DIRECT_SPREAD_REVIEW,

            "direct_spread_block":
                DIRECT_SPREAD_BLOCK,

            "minimum_actual_window_hours":
                MIN_ACTUAL_WINDOW_HOURS,

            "minimum_actual_transactions":
                MIN_ACTUAL_TRANSACTIONS,

            "actual_net_injection_watch":
                ACTUAL_NET_INJECTION_WATCH,

            "actual_net_injection_review":
                ACTUAL_NET_INJECTION_REVIEW,
        },

        "structural_failures":
            structural_failures,

        "price_change_authorized":
            0,

        "rate_change_authorized":
            0,

        "stock_change_authorized":
            0,

        "catalog_change_authorized":
            0,

        "runtime_mutation_authorized":
            0,

        "database_mutation_authorized":
            0,

        "auto_live_promotion":
            0,

        "files": {
            "item_budgets":
                str(
                    item_file
                ),

            "form_spread_gates":
                str(
                    form_file
                ),

            "review_gates":
                str(
                    gates_file
                ),
        },
    }

    summary_file.write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print("=" * 112)
    print(
        " Phase 3E.5 Gil-Flow Safety "
        "Budgets + Review Gates"
    )
    print("=" * 112)
    print()

    print("GLOBAL STRESS BUDGET")
    print()

    print(
        "Expected faucet / notional turnover: ",
        round(
            stress_turnover_ratio,
            6,
        ),
    )

    print(
        "Watch threshold:                     ",
        round(
            GLOBAL_STRESS_WATCH,
            6,
        ),
    )

    print(
        "Review threshold:                    ",
        round(
            GLOBAL_STRESS_REVIEW,
            6,
        ),
    )

    print()
    print("CONCENTRATION")
    print()

    print(
        "Largest buyer item share:            ",
        round(
            largest_share,
            6,
        ),
    )

    print(
        "Buyer top-5 share:                   ",
        round(
            top5_share,
            6,
        ),
    )

    print(
        "Buyer HHI:                           ",
        round(
            hhi,
            6,
        ),
    )

    print()
    print("DIRECT ARBITRAGE GUARD")
    print()

    print(
        "Spread review forms:                 ",
        spread_reviews,
    )

    print(
        "Spread block forms:                  ",
        spread_blocks,
    )

    print()
    print("ACTUAL FLOW EVIDENCE")
    print()

    print(
        "Observation hours:                   ",
        observation_hours,
    )

    print(
        "Post-baseline AHBot transactions:    ",
        actual_transactions,
    )

    print(
        "Actual evidence mature:              ",
        actual_evidence_mature,
    )

    print(
        "Actual faucet:                       ",
        post_faucet,
    )

    print(
        "Actual sink:                         ",
        post_sink,
    )

    print(
        "Actual net injection share:          ",
        round(
            actual_net_injection_share,
            6,
        ),
    )

    print(
        "Actual flow gate:                    ",
        actual_flow_gate,
    )

    print()
    print("REVIEW COUNTS")
    print()

    print(
        "Watch gates:                         ",
        watch_count,
    )

    print(
        "Review gates:                        ",
        review_count,
    )

    print(
        "Block gates:                         ",
        block_count,
    )

    print()
    print(
        "Repeatability protection:",
        repeatability_state,
    )

    print(
        "Full recipe-path re-audit required "
        "for expansion:",
        full_recipe_path_reaudit_required_for_expansion,
    )

    print()
    print(
        "Budget state:",
        budget_state,
    )

    print()
    print(
        "Price change authorized:             0"
    )

    print(
        "Rate change authorized:              0"
    )

    print(
        "Stock change authorized:             0"
    )

    print(
        "Catalog change authorized:           0"
    )

    print(
        "Runtime mutation authorized:         0"
    )

    print(
        "Database mutation authorized:        0"
    )

    print(
        "Auto live promotion:                 0"
    )

    print()
    print(
        "Item budgets:",
        item_file,
    )

    print(
        "Form spread gates:",
        form_file,
    )

    print(
        "Review gates:",
        gates_file,
    )

    print(
        "Summary:",
        summary_file,
    )

    print()
    print(status)

    if structural_failures:
        print()
        print("Structural failures:")

        for failure in structural_failures:
            print(
                " -",
                failure,
            )

        raise SystemExit(1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
