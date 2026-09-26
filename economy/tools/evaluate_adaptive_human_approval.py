from __future__ import annotations

import argparse
import hashlib
import json
import secrets
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

CONTRACT_FILE = (
    POLICY_ROOT
    / "adaptive-control-contract.json"
)


class ApprovalGateError(RuntimeError):
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


def load_json(path: Path) -> dict:
    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


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
        data = load_json(path)

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
        raise ApprovalGateError(
            f"No passing {phase} summary found "
            f"in {directory}."
        )

    return found[-1]


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
        raise ApprovalGateError(
            f"{label} grants unexpected "
            "authority: "
            + ", ".join(bad)
        )


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--approve",
        action="store_true",
        help=(
            "Explicitly approve the currently "
            "validated proposal set. This creates "
            "an approval record only and does not "
            "modify runtime files or the database."
        ),
    )

    parser.add_argument(
        "--proposal-sha256",
        default="",
        help=(
            "Required with --approve. Must match "
            "the proposal CSV SHA256 validated by "
            "the current simulation."
        ),
    )

    parser.add_argument(
        "--simulation-sha256",
        default="",
        help=(
            "Required with --approve. Must match "
            "the current 3F.5 summary SHA256."
        ),
    )

    parser.add_argument(
        "--note",
        default="",
        help=(
            "Required with --approve. Human note "
            "describing why this proposal set is "
            "being approved."
        ),
    )

    args = parser.parse_args()

    if not CONTRACT_FILE.exists():
        raise ApprovalGateError(
            "Missing 3F.1 control contract."
        )

    contract = load_json(
        CONTRACT_FILE
    )

    if contract.get(
        "status"
    ) != "PASS":
        raise ApprovalGateError(
            "3F.1 contract is not PASS."
        )

    if contract.get(
        "control_mode"
    ) != "PROPOSAL_ONLY":
        raise ApprovalGateError(
            "Contract is not PROPOSAL_ONLY."
        )

    if as_int(
        contract.get(
            "human_approval_required"
        )
    ) != 1:
        raise ApprovalGateError(
            "Contract unexpectedly does not "
            "require human approval."
        )

    require_zero_authority(
        "3F.1 contract",
        contract,
    )

    proposal_summary_path, proposal = (
        latest_passing_summary(
            PROPOSAL_ROOT,
            "3F.4",
        )
    )

    simulation_summary_path, simulation = (
        latest_passing_summary(
            SIMULATION_ROOT,
            "3F.5",
        )
    )

    require_zero_authority(
        "3F.4 proposals",
        proposal,
    )

    require_zero_authority(
        "3F.5 simulation",
        simulation,
    )

    contract_sha = sha256(
        CONTRACT_FILE
    )

    if (
        proposal.get(
            "contract_sha256"
        )
        != contract_sha
    ):
        raise ApprovalGateError(
            "3F.4 proposals were built against "
            "a different control contract."
        )

    if (
        simulation.get(
            "contract_sha256"
        )
        != contract_sha
    ):
        raise ApprovalGateError(
            "3F.5 simulation was built against "
            "a different control contract."
        )

    proposal_file_text = (
        proposal.get(
            "files",
            {},
        )
        .get(
            "proposals"
        )
    )

    if not proposal_file_text:
        raise ApprovalGateError(
            "3F.4 proposal CSV is missing."
        )

    proposal_file = Path(
        proposal_file_text
    )

    if not proposal_file.exists():
        raise ApprovalGateError(
            f"Missing proposal CSV: "
            f"{proposal_file}"
        )

    proposal_sha = sha256(
        proposal_file
    )

    if (
        simulation.get(
            "proposal_sha256"
        )
        != proposal_sha
    ):
        raise ApprovalGateError(
            "3F.5 simulation does not match "
            "the current proposal CSV."
        )

    simulation_sha = sha256(
        simulation_summary_path
    )

    simulation_state = str(
        simulation.get(
            "simulation_set_state",
            ""
        )
    )

    ready_count = as_int(
        simulation.get(
            "simulation_ready_proposals"
        )
    )

    simulated_changes = as_int(
        simulation.get(
            "simulated_changes"
        )
    )

    stale_proposals = as_int(
        simulation.get(
            "stale_proposals"
        )
    )

    unresolved_external = as_int(
        simulation.get(
            "unresolved_external_prerequisites"
        )
    )

    safe_for_human_review = as_int(
        simulation.get(
            "safe_for_human_review"
        )
    )

    review_conditions = (
        simulation.get(
            "review_conditions",
            []
        )
        or []
    )

    block_conditions = (
        simulation.get(
            "block_conditions",
            []
        )
        or []
    )

    failures = []

    # --------------------------------------------------
    # Determine whether the current proposal set is
    # even eligible to receive human approval.
    # --------------------------------------------------

    if simulation_state == (
        "EMPTY_PROPOSAL_SET_VALID"
    ):
        gate_state = (
            "NOT_APPROVABLE_EMPTY_PROPOSAL_SET"
        )

        approval_possible = 0

    elif simulation_state == (
        "SIMULATION_BLOCKED"
    ):
        gate_state = (
            "NOT_APPROVABLE_SIMULATION_BLOCKED"
        )

        approval_possible = 0

    elif simulation_state == (
        "SIMULATION_DEFERRED_"
        "EXTERNAL_PREREQUISITE"
    ):
        gate_state = (
            "NOT_APPROVABLE_EXTERNAL_"
            "PREREQUISITE"
        )

        approval_possible = 0

    elif simulation_state == (
        "SIMULATION_REVIEW_REQUIRED"
    ):
        gate_state = (
            "NOT_APPROVABLE_REVIEW_REQUIRED"
        )

        approval_possible = 0

    elif (
        simulation_state
        == "SAFE_FOR_HUMAN_REVIEW"
    ):
        gate_state = (
            "AWAITING_EXPLICIT_HUMAN_APPROVAL"
        )

        approval_possible = 1

    else:
        gate_state = (
            "NOT_APPROVABLE_UNKNOWN_"
            "SIMULATION_STATE"
        )

        approval_possible = 0

        failures.append(
            "Unknown 3F.5 simulation state."
        )

    # --------------------------------------------------
    # Additional fail-closed validation.
    # --------------------------------------------------

    if approval_possible:
        if ready_count <= 0:
            approval_possible = 0

            gate_state = (
                "NOT_APPROVABLE_NO_READY_PROPOSALS"
            )

        elif simulated_changes != ready_count:
            approval_possible = 0

            gate_state = (
                "NOT_APPROVABLE_INCOMPLETE_"
                "SIMULATION"
            )

        elif stale_proposals:
            approval_possible = 0

            gate_state = (
                "NOT_APPROVABLE_STALE_PROPOSALS"
            )

        elif unresolved_external:
            approval_possible = 0

            gate_state = (
                "NOT_APPROVABLE_EXTERNAL_"
                "PREREQUISITE"
            )

        elif review_conditions:
            approval_possible = 0

            gate_state = (
                "NOT_APPROVABLE_REVIEW_REQUIRED"
            )

        elif block_conditions:
            approval_possible = 0

            gate_state = (
                "NOT_APPROVABLE_BLOCKED"
            )

        elif safe_for_human_review != 1:
            approval_possible = 0

            gate_state = (
                "NOT_APPROVABLE_SIMULATION_"
                "NOT_REVIEW_READY"
            )

    seller_before = str(
        simulation.get(
            "seller_runtime_sha256_before",
            ""
        )
    )

    seller_after = str(
        simulation.get(
            "seller_runtime_sha256_after",
            ""
        )
    )

    buyer_before = str(
        simulation.get(
            "buyer_runtime_sha256_before",
            ""
        )
    )

    buyer_after = str(
        simulation.get(
            "buyer_runtime_sha256_after",
            ""
        )
    )

    if (
        not seller_before
        or seller_before != seller_after
    ):
        approval_possible = 0

        gate_state = (
            "NOT_APPROVABLE_SELLER_RUNTIME_"
            "MUTATED_DURING_SIMULATION"
        )

    if (
        not buyer_before
        or buyer_before != buyer_after
    ):
        approval_possible = 0

        gate_state = (
            "NOT_APPROVABLE_BUYER_RUNTIME_"
            "MUTATED_DURING_SIMULATION"
        )

    # --------------------------------------------------
    # Explicit human approval.
    #
    # This creates an approval record ONLY.
    # It grants no runtime or database mutation.
    # --------------------------------------------------

    approval_issued = 0
    approval_record = None
    approval_id = None

    if args.approve:
        if not approval_possible:
            raise ApprovalGateError(
                "Current proposal set is not "
                f"approvable. Gate state: "
                f"{gate_state}"
            )

        if not args.proposal_sha256:
            raise ApprovalGateError(
                "--proposal-sha256 is required "
                "with --approve."
            )

        if (
            args.proposal_sha256
            != proposal_sha
        ):
            raise ApprovalGateError(
                "Supplied proposal SHA256 does "
                "not match the validated proposal "
                "set."
            )

        if not args.simulation_sha256:
            raise ApprovalGateError(
                "--simulation-sha256 is required "
                "with --approve."
            )

        if (
            args.simulation_sha256
            != simulation_sha
        ):
            raise ApprovalGateError(
                "Supplied simulation SHA256 does "
                "not match the validated 3F.5 "
                "summary."
            )

        note = args.note.strip()

        if not note:
            raise ApprovalGateError(
                "--note is required with --approve."
            )

        now = datetime.now(
            timezone.utc
        )

        approval_id = (
            "adaptive-"
            + now.strftime(
                "%Y%m%dT%H%M%SZ"
            )
            + "-"
            + secrets.token_hex(4)
        )

        approval_record = {
            "status":
                "APPROVED_FOR_CONTROLLED_"
                "ACTIVATION_REVIEW",

            "phase":
                "3F.6",

            "approval_id":
                approval_id,

            "approved_utc":
                now.isoformat(),

            "approval_note":
                note,

            "contract_sha256":
                contract_sha,

            "proposal_sha256":
                proposal_sha,

            "simulation_summary_sha256":
                simulation_sha,

            "proposal_summary":
                str(
                    proposal_summary_path
                ),

            "proposal_file":
                str(
                    proposal_file
                ),

            "simulation_summary":
                str(
                    simulation_summary_path
                ),

            "seller_runtime_sha256_at_"
            "simulation":
                seller_before,

            "buyer_runtime_sha256_at_"
            "simulation":
                buyer_before,

            "approved_proposal_count":
                ready_count,

            "human_approval_recorded":
                1,

            "runtime_publish_authorized":
                0,

            "database_mutation_authorized":
                0,

            "service_restart_authorized":
                0,

            "automatic_application_allowed":
                0,

            "activation_still_requires_3f7":
                1,
        }

        APPROVAL_ROOT.mkdir(
            parents=True,
            exist_ok=True,
        )

        approval_path = (
            APPROVAL_ROOT
            / (
                now.strftime(
                    "%Y%m%dT%H%M%SZ"
                )
                + "-approval.json"
            )
        )

        approval_path.write_text(
            json.dumps(
                approval_record,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        approval_issued = 1

        gate_state = (
            "HUMAN_APPROVAL_RECORDED_"
            "AWAITING_3F7"
        )

    # --------------------------------------------------
    # Write normal gate evaluation.
    # --------------------------------------------------

    now = datetime.now(
        timezone.utc
    )

    stamp = now.strftime(
        "%Y%m%dT%H%M%SZ"
    )

    APPROVAL_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    gate_file = (
        APPROVAL_ROOT
        / f"{stamp}-gate-summary.json"
    )

    summary = {
        "status":
            "PASS"
            if not failures
            else "FAIL",

        "phase":
            "3F.6",

        "observation_utc":
            now.isoformat(),

        "gate_state":
            gate_state,

        "approval_possible":
            approval_possible,

        "approval_requested":
            int(
                args.approve
            ),

        "approval_issued":
            approval_issued,

        "approval_id":
            approval_id,

        "simulation_state":
            simulation_state,

        "simulation_ready_proposals":
            ready_count,

        "simulated_changes":
            simulated_changes,

        "stale_proposals":
            stale_proposals,

        "unresolved_external_prerequisites":
            unresolved_external,

        "review_conditions":
            review_conditions,

        "block_conditions":
            block_conditions,

        "safe_for_human_review":
            safe_for_human_review,

        "contract_sha256":
            contract_sha,

        "proposal_sha256":
            proposal_sha,

        "simulation_summary_sha256":
            simulation_sha,

        "seller_runtime_sha256_at_simulation":
            seller_before,

        "buyer_runtime_sha256_at_simulation":
            buyer_before,

        "human_approval_required":
            1,

        "runtime_publish_authorized":
            0,

        "service_restart_authorized":
            0,

        "automatic_application_allowed":
            0,

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
            "contract":
                str(
                    CONTRACT_FILE
                ),

            "proposal_summary":
                str(
                    proposal_summary_path
                ),

            "proposal_file":
                str(
                    proposal_file
                ),

            "simulation_summary":
                str(
                    simulation_summary_path
                ),
        },

        "failures":
            failures,
    }

    gate_file.write_text(
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
        " Phase 3F.6 Human Approval Gate"
    )
    print("=" * 112)
    print()

    print(
        "Simulation state:                 ",
        simulation_state,
    )

    print(
        "Simulation-ready proposals:       ",
        ready_count,
    )

    print(
        "Simulated changes:                ",
        simulated_changes,
    )

    print(
        "Stale proposals:                  ",
        stale_proposals,
    )

    print(
        "Unresolved prerequisites:         ",
        unresolved_external,
    )

    print(
        "Review conditions:                ",
        len(
            review_conditions
        ),
    )

    print(
        "Block conditions:                 ",
        len(
            block_conditions
        ),
    )

    print()
    print(
        "Safe for human review:            ",
        safe_for_human_review,
    )

    print(
        "Approval possible:                ",
        approval_possible,
    )

    print(
        "Approval requested:               ",
        int(
            args.approve
        ),
    )

    print(
        "Approval issued:                  ",
        approval_issued,
    )

    print()
    print(
        "Gate state:",
        gate_state,
    )

    print()
    print(
        "Proposal SHA256:",
        proposal_sha,
    )

    print(
        "Simulation SHA256:",
        simulation_sha,
    )

    print()
    print(
        "Runtime publish authorized:       0"
    )

    print(
        "Service restart authorized:       0"
    )

    print(
        "Database mutation authorized:     0"
    )

    print(
        "Automatic application allowed:    0"
    )

    print()
    print(
        "Gate summary:",
        gate_file,
    )

    if approval_record is not None:
        print()
        print(
            "Approval ID:",
            approval_id,
        )

    print()
    print(summary["status"])

    if failures:
        raise SystemExit(1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
