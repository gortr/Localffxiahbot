from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path.home() / "ffxiahbot"

POLICY_ROOT = (
    ROOT
    / "economy"
    / "policy"
)

OBS_ROOT = (
    ROOT
    / "economy"
    / "runtime-observations"
)

ADAPTIVE_ROOT = (
    OBS_ROOT
    / "adaptive"
)

CONTRACT_FILE = (
    POLICY_ROOT
    / "adaptive-control-contract.json"
)

PHASE_3D_CLOSEOUT = (
    OBS_ROOT
    / "player-market-response"
    / "closeout"
    / "phase-3d-closeout-summary.json"
)

PHASE_3E_CLOSEOUT = (
    OBS_ROOT
    / "gil-flow"
    / "closeout"
    / "phase-3e-closeout-summary.json"
)

EVIDENCE_ROOT = (
    ADAPTIVE_ROOT
    / "evidence"
)

RECOMMENDATION_ROOT = (
    ADAPTIVE_ROOT
    / "recommendations"
)

PROPOSAL_ROOT = (
    ADAPTIVE_ROOT
    / "proposals"
)

SIMULATION_ROOT = (
    ADAPTIVE_ROOT
    / "simulation"
)

APPROVAL_ROOT = (
    ADAPTIVE_ROOT
    / "approvals"
)

ACTIVATION_ROOT = (
    ADAPTIVE_ROOT
    / "activation"
)

MONITOR_ROOT = (
    ADAPTIVE_ROOT
    / "post-activation"
)

CLOSEOUT_ROOT = (
    ADAPTIVE_ROOT
    / "closeout"
)


class CloseoutError(RuntimeError):
    pass


def as_int(value: Any) -> int:
    if value is None:
        return 0

    try:
        return int(float(value))
    except (
        TypeError,
        ValueError,
    ):
        return 0


def load_json(path: Path) -> dict:
    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


def latest(
    directory: Path,
    pattern: str,
    phase: str,
) -> tuple[Path, dict]:
    matches = []

    for path in sorted(
        directory.glob(pattern)
    ):
        data = load_json(path)

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
            f"No passing {phase} result found "
            f"in {directory}."
        )

    return matches[-1]


def require_zero_authority(
    label: str,
    data: dict,
) -> None:
    keys = [
        "price_change_authorized",
        "rate_change_authorized",
        "stock_change_authorized",
        "catalog_change_authorized",
        "runtime_mutation_authorized",
        "database_mutation_authorized",
        "adaptive_mutation_authorized",
        "auto_live_promotion",
        "runtime_publish_authorized",
        "service_restart_authorized",
    ]

    bad = []

    for key in keys:
        if (
            key in data
            and as_int(
                data.get(key)
            ) != 0
        ):
            bad.append(key)

    if bad:
        raise CloseoutError(
            f"{label} grants unexpected "
            "authority: "
            + ", ".join(bad)
        )


def main() -> int:
    for path in [
        CONTRACT_FILE,
        PHASE_3D_CLOSEOUT,
        PHASE_3E_CLOSEOUT,
    ]:
        if not path.exists():
            raise CloseoutError(
                f"Missing required input: {path}"
            )

    contract = load_json(
        CONTRACT_FILE
    )

    phase_3d = load_json(
        PHASE_3D_CLOSEOUT
    )

    phase_3e = load_json(
        PHASE_3E_CLOSEOUT
    )

    evidence_path, evidence = latest(
        EVIDENCE_ROOT,
        "*-summary.json",
        "3F.2",
    )

    recommendation_path, recommendations = latest(
        RECOMMENDATION_ROOT,
        "*-summary.json",
        "3F.3",
    )

    proposal_path, proposals = latest(
        PROPOSAL_ROOT,
        "*-summary.json",
        "3F.4",
    )

    simulation_path, simulation = latest(
        SIMULATION_ROOT,
        "*-summary.json",
        "3F.5",
    )

    approval_path, approval = latest(
        APPROVAL_ROOT,
        "*-gate-summary.json",
        "3F.6",
    )

    activation_path, activation = latest(
        ACTIVATION_ROOT,
        "*-activation-gate.json",
        "3F.7",
    )

    monitor_path, monitor = latest(
        MONITOR_ROOT,
        "*-monitor-summary.json",
        "3F.8",
    )

    failures = []

    # --------------------------------------------------
    # Phase prerequisites
    # --------------------------------------------------

    if contract.get("status") != "PASS":
        failures.append(
            "3F.1 contract is not PASS."
        )

    if phase_3d.get("status") != "PASS":
        failures.append(
            "3D closeout is not PASS."
        )

    if phase_3e.get("status") != "PASS":
        failures.append(
            "3E closeout is not PASS."
        )

    if as_int(
        phase_3d.get(
            "engineering_complete"
        )
    ) != 1:
        failures.append(
            "3D engineering incomplete."
        )

    if as_int(
        phase_3e.get(
            "engineering_complete"
        )
    ) != 1:
        failures.append(
            "3E engineering incomplete."
        )

    # --------------------------------------------------
    # Zero-authority chain
    # --------------------------------------------------

    for label, data in [
        ("3F.1 contract", contract),
        ("3F.2 evidence", evidence),
        ("3F.3 recommendations", recommendations),
        ("3F.4 proposals", proposals),
        ("3F.5 simulation", simulation),
        ("3F.6 approval gate", approval),
        ("3F.7 activation gate", activation),
        ("3F.8 monitor", monitor),
    ]:
        try:
            require_zero_authority(
                label,
                data,
            )
        except CloseoutError as exc:
            failures.append(
                str(exc)
            )

    # --------------------------------------------------
    # Contract state
    # --------------------------------------------------

    contract_locked = int(
        contract.get(
            "control_mode"
        )
        == "PROPOSAL_ONLY"
        and contract.get(
            "current_activation_state"
        )
        == "DESIGN_ONLY_LOCKED"
        and as_int(
            contract.get(
                "live_runtime_publish_allowed"
            )
        )
        == 0
        and as_int(
            contract.get(
                "live_database_mutation_allowed"
            )
        )
        == 0
        and as_int(
            contract.get(
                "automatic_change_application_allowed"
            )
        )
        == 0
    )

    if not contract_locked:
        failures.append(
            "Adaptive control contract is not "
            "in the expected locked state."
        )

    # --------------------------------------------------
    # 3F pipeline state
    # --------------------------------------------------

    evidence_engine_ready = int(
        as_int(
            evidence.get(
                "analysis_eligible_forms"
            )
        )
        == 28
        and as_int(
            evidence.get(
                "simulation_eligible_forms"
            )
        )
        == 28
        and as_int(
            evidence.get(
                "recommendation_eligible_forms"
            )
        )
        == 0
        and as_int(
            evidence.get(
                "bounded_proposal_eligible_forms"
            )
        )
        == 0
    )

    recommendation_engine_ready = int(
        recommendations.get(
            "generator_state"
        )
        == "ALL_FORMS_INSUFFICIENT_EVIDENCE"
        and as_int(
            recommendations.get(
                "directional_recommendations"
            )
        )
        == 0
        and as_int(
            recommendations.get(
                "change_proposal_candidates"
            )
        )
        == 0
    )

    proposal_engine_ready = int(
        proposals.get(
            "proposal_set_state"
        )
        == "NO_CHANGE_PROPOSALS"
        and as_int(
            proposals.get(
                "simulation_ready_proposals"
            )
        )
        == 0
        and as_int(
            proposals.get(
                "blocked_proposals"
            )
        )
        == 0
    )

    simulation_engine_ready = int(
        simulation.get(
            "simulation_set_state"
        )
        == "EMPTY_PROPOSAL_SET_VALID"
        and as_int(
            simulation.get(
                "simulated_changes"
            )
        )
        == 0
        and as_int(
            simulation.get(
                "stale_proposals"
            )
        )
        == 0
    )

    approval_gate_ready = int(
        approval.get(
            "gate_state"
        )
        == "NOT_APPROVABLE_EMPTY_PROPOSAL_SET"
        and as_int(
            approval.get(
                "approval_possible"
            )
        )
        == 0
        and as_int(
            approval.get(
                "approval_issued"
            )
        )
        == 0
    )

    activation_gate_ready = int(
        activation.get(
            "activation_state"
        )
        == "ACTIVATION_LOCKED_NO_APPROVAL_RECORD"
        and as_int(
            activation.get(
                "controlled_activation_allowed"
            )
        )
        == 0
        and as_int(
            activation.get(
                "runtime_publish_performed"
            )
        )
        == 0
        and as_int(
            activation.get(
                "database_mutation_performed"
            )
        )
        == 0
    )

    monitoring_ready = int(
        monitor.get(
            "monitoring_state"
        )
        == "NO_ACTIVATION_TO_MONITOR"
        and as_int(
            monitor.get(
                "monitoring_harness_ready"
            )
        )
        == 1
        and as_int(
            monitor.get(
                "engineering_ready_for_closeout"
            )
        )
        == 1
        and as_int(
            monitor.get(
                "seller_service_active"
            )
        )
        == 1
        and as_int(
            monitor.get(
                "buyer_service_active"
            )
        )
        == 1
        and as_int(
            monitor.get(
                "seller_runtime_matches_3f7"
            )
        )
        == 1
        and as_int(
            monitor.get(
                "buyer_runtime_matches_3f7"
            )
        )
        == 1
    )

    component_states = {
        "contract_locked":
            contract_locked,

        "evidence_engine_ready":
            evidence_engine_ready,

        "recommendation_engine_ready":
            recommendation_engine_ready,

        "proposal_engine_ready":
            proposal_engine_ready,

        "simulation_engine_ready":
            simulation_engine_ready,

        "approval_gate_ready":
            approval_gate_ready,

        "activation_gate_ready":
            activation_gate_ready,

        "monitoring_ready":
            monitoring_ready,
    }

    for name, value in component_states.items():
        if value != 1:
            failures.append(
                f"{name} is not ready."
            )

    # --------------------------------------------------
    # Evidence maturity remains independent from
    # engineering completion.
    # --------------------------------------------------

    market_evidence_mature = as_int(
        phase_3d.get(
            "market_evidence_mature"
        )
    )

    gil_flow_evidence_mature = as_int(
        phase_3e.get(
            "actual_flow_evidence_mature"
        )
    )

    evidence_maturity_pending = int(
        not (
            market_evidence_mature
            and gil_flow_evidence_mature
        )
    )

    phase_3d_observation_continues = as_int(
        phase_3d.get(
            "observation_continues"
        )
    )

    phase_3e_observation_continues = as_int(
        phase_3e.get(
            "observation_continues"
        )
    )

    passive_observation_continues = int(
        phase_3d_observation_continues
        and phase_3e_observation_continues
    )

    live_adaptation_exercised = int(
        as_int(
            monitor.get(
                "post_activation_exercised"
            )
        )
        == 1
    )

    live_adaptation_authorized = int(
        as_int(
            activation.get(
                "controlled_activation_allowed"
            )
        )
        == 1
    )

    adaptive_framework_operational = int(
        all(
            value == 1
            for value in component_states.values()
        )
    )

    phase_3f_engineering_complete = int(
        adaptive_framework_operational
        and not failures
    )

    phase_3_engineering_complete = int(
        phase_3f_engineering_complete
        and as_int(
            phase_3d.get(
                "engineering_complete"
            )
        )
        == 1
        and as_int(
            phase_3e.get(
                "engineering_complete"
            )
        )
        == 1
    )

    # The future path exists architecturally, but
    # this is NOT permission to activate today.
    future_controlled_activation_path_ready = int(
        phase_3f_engineering_complete
        and contract_locked
        and monitoring_ready
    )

    if not phase_3f_engineering_complete:
        phase_state = (
            "ENGINEERING_CLOSEOUT_BLOCKED"
        )

    elif evidence_maturity_pending:
        phase_state = (
            "ENGINEERING_COMPLETE_"
            "ADAPTATION_LOCKED_"
            "OBSERVATION_CONTINUES"
        )

    elif not live_adaptation_authorized:
        phase_state = (
            "ENGINEERING_COMPLETE_"
            "EVIDENCE_MATURE_"
            "ACTIVATION_LOCKED"
        )

    else:
        phase_state = (
            "ENGINEERING_COMPLETE_"
            "CONTROLLED_ACTIVATION_AVAILABLE"
        )

    now = datetime.now(
        timezone.utc
    )

    CLOSEOUT_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    output = (
        CLOSEOUT_ROOT
        / "phase-3f-closeout-summary.json"
    )

    summary = {
        "status":
            (
                "PASS"
                if phase_3f_engineering_complete
                else "FAIL"
            ),

        "phase":
            "3F.9",

        "observation_utc":
            now.isoformat(),

        "phase_state":
            phase_state,

        "phase_3f_engineering_complete":
            phase_3f_engineering_complete,

        "phase_3_engineering_complete":
            phase_3_engineering_complete,

        "adaptive_framework_operational":
            adaptive_framework_operational,

        "live_adaptation_exercised":
            live_adaptation_exercised,

        "live_adaptation_authorized":
            live_adaptation_authorized,

        "evidence_maturity_pending":
            evidence_maturity_pending,

        "market_evidence_mature":
            market_evidence_mature,

        "gil_flow_evidence_mature":
            gil_flow_evidence_mature,

        "passive_observation_continues":
            passive_observation_continues,

        "future_controlled_activation_path_ready":
            future_controlled_activation_path_ready,

        "contract_locked":
            contract_locked,

        "component_states":
            component_states,

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

        "sources": {
            "3D_closeout":
                str(
                    PHASE_3D_CLOSEOUT
                ),

            "3E_closeout":
                str(
                    PHASE_3E_CLOSEOUT
                ),

            "3F1_contract":
                str(
                    CONTRACT_FILE
                ),

            "3F2_evidence":
                str(
                    evidence_path
                ),

            "3F3_recommendations":
                str(
                    recommendation_path
                ),

            "3F4_proposals":
                str(
                    proposal_path
                ),

            "3F5_simulation":
                str(
                    simulation_path
                ),

            "3F6_approval_gate":
                str(
                    approval_path
                ),

            "3F7_activation_gate":
                str(
                    activation_path
                ),

            "3F8_monitor":
                str(
                    monitor_path
                ),
        },

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

    print("=" * 112)
    print(
        " Phase 3F.9 Adaptive Economy "
        "Engineering Closeout"
    )
    print("=" * 112)
    print()

    print(
        "Contract locked:                         ",
        contract_locked,
    )

    print(
        "Evidence engine ready:                   ",
        evidence_engine_ready,
    )

    print(
        "Recommendation engine ready:             ",
        recommendation_engine_ready,
    )

    print(
        "Proposal engine ready:                   ",
        proposal_engine_ready,
    )

    print(
        "Simulation engine ready:                 ",
        simulation_engine_ready,
    )

    print(
        "Human approval gate ready:               ",
        approval_gate_ready,
    )

    print(
        "Controlled activation gate ready:        ",
        activation_gate_ready,
    )

    print(
        "Post-activation monitor ready:           ",
        monitoring_ready,
    )

    print()
    print(
        "Market evidence mature:                  ",
        market_evidence_mature,
    )

    print(
        "Gil-flow evidence mature:                ",
        gil_flow_evidence_mature,
    )

    print(
        "Evidence maturity pending:               ",
        evidence_maturity_pending,
    )

    print(
        "Passive observation continues:           ",
        passive_observation_continues,
    )

    print()
    print(
        "Adaptive framework operational:          ",
        adaptive_framework_operational,
    )

    print(
        "Live adaptation exercised:               ",
        live_adaptation_exercised,
    )

    print(
        "Live adaptation authorized:              ",
        live_adaptation_authorized,
    )

    print(
        "Future controlled activation path ready: ",
        future_controlled_activation_path_ready,
    )

    print()
    print(
        "Phase 3F engineering complete:           ",
        phase_3f_engineering_complete,
    )

    print(
        "Phase 3 engineering complete:            ",
        phase_3_engineering_complete,
    )

    print()
    print(
        "Phase state:",
        phase_state,
    )

    print()
    print(
        "Price change authorized:                 0"
    )

    print(
        "Rate change authorized:                  0"
    )

    print(
        "Stock change authorized:                 0"
    )

    print(
        "Catalog change authorized:               0"
    )

    print(
        "Runtime mutation authorized:             0"
    )

    print(
        "Database mutation authorized:            0"
    )

    print(
        "Auto live promotion:                     0"
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
