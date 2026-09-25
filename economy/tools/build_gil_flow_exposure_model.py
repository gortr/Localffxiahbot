from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

OBS_ROOT = (
    ROOT
    / "economy"
    / "runtime-observations"
    / "gil-flow"
)

FAUCET_ROOT = (
    OBS_ROOT
    / "faucet"
)

SINK_ROOT = (
    OBS_ROOT
    / "sink"
)

MODEL_ROOT = (
    OBS_ROOT
    / "model"
)

BASELINE_JSON = (
    OBS_ROOT
    / "baseline.json"
)


class FlowModelError(RuntimeError):
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


def latest_passing_summary(
    directory: Path,
    phase: str,
) -> tuple[Path, dict]:
    found = []

    for path in sorted(
        directory.glob(
            "*-summary.json"
        )
    ):
        data = json.loads(
            path.read_text(
                encoding="utf-8",
            )
        )

        if (
            data.get("phase") == phase
            and data.get("status") == "PASS"
        ):
            found.append(
                (
                    path,
                    data,
                )
            )

    if not found:
        raise FlowModelError(
            f"No passing {phase} summary found "
            f"in {directory}."
        )

    return found[-1]


def load_csv_from_summary(
    summary: dict,
    key: str,
) -> pd.DataFrame:
    files = summary.get(
        "files",
        {},
    )

    path_text = files.get(
        key
    )

    if not path_text:
        raise FlowModelError(
            f"Summary does not provide file: {key}"
        )

    path = Path(
        path_text
    )

    if not path.exists():
        raise FlowModelError(
            f"Missing referenced file: {path}"
        )

    return pd.read_csv(
        path,
        low_memory=False,
    )


def concentration_metrics(
    values: pd.Series,
) -> dict:
    values = (
        values
        .astype(float)
        .clip(lower=0)
    )

    total = float(
        values.sum()
    )

    if total <= 0:
        return {
            "total":
                0.0,

            "largest_share":
                0.0,

            "top_5_share":
                0.0,

            "hhi":
                0.0,
        }

    shares = (
        values
        / total
    ).sort_values(
        ascending=False
    )

    return {
        "total":
            total,

        "largest_share":
            round(
                float(
                    shares.iloc[0]
                ),
                6,
            ),

        "top_5_share":
            round(
                float(
                    shares.iloc[:5].sum()
                ),
                6,
            ),

        "hhi":
            round(
                float(
                    (
                        shares
                        * shares
                    ).sum()
                ),
                6,
            ),
    }


def safe_ratio(
    numerator: float,
    denominator: float,
):
    if denominator <= 0:
        return None

    return round(
        numerator
        / denominator,
        6,
    )


def main() -> int:
    if not BASELINE_JSON.exists():
        raise FlowModelError(
            "Missing 3E.1 baseline."
        )

    baseline = json.loads(
        BASELINE_JSON.read_text(
            encoding="utf-8",
        )
    )

    if baseline.get("status") != "PASS":
        raise FlowModelError(
            "3E.1 baseline is not PASS."
        )

    if not baseline.get(
        "baseline_locked",
        False,
    ):
        raise FlowModelError(
            "3E.1 baseline is not locked."
        )

    faucet_path, faucet = (
        latest_passing_summary(
            FAUCET_ROOT,
            "3E.2",
        )
    )

    sink_path, sink = (
        latest_passing_summary(
            SINK_ROOT,
            "3E.3",
        )
    )

    baseline_epoch = as_int(
        baseline.get(
            "baseline_epoch"
        )
    )

    if (
        as_int(
            faucet.get(
                "baseline_epoch"
            )
        )
        != baseline_epoch
    ):
        raise FlowModelError(
            "3E.2 baseline epoch differs "
            "from 3E.1."
        )

    if (
        as_int(
            sink.get(
                "baseline_epoch"
            )
        )
        != baseline_epoch
    ):
        raise FlowModelError(
            "3E.3 baseline epoch differs "
            "from 3E.1."
        )

    if (
        faucet.get(
            "status"
        )
        != "PASS"
        or sink.get(
            "status"
        )
        != "PASS"
    ):
        raise FlowModelError(
            "Faucet or sink ledger is not PASS."
        )

    if as_int(
        faucet.get(
            "integrity_failures"
        )
    ) != 0:
        raise FlowModelError(
            "Faucet ledger has "
            "integrity failures."
        )

    if as_int(
        sink.get(
            "integrity_failures"
        )
    ) != 0:
        raise FlowModelError(
            "Sink ledger has "
            "integrity failures."
        )

    faucet_ledger = (
        load_csv_from_summary(
            faucet,
            "ledger",
        )
    )

    sink_ledger = (
        load_csv_from_summary(
            sink,
            "ledger",
        )
    )

    # --------------------------------------------------
    # Actual economic flow
    # --------------------------------------------------

    baseline_faucet = as_int(
        baseline.get(
            "gross_faucet_gil"
        )
    )

    baseline_sink = as_int(
        baseline.get(
            "gross_sink_gil"
        )
    )

    baseline_net_sink = (
        baseline_sink
        - baseline_faucet
    )

    post_faucet = as_int(
        faucet.get(
            "postbaseline_faucet_gil"
        )
    )

    post_sink = as_int(
        sink.get(
            "postbaseline_sink_gil"
        )
    )

    post_net_sink = (
        post_sink
        - post_faucet
    )

    cumulative_faucet = (
        baseline_faucet
        + post_faucet
    )

    cumulative_sink = (
        baseline_sink
        + post_sink
    )

    cumulative_net_sink = (
        cumulative_sink
        - cumulative_faucet
    )

    if post_net_sink > 0:
        actual_flow_state = (
            "POSTBASELINE_NET_SINK"
        )

    elif post_net_sink < 0:
        actual_flow_state = (
            "POSTBASELINE_NET_FAUCET"
        )

    else:
        actual_flow_state = (
            "NO_POSTBASELINE_NET_FLOW"
        )

    # --------------------------------------------------
    # Buyer stress exposure
    # --------------------------------------------------

    expected_faucet_per_day = as_float(
        faucet.get(
            "expected_daily_gil_continuous_supply"
        )
    )

    absolute_faucet_ceiling_per_day = (
        as_float(
            faucet.get(
                "absolute_rng_success_ceiling_gil_per_day"
            )
        )
    )

    # --------------------------------------------------
    # Seller inventory / turnover reference
    #
    # These are NOT forecasts of player demand.
    # --------------------------------------------------

    current_inventory_value = as_float(
        sink.get(
            "current_ahbot_inventory_ask_value"
        )
    )

    configured_target_value = as_float(
        sink.get(
            "configured_total_market_target_ask_value"
        )
    )

    restocks_per_day = as_float(
        sink.get(
            "restocks_per_day"
        )
    )

    if restocks_per_day <= 0:
        raise FlowModelError(
            "Invalid restocks_per_day."
        )

    restock_window_hours = (
        24.0
        / restocks_per_day
    )

    expected_faucet_per_restock_window = (
        expected_faucet_per_day
        / restocks_per_day
    )

    absolute_faucet_per_restock_window = (
        absolute_faucet_ceiling_per_day
        / restocks_per_day
    )

    # Notional reference:
    #
    # If one full configured target-value inventory
    # turned over during every restock window, this
    # would be the corresponding sink scale.
    #
    # This is intentionally NOT called expected sink.
    notional_target_turnover_reference_per_day = (
        configured_target_value
        * restocks_per_day
    )

    notional_current_inventory_turnover_reference = (
        current_inventory_value
        * restocks_per_day
    )

    expected_faucet_to_current_inventory_ratio = (
        safe_ratio(
            expected_faucet_per_day,
            current_inventory_value,
        )
    )

    expected_faucet_to_target_value_ratio = (
        safe_ratio(
            expected_faucet_per_day,
            configured_target_value,
        )
    )

    expected_faucet_share_of_notional_target_turnover = (
        safe_ratio(
            expected_faucet_per_day,
            notional_target_turnover_reference_per_day,
        )
    )

    absolute_faucet_share_of_notional_target_turnover = (
        safe_ratio(
            absolute_faucet_ceiling_per_day,
            notional_target_turnover_reference_per_day,
        )
    )

    expected_faucet_window_share_of_target = (
        safe_ratio(
            expected_faucet_per_restock_window,
            configured_target_value,
        )
    )

    current_inventory_stress_coverage_hours = None

    if expected_faucet_per_day > 0:
        current_inventory_stress_coverage_hours = (
            round(
                (
                    current_inventory_value
                    / expected_faucet_per_day
                )
                * 24.0,
                6,
            )
        )

    target_value_stress_coverage_hours = None

    if expected_faucet_per_day > 0:
        target_value_stress_coverage_hours = (
            round(
                (
                    configured_target_value
                    / expected_faucet_per_day
                )
                * 24.0,
                6,
            )
        )

    # --------------------------------------------------
    # Concentration
    # --------------------------------------------------

    buyer_item = (
        faucet_ledger.groupby(
            [
                "itemid",
                "name",
            ],
            as_index=False,
        )[
            [
                "expected_gil_per_day_continuous_supply",
                "absolute_rng_success_ceiling_gil_per_day",
                "ahbot_faucet_gil",
            ]
        ]
        .sum()
    )

    sink_item = (
        sink_ledger.groupby(
            [
                "itemid",
                "name",
            ],
            as_index=False,
        )[
            [
                "current_ahbot_inventory_ask_value",
                "configured_total_market_target_ask_value",
                "postbaseline_player_gil_sink",
            ]
        ]
        .sum()
    )

    buyer_concentration = (
        concentration_metrics(
            buyer_item[
                "expected_gil_per_day_continuous_supply"
            ]
        )
    )

    current_sink_concentration = (
        concentration_metrics(
            sink_item[
                "current_ahbot_inventory_ask_value"
            ]
        )
    )

    target_sink_concentration = (
        concentration_metrics(
            sink_item[
                "configured_total_market_target_ask_value"
            ]
        )
    )

    buyer_item = buyer_item.rename(
        columns={
            "expected_gil_per_day_continuous_supply":
                "buyer_expected_faucet_per_day",

            "absolute_rng_success_ceiling_gil_per_day":
                "buyer_absolute_ceiling_per_day",

            "ahbot_faucet_gil":
                "actual_postbaseline_faucet_gil",
        }
    )

    sink_item = sink_item.rename(
        columns={
            "current_ahbot_inventory_ask_value":
                "seller_current_inventory_ask_value",

            "configured_total_market_target_ask_value":
                "seller_configured_target_ask_value",

            "postbaseline_player_gil_sink":
                "actual_postbaseline_sink_gil",
        }
    )

    item_model = pd.merge(
        buyer_item,
        sink_item,
        how="outer",
        on=[
            "itemid",
            "name",
        ],
    ).fillna(0)

    numeric_columns = [
        "buyer_expected_faucet_per_day",
        "buyer_absolute_ceiling_per_day",
        "actual_postbaseline_faucet_gil",
        "seller_current_inventory_ask_value",
        "seller_configured_target_ask_value",
        "actual_postbaseline_sink_gil",
    ]

    for column in numeric_columns:
        item_model[
            column
        ] = (
            item_model[
                column
            ]
            .astype(float)
        )

    item_model[
        "actual_postbaseline_net_player_gil_sink"
    ] = (
        item_model[
            "actual_postbaseline_sink_gil"
        ]
        - item_model[
            "actual_postbaseline_faucet_gil"
        ]
    )

    item_model[
        "buyer_exposure_to_current_inventory_ratio"
    ] = item_model.apply(
        lambda row: (
            safe_ratio(
                row[
                    "buyer_expected_faucet_per_day"
                ],
                row[
                    "seller_current_inventory_ask_value"
                ],
            )
        ),
        axis=1,
    )

    item_model[
        "buyer_exposure_to_target_value_ratio"
    ] = item_model.apply(
        lambda row: (
            safe_ratio(
                row[
                    "buyer_expected_faucet_per_day"
                ],
                row[
                    "seller_configured_target_ask_value"
                ],
            )
        ),
        axis=1,
    )

    if expected_faucet_per_day > 0:
        item_model[
            "buyer_expected_exposure_share"
        ] = (
            item_model[
                "buyer_expected_faucet_per_day"
            ]
            / expected_faucet_per_day
        )
    else:
        item_model[
            "buyer_expected_exposure_share"
        ] = 0.0

    if current_inventory_value > 0:
        item_model[
            "seller_current_inventory_share"
        ] = (
            item_model[
                "seller_current_inventory_ask_value"
            ]
            / current_inventory_value
        )
    else:
        item_model[
            "seller_current_inventory_share"
        ] = 0.0

    item_model[
        "buyer_expected_exposure_share"
    ] = (
        item_model[
            "buyer_expected_exposure_share"
        ]
        .round(6)
    )

    item_model[
        "seller_current_inventory_share"
    ] = (
        item_model[
            "seller_current_inventory_share"
        ]
        .round(6)
    )

    item_model = item_model.sort_values(
        [
            "buyer_expected_faucet_per_day",
            "seller_current_inventory_ask_value",
        ],
        ascending=[
            False,
            False,
        ],
    )

    # --------------------------------------------------
    # Safety / integrity
    # --------------------------------------------------

    failures = []

    if as_int(
        faucet.get(
            "integrity_failures"
        )
    ) != 0:
        failures.append(
            "Faucet ledger integrity failure."
        )

    if as_int(
        sink.get(
            "integrity_failures"
        )
    ) != 0:
        failures.append(
            "Sink ledger integrity failure."
        )

    if as_int(
        sink.get(
            "active_ahbot_outside_runtime"
        )
    ) != 0:
        failures.append(
            "Active AHBot seller rows exist "
            "outside seller runtime."
        )

    if as_int(
        sink.get(
            "active_ahbot_price_mismatches"
        )
    ) != 0:
        failures.append(
            "Active AHBot seller price "
            "mismatches exist."
        )

    if as_int(
        faucet.get(
            "one_roll_per_item_form_per_cycle"
        )
    ) != 1:
        failures.append(
            "Buyer rate hardening not present."
        )

    if failures:
        model_state = (
            "EXPOSURE_MODEL_BLOCKED"
        )
    else:
        model_state = (
            "EXPOSURE_MODEL_READY"
        )

    MODEL_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    item_file = (
        MODEL_ROOT
        / "current-item-exposure-model.csv"
    )

    summary_file = (
        MODEL_ROOT
        / "current-exposure-summary.json"
    )

    item_model.to_csv(
        item_file,
        index=False,
    )

    summary = {
        "status":
            "PASS"
            if not failures
            else "FAIL",

        "phase":
            "3E.4",

        "model_state":
            model_state,

        "baseline_utc":
            baseline.get(
                "baseline_utc"
            ),

        "faucet_observation_utc":
            faucet.get(
                "observation_utc"
            ),

        "sink_observation_utc":
            sink.get(
                "observation_utc"
            ),

        "actual_flow": {
            "baseline_faucet_gil":
                baseline_faucet,

            "baseline_sink_gil":
                baseline_sink,

            "baseline_net_player_gil_sink":
                baseline_net_sink,

            "postbaseline_faucet_gil":
                post_faucet,

            "postbaseline_sink_gil":
                post_sink,

            "postbaseline_net_player_gil_sink":
                post_net_sink,

            "actual_flow_state":
                actual_flow_state,

            "cumulative_faucet_gil":
                cumulative_faucet,

            "cumulative_sink_gil":
                cumulative_sink,

            "cumulative_net_player_gil_sink":
                cumulative_net_sink,
        },

        "buyer_stress_exposure": {
            "expected_gil_per_day_continuous_supply":
                expected_faucet_per_day,

            "absolute_rng_success_ceiling_gil_per_day":
                absolute_faucet_ceiling_per_day,

            "expected_gil_per_restock_window":
                round(
                    expected_faucet_per_restock_window,
                    6,
                ),

            "absolute_rng_ceiling_per_restock_window":
                round(
                    absolute_faucet_per_restock_window,
                    6,
                ),
        },

        "seller_inventory_reference": {
            "current_ahbot_inventory_ask_value":
                current_inventory_value,

            "configured_total_market_target_ask_value":
                configured_target_value,

            "restocks_per_day":
                restocks_per_day,

            "restock_window_hours":
                restock_window_hours,

            "notional_current_inventory_turnover_reference_per_day":
                notional_current_inventory_turnover_reference,

            "notional_target_turnover_reference_per_day":
                notional_target_turnover_reference_per_day,
        },

        "scale_ratios": {
            "expected_faucet_to_current_inventory_ratio":
                expected_faucet_to_current_inventory_ratio,

            "expected_faucet_to_target_value_ratio":
                expected_faucet_to_target_value_ratio,

            "expected_faucet_share_of_notional_target_turnover_reference":
                expected_faucet_share_of_notional_target_turnover,

            "absolute_faucet_share_of_notional_target_turnover_reference":
                absolute_faucet_share_of_notional_target_turnover,

            "expected_faucet_window_share_of_target_value":
                expected_faucet_window_share_of_target,

            "current_inventory_stress_coverage_hours":
                current_inventory_stress_coverage_hours,

            "target_value_stress_coverage_hours":
                target_value_stress_coverage_hours,
        },

        "buyer_exposure_concentration": {
            "largest_item_share":
                buyer_concentration[
                    "largest_share"
                ],

            "top_5_item_share":
                buyer_concentration[
                    "top_5_share"
                ],

            "hhi":
                buyer_concentration[
                    "hhi"
                ],
        },

        "current_sink_inventory_concentration": {
            "largest_item_share":
                current_sink_concentration[
                    "largest_share"
                ],

            "top_5_item_share":
                current_sink_concentration[
                    "top_5_share"
                ],

            "hhi":
                current_sink_concentration[
                    "hhi"
                ],
        },

        "configured_target_concentration": {
            "largest_item_share":
                target_sink_concentration[
                    "largest_share"
                ],

            "top_5_item_share":
                target_sink_concentration[
                    "top_5_share"
                ],

            "hhi":
                target_sink_concentration[
                    "hhi"
                ],
        },

        "interpretation_contract": {
            "actual_flow_is_observed":
                True,

            "buyer_expected_exposure_is_forecast":
                False,

            "buyer_expected_exposure_is_stress_model":
                True,

            "seller_turnover_reference_is_forecast":
                False,

            "seller_turnover_reference_is_capacity_reference":
                True,

            "scale_ratios_authorize_changes":
                False,
        },

        "runtime_mutation_performed":
            0,

        "database_mutation_performed":
            0,

        "price_change_authorized":
            0,

        "rate_change_authorized":
            0,

        "stock_change_authorized":
            0,

        "catalog_change_authorized":
            0,

        "auto_live_promotion":
            0,

        "source_summaries": {
            "faucet":
                str(
                    faucet_path
                ),

            "sink":
                str(
                    sink_path
                ),
        },

        "files": {
            "item_exposure_model":
                str(
                    item_file
                ),
        },

        "failures":
            failures,
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
        " Phase 3E.4 Net Flow + Exposure Model"
    )
    print("=" * 112)
    print()

    print("ACTUAL GIL FLOW")
    print()

    print(
        "Historical faucet at T0:              ",
        baseline_faucet,
    )

    print(
        "Historical sink at T0:                ",
        baseline_sink,
    )

    print(
        "Historical net player gil sink:       ",
        baseline_net_sink,
    )

    print()
    print(
        "Post-baseline faucet:                 ",
        post_faucet,
    )

    print(
        "Post-baseline sink:                   ",
        post_sink,
    )

    print(
        "Post-baseline net player gil sink:    ",
        post_net_sink,
    )

    print(
        "Actual flow state:                    ",
        actual_flow_state,
    )

    print()
    print("BUYER STRESS EXPOSURE")
    print()

    print(
        "Expected continuous-supply gil/day:   ",
        round(
            expected_faucet_per_day,
            6,
        ),
    )

    print(
        "Absolute RNG ceiling gil/day:         ",
        round(
            absolute_faucet_ceiling_per_day,
            6,
        ),
    )

    print(
        "Expected gil / restock window:        ",
        round(
            expected_faucet_per_restock_window,
            6,
        ),
    )

    print()
    print("SELLER INVENTORY REFERENCE")
    print()

    print(
        "Current AHBot inventory ask value:    ",
        round(
            current_inventory_value,
            6,
        ),
    )

    print(
        "Configured total-market target value: ",
        round(
            configured_target_value,
            6,
        ),
    )

    print(
        "Restocks per day:                     ",
        restocks_per_day,
    )

    print(
        "Notional target turnover reference:   ",
        round(
            notional_target_turnover_reference_per_day,
            6,
        ),
    )

    print()
    print("SCALE RATIOS")
    print()

    print(
        "Expected faucet / current inventory:   ",
        expected_faucet_to_current_inventory_ratio,
    )

    print(
        "Expected faucet / target value:        ",
        expected_faucet_to_target_value_ratio,
    )

    print(
        "Expected faucet / notional target "
        "turnover:                            ",
        expected_faucet_share_of_notional_target_turnover,
    )

    print(
        "Expected faucet per restock window / "
        "target:                              ",
        expected_faucet_window_share_of_target,
    )

    print(
        "Current inventory stress coverage h:  ",
        current_inventory_stress_coverage_hours,
    )

    print(
        "Target value stress coverage h:       ",
        target_value_stress_coverage_hours,
    )

    print()
    print("CONCENTRATION")
    print()

    print(
        "Buyer largest item share:             ",
        buyer_concentration[
            "largest_share"
        ],
    )

    print(
        "Buyer top-5 share:                    ",
        buyer_concentration[
            "top_5_share"
        ],
    )

    print(
        "Buyer exposure HHI:                   ",
        buyer_concentration[
            "hhi"
        ],
    )

    print()
    print(
        "Seller largest current item share:    ",
        current_sink_concentration[
            "largest_share"
        ],
    )

    print(
        "Seller current top-5 share:           ",
        current_sink_concentration[
            "top_5_share"
        ],
    )

    print(
        "Seller current HHI:                   ",
        current_sink_concentration[
            "hhi"
        ],
    )

    print()
    print(
        "Model state:",
        model_state,
    )

    print()
    print(
        "IMPORTANT:"
    )

    print(
        "  Buyer expected exposure is a "
        "continuous-supply stress model."
    )

    print(
        "  Seller turnover reference is a "
        "scale reference, not predicted demand."
    )

    print(
        "  These ratios do not authorize "
        "economic changes."
    )

    print()
    print(
        "Runtime mutation performed:           0"
    )

    print(
        "Database mutation performed:          0"
    )

    print(
        "Price change authorized:              0"
    )

    print(
        "Rate change authorized:               0"
    )

    print(
        "Stock change authorized:              0"
    )

    print(
        "Catalog change authorized:            0"
    )

    print(
        "Auto live promotion:                  0"
    )

    print()
    print(
        "Item model:",
        item_file,
    )

    print(
        "Summary:",
        summary_file,
    )

    print()
    print(summary["status"])

    if failures:
        print()
        print("Failures:")

        for failure in failures:
            print(
                " -",
                failure,
            )

        raise SystemExit(1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
