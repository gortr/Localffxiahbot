from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

MODEL_FILE = (
    ROOT
    / "economy"
    / "generated"
    / "buyer-bid-model.csv"
)

REPEATABLE_FILE = (
    ROOT
    / "economy"
    / "reports"
    / "buyer-pilot-b-repeatable-input-summary.csv"
)

OUTPUT_FILE = (
    ROOT
    / "economy"
    / "generated"
    / "buyer-pilot-b-safe-ceiling-policy.csv"
)

SUMMARY_FILE = (
    ROOT
    / "economy"
    / "reports"
    / "buyer-pilot-b-safe-ceiling-policy-summary.json"
)


CANDIDATE_IDS = {
    2308,
    2311,
    2360,
    2710,
    5188,
    5189,
    5193,
    5213,
    5327,
    5644,
    5653,
    5655,
    9052,
    9053,
    9196,
}


class BuyerPilotPolicyError(RuntimeError):
    pass


def as_int(value) -> int:
    if value is None or pd.isna(value):
        return 0

    return int(float(value))


def as_float(value):
    if value is None or pd.isna(value):
        return None

    return float(value)


def safe_floor(value: float) -> int:
    return max(
        1,
        math.floor(value + 1e-9),
    )


def main() -> None:
    model = pd.read_csv(
        MODEL_FILE,
        low_memory=False,
    )

    repeatable = pd.read_csv(
        REPEATABLE_FILE,
        low_memory=False,
    )

    model = model[
        model["itemid"]
        .astype(int)
        .isin(CANDIDATE_IDS)
    ].copy()

    repeatable = repeatable[
        repeatable["itemid"]
        .astype(int)
        .isin(CANDIDATE_IDS)
    ].copy()

    if len(model) != 15:
        raise BuyerPilotPolicyError(
            "Expected 15 buyer-model candidates; "
            f"found {len(model)}."
        )

    if len(repeatable) != 15:
        raise BuyerPilotPolicyError(
            "Expected 15 repeatable-source candidates; "
            f"found {len(repeatable)}."
        )

    merged = model.merge(
        repeatable[
            [
                "itemid",
                "ahbot_sold_inputs",
                "npc_vendor_inputs",
                "repeatable_inputs",
                "nonrepeatable_inputs",
                "all_inputs_repeatable",
                "repeatable_recipe_cost",
                "repeatable_unit_cost",
            ]
        ],
        on="itemid",
        how="left",
        validate="one_to_one",
    )

    rows = []

    for _, row in merged.iterrows():
        itemid = as_int(row["itemid"])

        stack_size = as_int(
            row["stack_size"]
        )

        economic_cost = as_float(
            row["economic_cost"]
        )

        economic_floor = as_float(
            row["economic_floor"]
        )

        modeled_single = as_int(
            row["final_single_bid"]
        )

        modeled_stack = as_int(
            row["final_stack_bid"]
        )

        all_repeatable = as_int(
            row["all_inputs_repeatable"]
        )

        repeatable_unit_cost = as_float(
            row["repeatable_unit_cost"]
        )

        if economic_cost is None:
            raise BuyerPilotPolicyError(
                f"{itemid}: missing economic cost."
            )

        if modeled_single <= 0:
            raise BuyerPilotPolicyError(
                f"{itemid}: invalid single bid."
            )

        if stack_size <= 0:
            raise BuyerPilotPolicyError(
                f"{itemid}: invalid stack size."
            )

        repeatable_single_ceiling = None
        repeatable_stack_ceiling = None

        if all_repeatable:
            if (
                repeatable_unit_cost is None
                or repeatable_unit_cost <= 0
            ):
                raise BuyerPilotPolicyError(
                    f"{itemid}: repeatable recipe "
                    "lacks repeatable unit cost."
                )

            repeatable_single_ceiling = (
                safe_floor(
                    repeatable_unit_cost
                )
            )

            repeatable_stack_ceiling = (
                safe_floor(
                    repeatable_unit_cost
                    * stack_size
                )
            )

        floor_conflict = int(
            all_repeatable
            and economic_floor is not None
            and repeatable_single_ceiling is not None
            and economic_floor
            > repeatable_single_ceiling
        )

        single_cap_required = int(
            all_repeatable
            and repeatable_single_ceiling
            is not None
            and modeled_single
            > repeatable_single_ceiling
        )

        stack_cap_required = int(
            all_repeatable
            and repeatable_stack_ceiling
            is not None
            and modeled_stack
            > repeatable_stack_ceiling
        )

        bid_above_economic_cost = int(
            modeled_single
            > economic_cost
        )

        safe_single = modeled_single
        safe_stack = modeled_stack

        if all_repeatable:
            safe_single = min(
                modeled_single,
                repeatable_single_ceiling,
            )

            safe_stack = min(
                modeled_stack,
                repeatable_stack_ceiling,
            )

        # ------------------------------------------------
        # Fail-closed disposition hierarchy.
        # ------------------------------------------------

        if floor_conflict:
            disposition = (
                "FLOOR_CONFLICT_DEFER_BUYER"
            )

            reason = (
                "Economic floor exceeds the "
                "repeatable acquisition ceiling."
            )

            pilot_price_ready = 0

        elif (
            bid_above_economic_cost
            and not all_repeatable
        ):
            disposition = (
                "ABOVE_COST_FLOOR_REVIEW"
            )

            reason = (
                "Modeled bid exceeds deterministic "
                "economic cost and recipe is not "
                "fully repeatable; weak floor is "
                "insufficient for Pilot B activation."
            )

            pilot_price_ready = 0

        elif (
            single_cap_required
            or stack_cap_required
        ):
            disposition = (
                "REPEATABLE_SOURCE_CAP_REQUIRED"
            )

            reason = (
                "Modeled bid requires a hard cap "
                "below repeatable acquisition cost."
            )

            pilot_price_ready = 1

        elif all_repeatable:
            disposition = (
                "PILOT_B_PRICE_READY_REPEATABLE_SAFE"
            )

            reason = (
                "Modeled bid is already below the "
                "repeatable acquisition ceiling."
            )

            pilot_price_ready = 1

        else:
            disposition = (
                "PILOT_B_PRICE_READY_PARTIAL_REPEATABILITY"
            )

            reason = (
                "No fully repeatable acquisition "
                "chain exists and modeled bid does "
                "not exceed deterministic cost."
            )

            pilot_price_ready = 1

        rows.append({
            "itemid":
                itemid,

            "name":
                row["name"],

            "market_class":
                row["market_class"],

            "minimum_maturity":
                row["minimum_maturity"],

            "economic_cost":
                economic_cost,

            "economic_floor":
                economic_floor,

            "modeled_single_bid":
                modeled_single,

            "modeled_stack_bid":
                modeled_stack,

            "stack_size":
                stack_size,

            "ahbot_sold_inputs":
                as_int(
                    row["ahbot_sold_inputs"]
                ),

            "npc_vendor_inputs":
                as_int(
                    row["npc_vendor_inputs"]
                ),

            "repeatable_inputs":
                as_int(
                    row["repeatable_inputs"]
                ),

            "nonrepeatable_inputs":
                as_int(
                    row["nonrepeatable_inputs"]
                ),

            "all_inputs_repeatable":
                all_repeatable,

            "repeatable_unit_cost":
                repeatable_unit_cost,

            "repeatable_single_ceiling":
                repeatable_single_ceiling,

            "repeatable_stack_ceiling":
                repeatable_stack_ceiling,

            "economic_floor_conflict":
                floor_conflict,

            "bid_above_economic_cost":
                bid_above_economic_cost,

            "single_cap_required":
                single_cap_required,

            "stack_cap_required":
                stack_cap_required,

            "safe_single_bid":
                safe_single,

            "safe_stack_bid":
                safe_stack,

            "buy_rate_single":
                0.015,

            "buy_rate_stacks":
                0.015,

            "rate_semantic":
                "ONE_ROLL_PER_ITEM_FORM_PER_CYCLE",

            "disposition":
                disposition,

            "disposition_reason":
                reason,

            "pilot_price_ready":
                pilot_price_ready,

            "catalog_change_authorized":
                0,

            "runtime_mutation_authorized":
                0,

            "activation_ready":
                0,

            "auto_live_promotion":
                0,
        })

    output = pd.DataFrame(rows).sort_values(
        "itemid"
    )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    counts = (
        output["disposition"]
        .value_counts()
        .sort_index()
        .to_dict()
    )

    ready = int(
        output["pilot_price_ready"].sum()
    )

    floor_conflicts = int(
        output[
            "economic_floor_conflict"
        ].sum()
    )

    above_cost_reviews = int(
        (
            output["disposition"]
            == "ABOVE_COST_FLOOR_REVIEW"
        ).sum()
    )

    cap_required = int(
        (
            output["disposition"]
            == "REPEATABLE_SOURCE_CAP_REQUIRED"
        ).sum()
    )

    failures = []

    if floor_conflicts != 1:
        failures.append(
            "Expected exactly one repeatable "
            "floor conflict."
        )

    if above_cost_reviews != 1:
        failures.append(
            "Expected exactly one partial-repeatability "
            "above-cost review."
        )

    if (
        output[
            "catalog_change_authorized"
        ].sum()
        != 0
    ):
        failures.append(
            "Catalog mutation became authorized."
        )

    if (
        output[
            "runtime_mutation_authorized"
        ].sum()
        != 0
    ):
        failures.append(
            "Runtime mutation became authorized."
        )

    if (
        output["activation_ready"].sum()
        != 0
    ):
        failures.append(
            "Activation unexpectedly became ready."
        )

    if (
        output["auto_live_promotion"].sum()
        != 0
    ):
        failures.append(
            "Automatic live promotion detected."
        )

    summary = {
        "status":
            "PASS"
            if not failures
            else "FAIL",

        "candidate_items":
            len(output),

        "price_ready_items":
            ready,

        "deferred_items":
            len(output) - ready,

        "fully_repeatable_items":
            int(
                output[
                    "all_inputs_repeatable"
                ].sum()
            ),

        "economic_floor_conflicts":
            floor_conflicts,

        "above_cost_floor_reviews":
            above_cost_reviews,

        "repeatable_caps_required":
            cap_required,

        "disposition_counts":
            counts,

        "catalog_change_authorized":
            0,

        "runtime_mutation_authorized":
            0,

        "activation_ready":
            0,

        "auto_live_promotion":
            0,

        "failures":
            failures,
    }

    SUMMARY_FILE.write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    print("=" * 104)
    print(
        " Phase 3C.2 Safe Buyer Ceiling Policy"
    )
    print("=" * 104)
    print()

    display = output[
        [
            "itemid",
            "name",
            "economic_cost",
            "economic_floor",
            "modeled_single_bid",
            "all_inputs_repeatable",
            "repeatable_unit_cost",
            "repeatable_single_ceiling",
            "safe_single_bid",
            "safe_stack_bid",
            "disposition",
            "pilot_price_ready",
        ]
    ]

    print(
        display.to_string(
            index=False
        )
    )

    print()
    print(
        "Candidate items:            ",
        len(output),
    )

    print(
        "Price-ready items:          ",
        ready,
    )

    print(
        "Deferred/review items:      ",
        len(output) - ready,
    )

    print(
        "Fully repeatable recipes:   ",
        summary[
            "fully_repeatable_items"
        ],
    )

    print(
        "Floor conflicts:            ",
        floor_conflicts,
    )

    print(
        "Above-cost reviews:         ",
        above_cost_reviews,
    )

    print(
        "Repeatable caps required:   ",
        cap_required,
    )

    print()
    print(
        "Runtime mutation authorized: 0"
    )

    print(
        "Activation ready:             0"
    )

    print(
        "Auto live promotion:          0"
    )

    print()
    print("Policy:", OUTPUT_FILE)
    print("Summary:", SUMMARY_FILE)
    print()
    print(summary["status"])

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
