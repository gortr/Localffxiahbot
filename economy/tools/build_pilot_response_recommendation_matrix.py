from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

OBS_ROOT = (
    ROOT
    / "economy"
    / "runtime-observations"
    / "player-market-response"
)

ANALYSIS_ROOT = (
    OBS_ROOT
    / "analysis"
)

OUTPUT_ROOT = (
    OBS_ROOT
    / "recommendations"
)

BASELINE_JSON = (
    OBS_ROOT
    / "baseline.json"
)

MIN_DECISION_WINDOW_HOURS = 72.0
MIN_PLAYER_EVENTS_FOR_ACTION = 3

MIN_LONGITUDINAL_OBSERVATIONS = 3
MIN_LONGITUDINAL_SPAN_HOURS = 24.0

ALLOWED_RECOMMENDATIONS = {
    "KEEP",
    "OBSERVE",
    "REVIEW_PRICE",
    "REVIEW_RATE",
    "REVIEW_STOCK",
    "DEFER",
}


class RecommendationError(RuntimeError):
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


def as_float(value: Any):
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass

    return float(value)


def load_analysis_summaries():
    records = []

    for path in sorted(
        ANALYSIS_ROOT.glob(
            "*-summary.json"
        )
    ):
        data = json.loads(
            path.read_text(
                encoding="utf-8",
            )
        )

        if data.get("phase") != "3D.4":
            continue

        if data.get("status") != "PASS":
            continue

        epoch = as_int(
            data.get(
                "observation_epoch"
            )
        )

        if epoch <= 0:
            continue

        prefix = path.name[
            :-len("-summary.json")
        ]

        metrics_path = (
            ANALYSIS_ROOT
            / (
                prefix
                + "-price-depth-sellthrough.csv"
            )
        )

        if not metrics_path.exists():
            raise RecommendationError(
                "Missing metrics file for "
                f"{path.name}"
            )

        records.append({
            "path":
                path,

            "metrics_path":
                metrics_path,

            "epoch":
                epoch,

            "summary":
                data,
        })

    if not records:
        raise RecommendationError(
            "No passing 3D.4 analysis "
            "observations found."
        )

    return records


def main() -> int:
    if not BASELINE_JSON.exists():
        raise RecommendationError(
            "Missing 3D baseline."
        )

    baseline = json.loads(
        BASELINE_JSON.read_text(
            encoding="utf-8",
        )
    )

    if baseline.get("status") != "PASS":
        raise RecommendationError(
            "3D.1 baseline is not PASS."
        )

    baseline_epoch = as_int(
        baseline.get(
            "baseline_epoch"
        )
    )

    if baseline_epoch <= 0:
        raise RecommendationError(
            "Invalid baseline epoch."
        )

    observations = (
        load_analysis_summaries()
    )

    latest = max(
        observations,
        key=lambda x: x["epoch"],
    )

    earliest = min(
        observations,
        key=lambda x: x["epoch"],
    )

    latest_summary = latest[
        "summary"
    ]

    metrics = pd.read_csv(
        latest["metrics_path"],
        low_memory=False,
    )

    if len(metrics) != 28:
        raise RecommendationError(
            "Expected 28 pilot forms in "
            f"latest analysis; found {len(metrics)}."
        )

    if as_int(
        latest_summary.get(
            "integrity_failures"
        )
    ) != 0:
        raise RecommendationError(
            "Latest 3D.4 analysis has "
            "integrity failures."
        )

    latest_epoch = latest[
        "epoch"
    ]

    observation_age_hours = round(
        (
            latest_epoch
            - baseline_epoch
        )
        / 3600,
        6,
    )

    analysis_observation_count = len(
        observations
    )

    analysis_span_hours = round(
        (
            latest["epoch"]
            - earliest["epoch"]
        )
        / 3600,
        6,
    )

    longitudinal_ready = int(
        analysis_observation_count
        >= MIN_LONGITUDINAL_OBSERVATIONS
        and analysis_span_hours
        >= MIN_LONGITUDINAL_SPAN_HOURS
    )

    item_rows = []

    for itemid, group in metrics.groupby(
        "itemid",
        sort=True,
    ):
        itemid = int(
            itemid
        )

        if len(group) != 2:
            raise RecommendationError(
                f"{itemid}: expected two forms, "
                f"found {len(group)}."
            )

        names = set(
            group[
                "name"
            ].astype(str)
        )

        lanes = set(
            group[
                "pilot_lane"
            ].astype(str)
        )

        if len(names) != 1:
            raise RecommendationError(
                f"{itemid}: inconsistent names."
            )

        if len(lanes) != 1:
            raise RecommendationError(
                f"{itemid}: inconsistent lanes."
            )

        name = next(
            iter(names)
        )

        lane = next(
            iter(lanes)
        )

        demand_sales = int(
            group[
                "ahbot_to_player_sales"
            ].fillna(0).sum()
        )

        supply_sales = int(
            group[
                "player_to_ahbot_sales"
            ].fillna(0).sum()
        )

        organic_trades = int(
            group[
                "organic_player_trades"
            ].fillna(0).sum()
        )

        total_player_events = (
            demand_sales
            + supply_sales
            + organic_trades
        )

        current_ahbot_depth = int(
            group[
                "current_active_ahbot"
            ].fillna(0).sum()
        )

        current_player_depth = int(
            group[
                "current_active_player"
            ].fillna(0).sum()
        )

        current_eligible_depth = int(
            group[
                "current_eligible_player_depth"
            ].fillna(0).sum()
        )

        ahbot_opportunities = int(
            group[
                "ahbot_listing_opportunities"
            ].fillna(0).sum()
        )

        player_opportunities = int(
            group[
                "player_listing_opportunities"
            ].fillna(0).sum()
        )

        eligible_player_opportunities = int(
            group[
                "eligible_player_opportunities"
            ].fillna(0).sum()
        )

        player_gil_sink = int(
            group[
                "player_gil_sink"
            ].fillna(0).sum()
        )

        ahbot_gil_faucet = int(
            group[
                "ahbot_gil_faucet"
            ].fillna(0).sum()
        )

        net_player_gil_sink = (
            player_gil_sink
            - ahbot_gil_faucet
        )

        unclassified = int(
            group[
                "unclassified_events"
            ].fillna(0).sum()
        )

        active_unknown = int(
            group[
                "current_active_unknown"
            ].fillna(0).sum()
        )

        signal_states = (
            sorted(
                set(
                    group[
                        "signal_state"
                    ].astype(str)
                )
            )
        )

        # --------------------------------------------
        # Recommendation contract
        #
        # This is advisory only.
        # It never writes runtime or DB state.
        # --------------------------------------------

        safety_issue = (
            unclassified > 0
            or active_unknown > 0
        )

        recommendation = "OBSERVE"
        reason = ""
        evidence_ready = 0

        if safety_issue:
            recommendation = "DEFER"
            reason = (
                "INTEGRITY_OR_CLASSIFICATION_"
                "ISSUE"
            )

        elif (
            observation_age_hours
            < MIN_DECISION_WINDOW_HOURS
        ):
            recommendation = "OBSERVE"
            reason = (
                "MINIMUM_OBSERVATION_"
                "WINDOW_NOT_MET"
            )

        elif (
            total_player_events
            < MIN_PLAYER_EVENTS_FOR_ACTION
        ):
            recommendation = "OBSERVE"
            reason = (
                "INSUFFICIENT_PLAYER_EVIDENCE"
            )

        else:
            evidence_ready = 1

            if lane == "PILOT_A_SELLER":
                if (
                    demand_sales
                    >= MIN_PLAYER_EVENTS_FOR_ACTION
                ):
                    recommendation = "KEEP"
                    reason = (
                        "PLAYER_DEMAND_CONFIRMED"
                    )

                elif (
                    organic_trades
                    >= MIN_PLAYER_EVENTS_FOR_ACTION
                    and demand_sales == 0
                ):
                    recommendation = (
                        "REVIEW_PRICE"
                    )

                    reason = (
                        "ORGANIC_MARKET_ACTIVITY_"
                        "WITHOUT_AHBOT_DEMAND"
                    )

                else:
                    recommendation = "OBSERVE"
                    reason = (
                        "PLAYER_SIGNAL_PRESENT_"
                        "BUT_NOT_DIRECTIONAL"
                    )

            elif lane == "PILOT_B_BUYER":
                if (
                    supply_sales
                    >= MIN_PLAYER_EVENTS_FOR_ACTION
                ):
                    recommendation = "KEEP"
                    reason = (
                        "PLAYER_SUPPLY_CAPTURE_"
                        "CONFIRMED"
                    )

                elif (
                    player_opportunities
                    >= MIN_PLAYER_EVENTS_FOR_ACTION
                    and eligible_player_opportunities
                    == 0
                ):
                    recommendation = (
                        "REVIEW_PRICE"
                    )

                    reason = (
                        "PLAYER_SUPPLY_EXISTS_"
                        "ABOVE_CURRENT_BID"
                    )

                elif (
                    eligible_player_opportunities
                    >= MIN_PLAYER_EVENTS_FOR_ACTION
                    and supply_sales == 0
                ):
                    if longitudinal_ready:
                        recommendation = (
                            "REVIEW_RATE"
                        )

                        reason = (
                            "ELIGIBLE_SUPPLY_"
                            "PERSISTS_WITHOUT_CAPTURE"
                        )
                    else:
                        recommendation = (
                            "OBSERVE"
                        )

                        reason = (
                            "ELIGIBLE_SUPPLY_PRESENT_"
                            "LONGITUDINAL_GATE_NOT_MET"
                        )

                else:
                    recommendation = "OBSERVE"
                    reason = (
                        "PLAYER_SIGNAL_PRESENT_"
                        "BUT_NOT_DIRECTIONAL"
                    )

            else:
                recommendation = "DEFER"
                reason = (
                    "UNKNOWN_PILOT_LANE"
                )

        if (
            recommendation
            not in ALLOWED_RECOMMENDATIONS
        ):
            raise RecommendationError(
                f"{itemid}: invalid recommendation "
                f"{recommendation}"
            )

        # REVIEW_STOCK is intentionally impossible
        # from a single cumulative reconstruction.
        #
        # Stock review requires longitudinal depth
        # persistence. This flag records whether
        # the global observation cadence has even
        # matured enough to consider it later.
        stock_review_gate_ready = int(
            longitudinal_ready
            and evidence_ready
        )

        item_rows.append({
            "itemid":
                itemid,

            "name":
                name,

            "pilot_lane":
                lane,

            "observation_age_hours":
                observation_age_hours,

            "analysis_observations":
                analysis_observation_count,

            "analysis_span_hours":
                analysis_span_hours,

            "longitudinal_ready":
                longitudinal_ready,

            "minimum_player_events":
                MIN_PLAYER_EVENTS_FOR_ACTION,

            "player_events":
                total_player_events,

            "ahbot_to_player_sales":
                demand_sales,

            "player_to_ahbot_sales":
                supply_sales,

            "organic_player_trades":
                organic_trades,

            "current_ahbot_depth":
                current_ahbot_depth,

            "current_player_depth":
                current_player_depth,

            "current_eligible_player_depth":
                current_eligible_depth,

            "ahbot_listing_opportunities":
                ahbot_opportunities,

            "player_listing_opportunities":
                player_opportunities,

            "eligible_player_opportunities":
                eligible_player_opportunities,

            "player_gil_sink":
                player_gil_sink,

            "ahbot_gil_faucet":
                ahbot_gil_faucet,

            "net_player_gil_sink":
                net_player_gil_sink,

            "unclassified_events":
                unclassified,

            "active_unknown":
                active_unknown,

            "signal_states":
                "|".join(
                    signal_states
                ),

            "evidence_ready":
                evidence_ready,

            "stock_review_gate_ready":
                stock_review_gate_ready,

            "recommendation":
                recommendation,

            "recommendation_reason":
                reason,

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
        })

    matrix = pd.DataFrame(
        item_rows
    ).sort_values(
        [
            "pilot_lane",
            "itemid",
        ]
    )

    if len(matrix) != 14:
        raise RecommendationError(
            "Expected 14 item recommendations; "
            f"found {len(matrix)}."
        )

    mutation_columns = [
        "price_change_authorized",
        "rate_change_authorized",
        "stock_change_authorized",
        "catalog_change_authorized",
        "runtime_mutation_authorized",
        "database_mutation_authorized",
        "auto_live_promotion",
    ]

    for column in mutation_columns:
        if int(
            matrix[
                column
            ].sum()
        ) != 0:
            raise RecommendationError(
                f"Mutation authority detected "
                f"in {column}."
            )

    recommendation_counts = (
        matrix[
            "recommendation"
        ]
        .value_counts()
        .sort_index()
        .to_dict()
    )

    items_with_evidence = int(
        matrix[
            "evidence_ready"
        ].sum()
    )

    items_with_player_events = int(
        (
            matrix[
                "player_events"
            ]
            > 0
        ).sum()
    )

    safety_deferrals = int(
        (
            matrix[
                "recommendation"
            ]
            == "DEFER"
        ).sum()
    )

    # A recommendation matrix can PASS even when
    # every item is OBSERVE. That is the correct
    # fail-closed result for insufficient evidence.
    status = "PASS"

    stamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    OUTPUT_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    matrix_file = (
        OUTPUT_ROOT
        / f"{stamp}-item-matrix.csv"
    )

    form_file = (
        OUTPUT_ROOT
        / f"{stamp}-form-evidence.csv"
    )

    summary_file = (
        OUTPUT_ROOT
        / f"{stamp}-summary.json"
    )

    matrix.to_csv(
        matrix_file,
        index=False,
    )

    metrics.to_csv(
        form_file,
        index=False,
    )

    summary = {
        "status":
            status,

        "phase":
            "3D.5",

        "observation_utc":
            latest_summary.get(
                "observation_utc"
            ),

        "baseline_utc":
            baseline.get(
                "baseline_utc"
            ),

        "observation_age_hours":
            observation_age_hours,

        "minimum_decision_window_hours":
            MIN_DECISION_WINDOW_HOURS,

        "minimum_player_events_for_action":
            MIN_PLAYER_EVENTS_FOR_ACTION,

        "analysis_observation_count":
            analysis_observation_count,

        "analysis_span_hours":
            analysis_span_hours,

        "minimum_longitudinal_observations":
            MIN_LONGITUDINAL_OBSERVATIONS,

        "minimum_longitudinal_span_hours":
            MIN_LONGITUDINAL_SPAN_HOURS,

        "longitudinal_ready":
            longitudinal_ready,

        "pilot_items":
            len(matrix),

        "items_with_player_events":
            items_with_player_events,

        "items_with_actionable_evidence":
            items_with_evidence,

        "safety_deferrals":
            safety_deferrals,

        "recommendation_counts":
            recommendation_counts,

        "latest_analysis_summary":
            str(
                latest[
                    "path"
                ]
            ),

        "latest_analysis_metrics":
            str(
                latest[
                    "metrics_path"
                ]
            ),

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
            "item_matrix":
                str(
                    matrix_file
                ),

            "form_evidence":
                str(
                    form_file
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

    print("=" * 110)
    print(
        " Phase 3D.5 Pilot A + B "
        "Recommendation Matrix"
    )
    print("=" * 110)
    print()

    display_columns = [
        "itemid",
        "name",
        "pilot_lane",
        "observation_age_hours",
        "player_events",
        "ahbot_to_player_sales",
        "player_to_ahbot_sales",
        "organic_player_trades",
        "current_ahbot_depth",
        "current_player_depth",
        "evidence_ready",
        "recommendation",
        "recommendation_reason",
    ]

    print(
        matrix[
            display_columns
        ].to_string(
            index=False
        )
    )

    print()
    print(
        "Observation age hours:          ",
        observation_age_hours,
    )

    print(
        "Minimum decision window:        ",
        MIN_DECISION_WINDOW_HOURS,
    )

    print()
    print(
        "Analysis observations:          ",
        analysis_observation_count,
    )

    print(
        "Analysis span hours:             ",
        analysis_span_hours,
    )

    print(
        "Longitudinal ready:              ",
        longitudinal_ready,
    )

    print()
    print(
        "Items with player events:        ",
        items_with_player_events,
    )

    print(
        "Items with actionable evidence:  ",
        items_with_evidence,
    )

    print(
        "Safety deferrals:                ",
        safety_deferrals,
    )

    print()
    print("Recommendation counts:")

    for key in sorted(
        recommendation_counts
    ):
        print(
            f"  {key:<14}",
            recommendation_counts[
                key
            ],
        )

    print()
    print(
        "Price change authorized:        0"
    )

    print(
        "Rate change authorized:         0"
    )

    print(
        "Stock change authorized:        0"
    )

    print(
        "Catalog change authorized:      0"
    )

    print(
        "Runtime mutation authorized:    0"
    )

    print(
        "Database mutation authorized:   0"
    )

    print(
        "Auto live promotion:            0"
    )

    print()
    print(
        "Matrix:",
        matrix_file,
    )

    print(
        "Form evidence:",
        form_file,
    )

    print(
        "Summary:",
        summary_file,
    )

    print()
    print(status)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
