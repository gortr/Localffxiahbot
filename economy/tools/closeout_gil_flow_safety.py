from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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

SAFETY_ROOT = (
    OBS_ROOT
    / "safety"
)

CLOSEOUT_ROOT = (
    OBS_ROOT
    / "closeout"
)

BASELINE_JSON = (
    OBS_ROOT
    / "baseline.json"
)

MODEL_SUMMARY = (
    MODEL_ROOT
    / "current-exposure-summary.json"
)

SAFETY_SUMMARY = (
    SAFETY_ROOT
    / "current-safety-summary.json"
)


class CloseoutError(RuntimeError):
    pass


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
        raise CloseoutError(
            f"No passing {phase} summary found."
        )

    return found[-1]


def require_zero_authority(
    label: str,
    data: dict,
) -> None:
    authority_keys = [
        "price_change_authorized",
        "rate_change_authorized",
        "stock_change_authorized",
        "catalog_change_authorized",
        "runtime_mutation_authorized",
        "database_mutation_authorized",
        "auto_live_promotion",
    ]

    bad = []

    for key in authority_keys:
        if (
            key in data
            and as_int(
                data.get(key)
            ) != 0
        ):
            bad.append(
                key
            )

    if bad:
        raise CloseoutError(
            f"{label} grants unexpected "
            "authority: "
            + ", ".join(bad)
        )


def main() -> int:
    required = [
        BASELINE_JSON,
        MODEL_SUMMARY,
        SAFETY_SUMMARY,
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

    model = json.loads(
        MODEL_SUMMARY.read_text(
            encoding="utf-8",
        )
    )

    safety = json.loads(
        SAFETY_SUMMARY.read_text(
            encoding="utf-8",
        )
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

    if baseline.get("status") != "PASS":
        raise CloseoutError(
            "3E.1 baseline is not PASS."
        )

    if not baseline.get(
        "baseline_locked",
        False,
    ):
        raise CloseoutError(
            "3E.1 baseline is not locked."
        )

    if model.get("status") != "PASS":
        raise CloseoutError(
            "3E.4 model is not PASS."
        )

    if (
        model.get("model_state")
        != "EXPOSURE_MODEL_READY"
    ):
        raise CloseoutError(
            "3E.4 model is not ready."
        )

    if safety.get("status") != "PASS":
        raise CloseoutError(
            "3E.5 safety gates are not PASS."
        )

    for label, data in [
        ("3E.1 baseline", baseline),
        ("3E.2 faucet", faucet),
        ("3E.3 sink", sink),
        ("3E.4 model", model),
        ("3E.5 safety", safety),
    ]:
        require_zero_authority(
            label,
            data,
        )

    failures = []

    if as_int(
        faucet.get(
            "integrity_failures"
        )
    ) != 0:
        failures.append(
            "Faucet integrity failures."
        )

    if as_int(
        sink.get(
            "integrity_failures"
        )
    ) != 0:
        failures.append(
            "Sink integrity failures."
        )

    if as_int(
        safety.get(
            "block_count"
        )
    ) != 0:
        failures.append(
            "Safety BLOCK gate active."
        )

    structural_failures = (
        safety.get(
            "structural_failures",
            [],
        )
    )

    if structural_failures:
        failures.append(
            "3E.5 structural failures active."
        )

    budget_state = str(
        safety.get(
            "budget_state",
            "",
        )
    )

    watch_count = as_int(
        safety.get(
            "watch_count"
        )
    )

    review_count = as_int(
        safety.get(
            "review_count"
        )
    )

    block_count = as_int(
        safety.get(
            "block_count"
        )
    )

    actual_evidence_mature = as_int(
        safety.get(
            "actual_flow_evidence_mature"
        )
    )

    actual_transactions = as_int(
        safety.get(
            "actual_postbaseline_transactions"
        )
    )

    global_stress_ratio = as_float(
        safety.get(
            "global_stress_ratio"
        )
    )

    actual_flow_gate = str(
        safety.get(
            "actual_flow_gate",
            "",
        )
    )

    repeatability_state = str(
        safety.get(
            "repeatability_protection_state",
            "",
        )
    )

    expansion_reaudit_required = as_int(
        safety.get(
            "full_recipe_path_reaudit_required_for_expansion"
        )
    )

    actual = model.get(
        "actual_flow",
        {},
    )

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

    post_net = as_int(
        actual.get(
            "postbaseline_net_player_gil_sink"
        )
    )

    engineering_complete = int(
        not failures
    )

    observation_continues = 1

    safe_to_design_3f = int(
        engineering_complete
        and block_count == 0
    )

    # Phase 3F may be designed and validated,
    # but live adaptive mutation remains locked
    # until actual market evidence matures and
    # no REVIEW/BLOCK condition exists.
    adaptive_mutation_authorized = int(
        engineering_complete
        and actual_evidence_mature
        and review_count == 0
        and block_count == 0
        and actual_flow_gate
        in {
            "NORMAL",
            "WATCH",
        }
        and False
    )

    # The explicit "and False" is intentional:
    # Phase 3E never grants live adaptive authority.
    # A future Phase 3F activation gate must make
    # that decision independently.

    if not engineering_complete:
        phase_state = (
            "CLOSEOUT_BLOCKED"
        )

    elif review_count:
        phase_state = (
            "ENGINEERING_COMPLETE_"
            "REVIEW_REQUIRED"
        )

    elif watch_count:
        phase_state = (
            "ENGINEERING_COMPLETE_"
            "WATCH_OBSERVATION_CONTINUES"
        )

    else:
        phase_state = (
            "ENGINEERING_COMPLETE_"
            "OBSERVATION_CONTINUES"
        )

    if actual_evidence_mature:
        evidence_state = (
            "ACTUAL_FLOW_EVIDENCE_MATURE"
        )
    else:
        evidence_state = (
            "ACTUAL_FLOW_EVIDENCE_IMMATURE"
        )

    CLOSEOUT_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    output = (
        CLOSEOUT_ROOT
        / "phase-3e-closeout-summary.json"
    )

    summary = {
        "status":
            "PASS"
            if engineering_complete
            else "FAIL",

        "phase":
            "3E.6",

        "phase_state":
            phase_state,

        "engineering_complete":
            engineering_complete,

        "evidence_state":
            evidence_state,

        "actual_flow_evidence_mature":
            actual_evidence_mature,

        "actual_postbaseline_transactions":
            actual_transactions,

        "observation_continues":
            observation_continues,

        "safe_to_proceed_to_phase_3f_design":
            safe_to_design_3f,

        "adaptive_mutation_authorized":
            adaptive_mutation_authorized,

        "budget_state":
            budget_state,

        "watch_count":
            watch_count,

        "review_count":
            review_count,

        "block_count":
            block_count,

        "global_stress_ratio":
            global_stress_ratio,

        "actual_flow_gate":
            actual_flow_gate,

        "postbaseline_faucet_gil":
            post_faucet,

        "postbaseline_sink_gil":
            post_sink,

        "postbaseline_net_player_gil_sink":
            post_net,

        "repeatability_protection_state":
            repeatability_state,

        "full_recipe_path_reaudit_required_for_expansion":
            expansion_reaudit_required,

        "historical_baseline": {
            "faucet_gil":
                as_int(
                    baseline.get(
                        "gross_faucet_gil"
                    )
                ),

            "sink_gil":
                as_int(
                    baseline.get(
                        "gross_sink_gil"
                    )
                ),

            "net_player_gil_sink":
                as_int(
                    baseline.get(
                        "net_player_gil_sink"
                    )
                ),
        },

        "source_summaries": {
            "3E.2":
                str(
                    faucet_path
                ),

            "3E.3":
                str(
                    sink_path
                ),

            "3E.4":
                str(
                    MODEL_SUMMARY
                ),

            "3E.5":
                str(
                    SAFETY_SUMMARY
                ),
        },

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

    print("=" * 110)
    print(
        " Phase 3E.6 Gil-Flow "
        "Safety Closeout"
    )
    print("=" * 110)
    print()

    print(
        "Budget state:                       ",
        budget_state,
    )

    print(
        "Watch gates:                        ",
        watch_count,
    )

    print(
        "Review gates:                       ",
        review_count,
    )

    print(
        "Block gates:                        ",
        block_count,
    )

    print()
    print(
        "Global stress ratio:                ",
        global_stress_ratio,
    )

    print()
    print(
        "Post-baseline faucet:               ",
        post_faucet,
    )

    print(
        "Post-baseline sink:                 ",
        post_sink,
    )

    print(
        "Post-baseline net player gil sink:  ",
        post_net,
    )

    print()
    print(
        "Actual AHBot transactions:          ",
        actual_transactions,
    )

    print(
        "Actual evidence mature:             ",
        actual_evidence_mature,
    )

    print(
        "Actual flow gate:                   ",
        actual_flow_gate,
    )

    print()
    print(
        "Repeatability protection:           ",
        repeatability_state,
    )

    print(
        "Expansion recipe re-audit required: ",
        expansion_reaudit_required,
    )

    print()
    print(
        "Engineering complete:               ",
        engineering_complete,
    )

    print(
        "Observation continues:              ",
        observation_continues,
    )

    print(
        "Safe to proceed to 3F design:       ",
        safe_to_design_3f,
    )

    print(
        "Adaptive mutation authorized:       ",
        adaptive_mutation_authorized,
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
        "Price change authorized:            0"
    )

    print(
        "Rate change authorized:             0"
    )

    print(
        "Stock change authorized:            0"
    )

    print(
        "Catalog change authorized:          0"
    )

    print(
        "Runtime mutation authorized:        0"
    )

    print(
        "Database mutation authorized:       0"
    )

    print(
        "Auto live promotion:                0"
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
