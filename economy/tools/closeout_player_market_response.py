from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


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
    / "player-market-response"
)

SUPPLY_ROOT = (
    OBS_ROOT
    / "supply"
)

DEMAND_ROOT = (
    OBS_ROOT
    / "demand"
)

ANALYSIS_ROOT = (
    OBS_ROOT
    / "analysis"
)

RECOMMENDATION_ROOT = (
    OBS_ROOT
    / "recommendations"
)

CLOSEOUT_ROOT = (
    OBS_ROOT
    / "closeout"
)

BASELINE_JSON = (
    OBS_ROOT
    / "baseline.json"
)

SELLER_RUNTIME = (
    GENERATED
    / "market-sell.csv"
)

BUYER_RUNTIME = (
    GENERATED
    / "market-buy.csv"
)


class CloseoutError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(
                1024 * 1024
            ),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


def as_int(value: Any) -> int:
    if value is None:
        return 0

    try:
        return int(
            float(value)
        )
    except (
        TypeError,
        ValueError,
    ):
        return 0


def as_float(value: Any) -> float:
    if value is None:
        return 0.0

    try:
        return float(value)
    except (
        TypeError,
        ValueError,
    ):
        return 0.0


def latest_passing_summary(
    directory: Path,
    phase: str,
) -> tuple[Path, dict]:
    matches = []

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
            matches.append(
                (
                    path,
                    data,
                )
            )

    if not matches:
        raise CloseoutError(
            f"No passing {phase} summaries "
            f"found in {directory}."
        )

    return matches[-1]


def require_zero_authority(
    label: str,
    data: dict,
) -> None:
    keys = [
        "price_influence_authorized",
        "stock_influence_authorized",
        "catalog_mutation_authorized",
        "price_change_authorized",
        "rate_change_authorized",
        "stock_change_authorized",
        "catalog_change_authorized",
        "runtime_mutation_authorized",
        "database_mutation_authorized",
        "auto_live_promotion",
    ]

    failures = []

    for key in keys:
        if key not in data:
            continue

        if as_int(
            data.get(key)
        ) != 0:
            failures.append(
                key
            )

    if failures:
        raise CloseoutError(
            f"{label} unexpectedly grants "
            "authority: "
            + ", ".join(
                failures
            )
        )


def main() -> int:
    required = [
        BASELINE_JSON,
        SELLER_RUNTIME,
        BUYER_RUNTIME,
    ]

    for path in required:
        if not path.exists():
            raise CloseoutError(
                f"Missing required input: {path}"
            )

    baseline = json.loads(
        BASELINE_JSON.read_text(
            encoding="utf-8",
        )
    )

    if baseline.get("status") != "PASS":
        raise CloseoutError(
            "3D.1 baseline is not PASS."
        )

    if not baseline.get(
        "baseline_locked",
        False,
    ):
        raise CloseoutError(
            "3D baseline is not locked."
        )

    require_zero_authority(
        "3D.1 baseline",
        baseline,
    )

    supply_path, supply = (
        latest_passing_summary(
            SUPPLY_ROOT,
            "3D.2",
        )
    )

    demand_path, demand = (
        latest_passing_summary(
            DEMAND_ROOT,
            "3D.3",
        )
    )

    analysis_path, analysis = (
        latest_passing_summary(
            ANALYSIS_ROOT,
            "3D.4",
        )
    )

    rec_path, recommendation = (
        latest_passing_summary(
            RECOMMENDATION_ROOT,
            "3D.5",
        )
    )

    require_zero_authority(
        "3D.2 supply observer",
        supply,
    )

    require_zero_authority(
        "3D.3 demand observer",
        demand,
    )

    require_zero_authority(
        "3D.4 analyzer",
        analysis,
    )

    require_zero_authority(
        "3D.5 recommendation matrix",
        recommendation,
    )

    failures = []

    seller_sha = sha256(
        SELLER_RUNTIME
    )

    buyer_sha = sha256(
        BUYER_RUNTIME
    )

    expected_seller_sha = str(
        baseline.get(
            "seller_runtime_sha256",
            "",
        )
    )

    expected_buyer_sha = str(
        baseline.get(
            "buyer_runtime_sha256",
            "",
        )
    )

    if (
        not expected_seller_sha
        or seller_sha
        != expected_seller_sha
    ):
        failures.append(
            "Seller runtime changed "
            "since 3D baseline."
        )

    if (
        not expected_buyer_sha
        or buyer_sha
        != expected_buyer_sha
    ):
        failures.append(
            "Buyer runtime changed "
            "since 3D baseline."
        )

    if as_int(
        supply.get(
            "integrity_failures"
        )
    ) != 0:
        failures.append(
            "3D.2 has integrity failures."
        )

    if as_int(
        demand.get(
            "integrity_failures"
        )
    ) != 0:
        failures.append(
            "3D.3 has integrity failures."
        )

    if as_int(
        analysis.get(
            "integrity_failures"
        )
    ) != 0:
        failures.append(
            "3D.4 has integrity failures."
        )

    if as_int(
        recommendation.get(
            "pilot_items"
        )
    ) != 14:
        failures.append(
            "3D.5 does not contain "
            "14 pilot items."
        )

    rec_counts = (
        recommendation.get(
            "recommendation_counts",
            {},
        )
    )

    recommendation_total = sum(
        as_int(value)
        for value
        in rec_counts.values()
    )

    if recommendation_total != 14:
        failures.append(
            "3D.5 recommendation counts "
            "do not sum to 14."
        )

    observation_age_hours = (
        as_float(
            recommendation.get(
                "observation_age_hours"
            )
        )
    )

    minimum_window_hours = (
        as_float(
            recommendation.get(
                "minimum_decision_window_hours"
            )
        )
    )

    analysis_observations = (
        as_int(
            recommendation.get(
                "analysis_observation_count"
            )
        )
    )

    analysis_span_hours = (
        as_float(
            recommendation.get(
                "analysis_span_hours"
            )
        )
    )

    longitudinal_ready = (
        as_int(
            recommendation.get(
                "longitudinal_ready"
            )
        )
    )

    actionable_items = (
        as_int(
            recommendation.get(
                "items_with_actionable_evidence"
            )
        )
    )

    items_with_events = (
        as_int(
            recommendation.get(
                "items_with_player_events"
            )
        )
    )

    window_mature = int(
        observation_age_hours
        >= minimum_window_hours
    )

    market_evidence_mature = int(
        window_mature
        and longitudinal_ready
    )

    actionable_evidence_available = int(
        actionable_items > 0
    )

    if market_evidence_mature:
        if actionable_evidence_available:
            evidence_state = (
                "MATURE_ACTIONABLE_EVIDENCE"
            )
        else:
            evidence_state = (
                "MATURE_WINDOW_NO_ACTIONABLE_EVIDENCE"
            )
    else:
        evidence_state = (
            "EARLY_OBSERVATION"
        )

    engineering_complete = int(
        not failures
    )

    observation_continues = 1

    proceed_to_3e = int(
        engineering_complete
    )

    if engineering_complete:
        phase_state = (
            "ENGINEERING_COMPLETE_"
            "OBSERVATION_CONTINUES"
        )
    else:
        phase_state = (
            "CLOSEOUT_BLOCKED"
        )

    CLOSEOUT_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    output = (
        CLOSEOUT_ROOT
        / "phase-3d-closeout-summary.json"
    )

    summary = {
        "status":
            "PASS"
            if engineering_complete
            else "FAIL",

        "phase":
            "3D.6",

        "phase_state":
            phase_state,

        "engineering_complete":
            engineering_complete,

        "market_evidence_mature":
            market_evidence_mature,

        "actionable_evidence_available":
            actionable_evidence_available,

        "evidence_state":
            evidence_state,

        "observation_continues":
            observation_continues,

        "safe_to_proceed_to_phase_3e":
            proceed_to_3e,

        "baseline_utc":
            baseline.get(
                "baseline_utc"
            ),

        "observation_age_hours":
            observation_age_hours,

        "minimum_decision_window_hours":
            minimum_window_hours,

        "analysis_observation_count":
            analysis_observations,

        "analysis_span_hours":
            analysis_span_hours,

        "longitudinal_ready":
            longitudinal_ready,

        "items_with_player_events":
            items_with_events,

        "items_with_actionable_evidence":
            actionable_items,

        "recommendation_counts":
            rec_counts,

        "seller_runtime_sha256":
            seller_sha,

        "buyer_runtime_sha256":
            buyer_sha,

        "source_summaries": {
            "3D.2":
                str(
                    supply_path
                ),

            "3D.3":
                str(
                    demand_path
                ),

            "3D.4":
                str(
                    analysis_path
                ),

            "3D.5":
                str(
                    rec_path
                ),
        },

        "database_mutation_performed":
            0,

        "runtime_mutation_performed":
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

        "failures":
            failures,
    }

    output.write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print("=" * 108)
    print(
        " Phase 3D.6 Player Market "
        "Response Closeout"
    )
    print("=" * 108)
    print()

    print(
        "Baseline UTC:                    ",
        baseline.get(
            "baseline_utc"
        ),
    )

    print(
        "Observation age hours:           ",
        observation_age_hours,
    )

    print(
        "Minimum decision window:         ",
        minimum_window_hours,
    )

    print()
    print(
        "Analysis observations:           ",
        analysis_observations,
    )

    print(
        "Analysis span hours:              ",
        analysis_span_hours,
    )

    print(
        "Longitudinal ready:               ",
        longitudinal_ready,
    )

    print()
    print(
        "Items with player events:         ",
        items_with_events,
    )

    print(
        "Items with actionable evidence:   ",
        actionable_items,
    )

    print()
    print(
        "Engineering complete:             ",
        engineering_complete,
    )

    print(
        "Market evidence mature:           ",
        market_evidence_mature,
    )

    print(
        "Actionable evidence available:    ",
        actionable_evidence_available,
    )

    print(
        "Observation continues:            ",
        observation_continues,
    )

    print(
        "Safe to proceed to Phase 3E:      ",
        proceed_to_3e,
    )

    print()
    print(
        "Evidence state:",
        evidence_state,
    )

    print(
        "Phase state:",
        phase_state,
    )

    print()
    print(
        "Price change authorized:          0"
    )

    print(
        "Rate change authorized:           0"
    )

    print(
        "Stock change authorized:          0"
    )

    print(
        "Catalog change authorized:        0"
    )

    print(
        "Runtime mutation performed:       0"
    )

    print(
        "Database mutation performed:      0"
    )

    print(
        "Auto live promotion:              0"
    )

    print()
    print(
        "Closeout:",
        output,
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
