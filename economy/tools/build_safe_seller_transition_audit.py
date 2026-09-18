from __future__ import annotations

import json
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_CEILING,
)
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

SELL_RUNTIME = (
    GENERATED
    / "market-sell-pricing-cutover.csv"
)

BUY_RUNTIME = (
    GENERATED
    / "market-buy-pricing-cutover.csv"
)

SELLER_MODEL = (
    GENERATED
    / "seller-price-model.csv"
)

BUYER_MODEL = (
    GENERATED
    / "buyer-bid-model.csv"
)

STACK_MODEL = (
    GENERATED
    / "stack-price-model.csv"
)

CRAFT_AUDIT = (
    REPORTS
    / "craft-material-relation-audit.csv"
)

OUTPUT_FILE = (
    REPORTS
    / "safe-seller-transition-audit.csv"
)

SUMMARY_FILE = (
    REPORTS
    / "safe-seller-transition-summary.json"
)


EXPECTED_LIVE_SELLERS = 162

EXPECTED_SAFE_TARGETS = {
    744: {
        "name": "silver_ingot",
        "single": 351,
        "stack": 4201,
    },
    1634: {
        "name": "rhodonite",
        "single": 601,
        "stack": 7201,
    },
    8740: {
        "name": "pizza_cutter",
        "single": 249,
        "stack": 2989,
    },
}

EXPECTED_ACTION_COUNTS = {
    "HOLD_STABLE": 153,
    "SAFE_DOWNWARD_NORMALIZATION": 3,
    "DEFER_UPWARD_REPRICE_REVIEW": 3,
    "HOLD_BUYER_OR_ECONOMIC_FLOOR": 3,
}


TARGET_STACK_RATIO = Decimal("0.95")
ABSOLUTE_MINIMUM_STACK_RATIO = Decimal("0.90")

DEADBAND_LOW = Decimal("0.90")
DEADBAND_HIGH = Decimal("1.10")


class SellerTransitionAuditError(
    RuntimeError
):
    pass


def clean_text(
    value: Any,
) -> str:
    if value is None:
        return ""

    text = str(value).strip()

    if text.lower() in {
        "",
        "nan",
        "none",
        "null",
    }:
        return ""

    return text


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
        return int(
            Decimal(str(value))
        )
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return 0


def as_decimal(
    value: Any,
) -> Decimal | None:
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass

    try:
        return Decimal(
            str(value)
        )
    except (
        InvalidOperation,
        ValueError,
        TypeError,
    ):
        return None


def ceil_decimal(
    value: Decimal | None,
) -> int:
    if value is None:
        return 0

    return int(
        value.to_integral_value(
            rounding=ROUND_CEILING
        )
    )


def spread_floor(
    buyer_price: Any,
    spread_value: Any,
    *,
    itemid: int,
    label: str,
) -> int:
    buyer = as_decimal(
        buyer_price
    )

    if (
        buyer is None
        or buyer <= 0
    ):
        return 0

    spread = as_decimal(
        spread_value
    )

    if (
        spread is None
        or spread <= 0
        or spread >= 1
    ):
        raise SellerTransitionAuditError(
            f"{itemid} has active {label} buyer "
            "price but invalid class spread: "
            f"{spread_value}"
        )

    return ceil_decimal(
        buyer / spread
    )


def require_columns(
    frame: pd.DataFrame,
    required: set[str],
    label: str,
) -> None:
    missing = sorted(
        required
        - set(frame.columns)
    )

    if missing:
        raise SellerTransitionAuditError(
            f"{label} missing columns: "
            f"{missing}"
        )


def load_inputs():
    required_files = [
        SELL_RUNTIME,
        BUY_RUNTIME,
        SELLER_MODEL,
        BUYER_MODEL,
        STACK_MODEL,
        CRAFT_AUDIT,
    ]

    missing = [
        str(path)
        for path in required_files
        if not path.exists()
    ]

    if missing:
        raise SellerTransitionAuditError(
            "Missing required inputs: "
            + ", ".join(missing)
        )

    sell = pd.read_csv(
        SELL_RUNTIME,
        low_memory=False,
    )

    buy = pd.read_csv(
        BUY_RUNTIME,
        low_memory=False,
    )

    seller = pd.read_csv(
        SELLER_MODEL,
        low_memory=False,
    )

    buyer = pd.read_csv(
        BUYER_MODEL,
        low_memory=False,
    )

    stack = pd.read_csv(
        STACK_MODEL,
        low_memory=False,
    )

    craft = pd.read_csv(
        CRAFT_AUDIT,
        low_memory=False,
    )

    require_columns(
        sell,
        {
            "itemid",
            "name",
            "price_single",
            "price_stacks",
        },
        "seller runtime",
    )

    require_columns(
        buy,
        {
            "itemid",
            "price_single",
            "price_stacks",
        },
        "buyer runtime",
    )

    require_columns(
        seller,
        {
            "itemid",
            "market_class",
            "cost_source_family",
            "economic_cost",
            "economic_floor",
            "theoretical_single",
        },
        "seller model",
    )

    require_columns(
        buyer,
        {
            "itemid",
            "class_spread",
        },
        "buyer model",
    )

    require_columns(
        stack,
        {
            "itemid",
            "stack_size",
            "effective_stack_hard_floor",
        },
        "stack model",
    )

    require_columns(
        craft,
        {
            "result_itemid",
            "selected_for_result",
            "all_inputs_bot_priceable",
        },
        "craft relation audit",
    )

    return (
        sell,
        buy,
        seller,
        buyer,
        stack,
        craft,
    )


def main() -> int:
    (
        sell,
        buy,
        seller,
        buyer,
        stack,
        craft,
    ) = load_inputs()

    if len(sell) != EXPECTED_LIVE_SELLERS:
        raise SellerTransitionAuditError(
            "Expected "
            f"{EXPECTED_LIVE_SELLERS} "
            "live sellers; found "
            f"{len(sell)}."
        )

    if sell["itemid"].duplicated().any():
        raise SellerTransitionAuditError(
            "Seller runtime contains "
            "duplicate itemids."
        )

    if buy["itemid"].duplicated().any():
        raise SellerTransitionAuditError(
            "Buyer runtime contains "
            "duplicate itemids."
        )

    live_sell = sell[
        [
            "itemid",
            "name",
            "price_single",
            "price_stacks",
        ]
    ].rename(
        columns={
            "price_single":
                "current_single",
            "price_stacks":
                "current_stack",
        }
    )

    live_buy = buy[
        [
            "itemid",
            "price_single",
            "price_stacks",
        ]
    ].rename(
        columns={
            "price_single":
                "live_buyer_single",
            "price_stacks":
                "live_buyer_stack",
        }
    )

    seller_fields = seller[
        [
            col
            for col in [
                "itemid",
                "market_class",
                "minimum_maturity",
                "pricing_evidence_class",
                "pricing_model_family",
                "cost_source_family",
                "economic_cost",
                "economic_floor",
                "hq_lower_bound",
                "theoretical_single",
            ]
            if col in seller.columns
        ]
    ].drop_duplicates(
        "itemid"
    )

    buyer_meta = buyer[
        [
            "itemid",
            "class_spread",
        ]
    ].drop_duplicates(
        "itemid"
    )

    stack_fields = stack[
        [
            col
            for col in [
                "itemid",
                "stack_size",
                "actual_vendor_source",
                "vendor_price_min",
                "vendor_stack_floor",
                "effective_stack_hard_floor",
            ]
            if col in stack.columns
        ]
    ].drop_duplicates(
        "itemid"
    )

    selected = craft[
        craft[
            "selected_for_result"
        ]
        == 1
    ].copy()

    craft_fields = selected[
        [
            col
            for col in [
                "result_itemid",
                "recipe_id",
                "result_qty",
                "unit_cost",
                "all_inputs_bot_priceable",
            ]
            if col in selected.columns
        ]
    ].rename(
        columns={
            "result_itemid":
                "itemid",
        }
    )

    if (
        craft_fields[
            "itemid"
        ]
        .duplicated()
        .any()
    ):
        raise SellerTransitionAuditError(
            "Selected craft relation contains "
            "duplicate result itemids."
        )

    df = (
        live_sell
        .merge(
            seller_fields,
            on="itemid",
            how="left",
            validate="one_to_one",
        )
        .merge(
            buyer_meta,
            on="itemid",
            how="left",
            validate="one_to_one",
        )
        .merge(
            live_buy,
            on="itemid",
            how="left",
            validate="one_to_one",
        )
        .merge(
            stack_fields,
            on="itemid",
            how="left",
            validate="one_to_one",
        )
        .merge(
            craft_fields,
            on="itemid",
            how="left",
            validate="one_to_one",
        )
    )

    rows = []

    for _, row in df.iterrows():
        itemid = as_int(
            row.get("itemid")
        )

        name = clean_text(
            row.get("name")
        )

        current_single = as_int(
            row.get(
                "current_single"
            )
        )

        current_stack = as_int(
            row.get(
                "current_stack"
            )
        )

        theoretical = as_decimal(
            row.get(
                "theoretical_single"
            )
        )

        economic_cost = as_decimal(
            row.get(
                "economic_cost"
            )
        )

        economic_floor = as_decimal(
            row.get(
                "economic_floor"
            )
        )

        spread = as_decimal(
            row.get(
                "class_spread"
            )
        )

        live_buyer_single = (
            as_decimal(
                row.get(
                    "live_buyer_single"
                )
            )
        )

        live_buyer_stack = (
            as_decimal(
                row.get(
                    "live_buyer_stack"
                )
            )
        )

        stack_size = as_int(
            row.get(
                "stack_size"
            )
        )

        if current_single <= 0:
            raise SellerTransitionAuditError(
                f"{itemid} has invalid "
                "current single price."
            )

        if stack_size > 1 and current_stack <= 0:
            raise SellerTransitionAuditError(
                f"{itemid} has invalid "
                "current stack price."
            )

        if (
            theoretical is None
            or theoretical <= 0
        ):
            action = (
                "DEFER_INVALID_THEORETICAL"
            )

            theoretical_ratio = None

        else:
            theoretical_ratio = (
                theoretical
                / Decimal(
                    current_single
                )
            )

            single_buyer_floor = (
                spread_floor(
                    live_buyer_single,
                    spread,
                    itemid=itemid,
                    label="single",
                )
            )

            single_economic_floor = max(
                ceil_decimal(
                    economic_cost
                ),
                ceil_decimal(
                    economic_floor
                ),
            )

            candidate_single = max(
                ceil_decimal(
                    theoretical
                ),
                single_buyer_floor,
                single_economic_floor,
            )

            if (
                DEADBAND_LOW
                <= theoretical_ratio
                <= DEADBAND_HIGH
            ):
                action = (
                    "HOLD_STABLE"
                )

            elif (
                theoretical_ratio
                > DEADBAND_HIGH
            ):
                action = (
                    "DEFER_UPWARD_REPRICE_REVIEW"
                )

            elif (
                candidate_single
                >= current_single
            ):
                action = (
                    "HOLD_BUYER_OR_ECONOMIC_FLOOR"
                )

            elif (
                clean_text(
                    row.get(
                        "cost_source_family"
                    )
                )
                == "CRAFT_NQ_COST"
                and as_int(
                    row.get(
                        "all_inputs_bot_priceable"
                    )
                )
                != 1
            ):
                action = (
                    "DEFER_CRAFT_INPUT_PRICEABILITY"
                )

            else:
                action = (
                    "SAFE_DOWNWARD_NORMALIZATION"
                )

        single_buyer_floor = (
            spread_floor(
                live_buyer_single,
                spread,
                itemid=itemid,
                label="single",
            )
        )

        single_economic_floor = max(
            ceil_decimal(
                economic_cost
            ),
            ceil_decimal(
                economic_floor
            ),
        )

        candidate_single = max(
            ceil_decimal(
                theoretical
            ),
            single_buyer_floor,
            single_economic_floor,
        )

        deployment_single = (
            current_single
        )

        if (
            action
            == "SAFE_DOWNWARD_NORMALIZATION"
        ):
            deployment_single = (
                candidate_single
            )

        stack_buyer_floor = (
            spread_floor(
                live_buyer_stack,
                spread,
                itemid=itemid,
                label="stack",
            )
        )

        stack_bulk_target = 0
        stack_absolute_minimum = 0
        stack_hard_floor = 0
        candidate_stack = current_stack

        if stack_size > 1:
            single_equivalent = (
                Decimal(
                    deployment_single
                )
                * Decimal(
                    stack_size
                )
            )

            stack_bulk_target = (
                ceil_decimal(
                    single_equivalent
                    * TARGET_STACK_RATIO
                )
            )

            stack_absolute_minimum = (
                ceil_decimal(
                    single_equivalent
                    * ABSOLUTE_MINIMUM_STACK_RATIO
                )
            )

            stack_hard_floor = max(
                ceil_decimal(
                    as_decimal(
                        row.get(
                            "effective_stack_hard_floor"
                        )
                    )
                ),
                ceil_decimal(
                    as_decimal(
                        row.get(
                            "vendor_stack_floor"
                        )
                    )
                ),
            )

            candidate_stack = max(
                stack_bulk_target,
                stack_absolute_minimum,
                stack_hard_floor,
                stack_buyer_floor,
            )

        deployment_stack = (
            current_stack
        )

        if (
            action
            == "SAFE_DOWNWARD_NORMALIZATION"
        ):
            deployment_stack = (
                candidate_stack
            )

        single_buyer_safe = int(
            deployment_single
            >= single_buyer_floor
        )

        single_floor_safe = int(
            deployment_single
            >= single_economic_floor
        )

        stack_buyer_safe = int(
            stack_size <= 1
            or deployment_stack
            >= stack_buyer_floor
        )

        stack_90_safe = int(
            stack_size <= 1
            or deployment_stack
            >= stack_absolute_minimum
        )

        stack_hard_floor_safe = int(
            stack_size <= 1
            or deployment_stack
            >= stack_hard_floor
        )

        stack_bulk_target_safe = int(
            action
            != "SAFE_DOWNWARD_NORMALIZATION"
            or stack_size <= 1
            or deployment_stack
            >= stack_bulk_target
        )

        non_authorized_unchanged = int(
            action
            == "SAFE_DOWNWARD_NORMALIZATION"
            or (
                deployment_single
                == current_single
                and deployment_stack
                == current_stack
            )
        )

        downward_only = int(
            deployment_single
            <= current_single
            and deployment_stack
            <= current_stack
        )

        rows.append({
            "itemid":
                itemid,

            "name":
                name,

            "market_class":
                clean_text(
                    row.get(
                        "market_class"
                    )
                ),

            "cost_source_family":
                clean_text(
                    row.get(
                        "cost_source_family"
                    )
                ),

            "current_single":
                current_single,

            "theoretical_single":
                (
                    float(theoretical)
                    if theoretical is not None
                    else None
                ),

            "theoretical_ratio":
                (
                    float(
                        theoretical_ratio
                    )
                    if theoretical_ratio
                    is not None
                    else None
                ),

            "live_buyer_single":
                (
                    float(
                        live_buyer_single
                    )
                    if live_buyer_single
                    is not None
                    else None
                ),

            "class_spread":
                (
                    float(spread)
                    if spread is not None
                    else None
                ),

            "single_buyer_floor":
                single_buyer_floor,

            "single_economic_floor":
                single_economic_floor,

            "candidate_single":
                candidate_single,

            "deployment_single":
                deployment_single,

            "current_stack":
                current_stack,

            "live_buyer_stack":
                (
                    float(
                        live_buyer_stack
                    )
                    if live_buyer_stack
                    is not None
                    else None
                ),

            "stack_size":
                stack_size,

            "stack_bulk_target":
                stack_bulk_target,

            "stack_absolute_minimum":
                stack_absolute_minimum,

            "stack_hard_floor":
                stack_hard_floor,

            "stack_buyer_floor":
                stack_buyer_floor,

            "candidate_stack":
                candidate_stack,

            "deployment_stack":
                deployment_stack,

            "action":
                action,

            "recipe_id":
                as_int(
                    row.get(
                        "recipe_id"
                    )
                ),

            "all_inputs_bot_priceable":
                as_int(
                    row.get(
                        "all_inputs_bot_priceable"
                    )
                ),

            "single_buyer_safe":
                single_buyer_safe,

            "single_floor_safe":
                single_floor_safe,

            "stack_buyer_safe":
                stack_buyer_safe,

            "stack_90_safe":
                stack_90_safe,

            "stack_hard_floor_safe":
                stack_hard_floor_safe,

            "stack_bulk_target_safe":
                stack_bulk_target_safe,

            "non_authorized_unchanged":
                non_authorized_unchanged,

            "downward_only":
                downward_only,

            "activation_ready":
                0,

            "auto_live_promotion":
                0,
        })

    output = pd.DataFrame(
        rows
    ).sort_values(
        [
            "action",
            "itemid",
        ],
        kind="stable",
    )

    action_counts = {
        str(key): int(value)
        for key, value in (
            output[
                "action"
            ]
            .value_counts()
            .to_dict()
            .items()
        )
    }

    if (
        action_counts
        != EXPECTED_ACTION_COUNTS
    ):
        raise SellerTransitionAuditError(
            "Transition action counts changed. "
            f"Expected "
            f"{EXPECTED_ACTION_COUNTS}; "
            f"found {action_counts}."
        )

    safe = output[
        output[
            "action"
        ]
        == "SAFE_DOWNWARD_NORMALIZATION"
    ].copy()

    safe_ids = set(
        safe[
            "itemid"
        ]
        .astype(int)
        .tolist()
    )

    expected_ids = set(
        EXPECTED_SAFE_TARGETS
    )

    if safe_ids != expected_ids:
        raise SellerTransitionAuditError(
            "Safe seller transition set changed. "
            f"Expected {sorted(expected_ids)}; "
            f"found {sorted(safe_ids)}."
        )

    for itemid, expected in (
        EXPECTED_SAFE_TARGETS.items()
    ):
        row = safe[
            safe[
                "itemid"
            ]
            == itemid
        ].iloc[0]

        actual_single = as_int(
            row[
                "deployment_single"
            ]
        )

        actual_stack = as_int(
            row[
                "deployment_stack"
            ]
        )

        if (
            actual_single
            != expected["single"]
            or actual_stack
            != expected["stack"]
        ):
            raise SellerTransitionAuditError(
                f"{itemid} target mismatch: "
                f"expected "
                f"{expected['single']}/"
                f"{expected['stack']}, "
                f"found "
                f"{actual_single}/"
                f"{actual_stack}."
            )

    safety_columns = [
        "single_buyer_safe",
        "single_floor_safe",
        "stack_buyer_safe",
        "stack_90_safe",
        "stack_hard_floor_safe",
        "stack_bulk_target_safe",
        "non_authorized_unchanged",
        "downward_only",
    ]

    safety_failures = {}

    for column in safety_columns:
        count = int(
            (
                output[
                    column
                ]
                != 1
            ).sum()
        )

        safety_failures[
            column
        ] = count

    total_failures = sum(
        safety_failures.values()
    )

    if total_failures:
        bad = output[
            (
                output[
                    safety_columns
                ]
                != 1
            )
            .any(axis=1)
        ]

        print()
        print(
            "SAFETY FAILURES:"
        )
        print(
            bad[
                [
                    "itemid",
                    "name",
                    "action",
                    *safety_columns,
                ]
            ].to_string(
                index=False
            )
        )

        raise SellerTransitionAuditError(
            f"{total_failures} seller "
            "transition safety failures."
        )

    # Stamina Apple is an exact spread equality:
    # 1260 / 0.70 = 1800.
    #
    # Decimal arithmetic prevents the prior
    # floating-point false positive of 1801.
    stamina = output[
        output[
            "itemid"
        ]
        == 2390
    ]

    if len(stamina) != 1:
        raise SellerTransitionAuditError(
            "Missing Stamina Apple "
            "precision sentinel."
        )

    stamina_row = stamina.iloc[0]

    if (
        as_int(
            stamina_row[
                "stack_buyer_floor"
            ]
        )
        != 1800
    ):
        raise SellerTransitionAuditError(
            "Exact spread calculation failed "
            "for Stamina Apple."
        )

    summary = {
        "status":
            "PASS",

        "stage":
            "1C.9c_SAFE_SELLER_TRANSITION_AUDIT",

        "live_seller_rows":
            int(len(output)),

        "action_counts":
            action_counts,

        "safe_transition_items":
            int(len(safe)),

        "safe_transition_itemids":
            sorted(
                safe_ids
            ),

        "safe_transition_targets": {
            str(itemid): {
                "single":
                    int(
                        EXPECTED_SAFE_TARGETS[
                            itemid
                        ][
                            "single"
                        ]
                    ),

                "stack":
                    int(
                        EXPECTED_SAFE_TARGETS[
                            itemid
                        ][
                            "stack"
                        ]
                    ),
            }
            for itemid
            in sorted(
                EXPECTED_SAFE_TARGETS
            )
        },

        "safety_failures":
            safety_failures,

        "total_safety_failures":
            int(
                total_failures
            ),

        "history_price_influence_ready":
            0,

        "history_stock_influence_ready":
            0,

        "deployment_change_authorized":
            0,

        "activation_ready":
            0,

        "auto_live_promotions":
            0,
    }

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
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
    print("=" * 92)
    print(
        " Phase 1C.9c Safe Seller "
        "Transition Audit"
    )
    print("=" * 92)

    print()
    print(
        f"Live sellers audited:                  "
        f"{len(output):>6}"
    )

    print()
    print("Transition actions:")

    for key in [
        "HOLD_STABLE",
        "SAFE_DOWNWARD_NORMALIZATION",
        "DEFER_UPWARD_REPRICE_REVIEW",
        "HOLD_BUYER_OR_ECONOMIC_FLOOR",
        "DEFER_CRAFT_INPUT_PRICEABILITY",
        "DEFER_INVALID_THEORETICAL",
    ]:
        print(
            f"  {key:<38} "
            f"{action_counts.get(key, 0):>6}"
        )

    print()
    print(
        "Authorized candidate transitions:"
    )
    print()

    print(
        safe[
            [
                "itemid",
                "name",
                "current_single",
                "deployment_single",
                "current_stack",
                "deployment_stack",
                "live_buyer_single",
                "live_buyer_stack",
                "single_buyer_floor",
                "stack_buyer_floor",
            ]
        ]
        .sort_values(
            "itemid"
        )
        .to_string(
            index=False
        )
    )

    print()
    print("Safety checks:")

    for key in safety_columns:
        print(
            f"  {key:<38} "
            f"failures="
            f"{safety_failures[key]}"
        )

    print()
    print(
        "Stamina Apple exact stack "
        "spread floor: "
        f"{as_int(stamina_row['stack_buyer_floor'])}"
    )

    print()
    print(
        "History price influence ready:          0"
    )
    print(
        "History stock influence ready:          0"
    )
    print(
        "Deployment changes authorized:          0"
    )
    print(
        "Activation ready:                       0"
    )
    print(
        "Auto live promotions:                   0"
    )

    print()
    print(
        f"Audit:   {OUTPUT_FILE}"
    )
    print(
        f"Summary: {SUMMARY_FILE}"
    )

    print()
    print("PASS")

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
