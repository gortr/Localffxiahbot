from __future__ import annotations

import hashlib
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

GENERATED = (
    ROOT
    / "economy"
    / "generated"
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

APPROVAL_ROOT = (
    ADAPTIVE_ROOT
    / "approvals"
)

ACTIVATION_ROOT = (
    ADAPTIVE_ROOT
    / "activation"
)

GIL_FLOW_ROOT = (
    OBS_ROOT
    / "gil-flow"
)

CONTRACT_FILE = (
    POLICY_ROOT
    / "adaptive-control-contract.json"
)

SAFETY_FILE = (
    GIL_FLOW_ROOT
    / "safety"
    / "current-safety-summary.json"
)

SELLER_RUNTIME = (
    GENERATED
    / "market-sell.csv"
)

BUYER_RUNTIME = (
    GENERATED
    / "market-buy.csv"
)


class ActivationGateError(RuntimeError):
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


def latest_gate_summary() -> tuple[Path, dict]:
    paths = sorted(
        APPROVAL_ROOT.glob(
            "*-gate-summary.json"
        )
    )

    if not paths:
        raise ActivationGateError(
            "No 3F.6 gate summary found."
        )

    path = paths[-1]
    data = load_json(path)

    if (
        data.get("phase") != "3F.6"
        or data.get("status") != "PASS"
    ):
        raise ActivationGateError(
            "Latest 3F.6 gate summary "
            "is not PASS."
        )

    return path, data


def latest_approval_record():
    paths = sorted(
        APPROVAL_ROOT.glob(
            "*-approval.json"
        )
    )

    if not paths:
        return None, None

    path = paths[-1]

    return path, load_json(path)


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
        raise ActivationGateError(
            f"{label} grants unexpected "
            "authority: "
            + ", ".join(bad)
        )


def main() -> int:
    required = [
        CONTRACT_FILE,
        SAFETY_FILE,
        SELLER_RUNTIME,
        BUYER_RUNTIME,
    ]

    for path in required:
        if not path.exists():
            raise ActivationGateError(
                f"Missing required input: {path}"
            )

    contract = load_json(
        CONTRACT_FILE
    )

    safety = load_json(
        SAFETY_FILE
    )

    if contract.get(
        "status"
    ) != "PASS":
        raise ActivationGateError(
            "3F.1 contract is not PASS."
        )

    if contract.get(
        "control_mode"
    ) != "PROPOSAL_ONLY":
        raise ActivationGateError(
            "Unexpected control mode."
        )

    if safety.get(
        "status"
    ) != "PASS":
        raise ActivationGateError(
            "3E.5 safety state is not PASS."
        )

    require_zero_authority(
        "3F.1 contract",
        contract,
    )

    require_zero_authority(
        "3E.5 safety",
        safety,
    )

    gate_path, gate = (
        latest_gate_summary()
    )

    require_zero_authority(
        "3F.6 gate",
        gate,
    )

    approval_path, approval = (
        latest_approval_record()
    )

    contract_sha = sha256(
        CONTRACT_FILE
    )

    seller_sha = sha256(
        SELLER_RUNTIME
    )

    buyer_sha = sha256(
        BUYER_RUNTIME
    )

    approval_present = int(
        approval is not None
    )

    approval_chain_valid = 0
    proposal_sha_valid = 0
    simulation_sha_valid = 0
    runtime_fresh = 0
    safety_clear = 0

    failures = []

    # --------------------------------------------------
    # Current safety state
    #
    # WATCH is allowed.
    # REVIEW and BLOCK prevent activation.
    # --------------------------------------------------

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

    safety_clear = int(
        review_count == 0
        and block_count == 0
    )

    # --------------------------------------------------
    # Human approval chain
    # --------------------------------------------------

    if approval is not None:
        if (
            approval.get("phase")
            != "3F.6"
        ):
            failures.append(
                "Approval record has wrong phase."
            )

        if (
            approval.get("status")
            !=
            "APPROVED_FOR_CONTROLLED_"
            "ACTIVATION_REVIEW"
        ):
            failures.append(
                "Approval record has invalid "
                "status."
            )

        if as_int(
            approval.get(
                "human_approval_recorded"
            )
        ) != 1:
            failures.append(
                "Human approval was not recorded."
            )

        if (
            approval.get(
                "contract_sha256"
            )
            != contract_sha
        ):
            failures.append(
                "Approval contract hash is stale."
            )

        proposal_path_text = (
            approval.get(
                "proposal_file"
            )
        )

        simulation_path_text = (
            approval.get(
                "simulation_summary"
            )
        )

        if not proposal_path_text:
            failures.append(
                "Approval proposal path missing."
            )

        if not simulation_path_text:
            failures.append(
                "Approval simulation path missing."
            )

        proposal_file = (
            Path(proposal_path_text)
            if proposal_path_text
            else None
        )

        simulation_file = (
            Path(simulation_path_text)
            if simulation_path_text
            else None
        )

        if (
            proposal_file is not None
            and proposal_file.exists()
        ):
            proposal_sha_valid = int(
                sha256(
                    proposal_file
                )
                == approval.get(
                    "proposal_sha256"
                )
            )

            if not proposal_sha_valid:
                failures.append(
                    "Approved proposal hash "
                    "does not match."
                )

        elif proposal_file is not None:
            failures.append(
                "Approved proposal file missing."
            )

        if (
            simulation_file is not None
            and simulation_file.exists()
        ):
            simulation_sha_valid = int(
                sha256(
                    simulation_file
                )
                == approval.get(
                    "simulation_summary_sha256"
                )
            )

            if not simulation_sha_valid:
                failures.append(
                    "Approved simulation hash "
                    "does not match."
                )

        elif simulation_file is not None:
            failures.append(
                "Approved simulation file missing."
            )

        seller_expected = str(
            approval.get(
                "seller_runtime_sha256_at_"
                "simulation",
                "",
            )
        )

        buyer_expected = str(
            approval.get(
                "buyer_runtime_sha256_at_"
                "simulation",
                "",
            )
        )

        runtime_fresh = int(
            bool(seller_expected)
            and bool(buyer_expected)
            and seller_expected == seller_sha
            and buyer_expected == buyer_sha
        )

        if not runtime_fresh:
            failures.append(
                "Live runtime changed since "
                "approved simulation."
            )

        approval_chain_valid = int(
            not failures
            and proposal_sha_valid
            and simulation_sha_valid
            and runtime_fresh
        )

    # --------------------------------------------------
    # Contract activation authority
    #
    # Contract v1 is intentionally locked.
    # This gate MUST NOT override it.
    # --------------------------------------------------

    contract_activation_state = str(
        contract.get(
            "current_activation_state",
            "",
        )
    )

    contract_publish_allowed = as_int(
        contract.get(
            "live_runtime_publish_allowed"
        )
    )

    contract_db_allowed = as_int(
        contract.get(
            "live_database_mutation_allowed"
        )
    )

    contract_auto_apply = as_int(
        contract.get(
            "automatic_change_application_allowed"
        )
    )

    controlled_activation_allowed = int(
        approval_present
        and approval_chain_valid
        and safety_clear
        and contract_activation_state
        == "CONTROLLED_ACTIVATION_ENABLED"
        and contract_publish_allowed == 1
    )

    # --------------------------------------------------
    # State resolution
    # --------------------------------------------------

    if not approval_present:
        activation_state = (
            "ACTIVATION_LOCKED_"
            "NO_APPROVAL_RECORD"
        )

    elif failures:
        activation_state = (
            "ACTIVATION_LOCKED_"
            "APPROVAL_CHAIN_INVALID"
        )

    elif not safety_clear:
        activation_state = (
            "ACTIVATION_LOCKED_"
            "SAFETY_REVIEW_OR_BLOCK"
        )

    elif (
        contract_activation_state
        != "CONTROLLED_ACTIVATION_ENABLED"
        or not contract_publish_allowed
    ):
        activation_state = (
            "ACTIVATION_LOCKED_"
            "BY_CONTROL_CONTRACT"
        )

    elif controlled_activation_allowed:
        activation_state = (
            "CONTROLLED_ACTIVATION_"
            "PRECHECK_READY"
        )

    else:
        activation_state = (
            "ACTIVATION_LOCKED"
        )

    # This tool performs validation only.
    runtime_publish_performed = 0
    database_mutation_performed = 0
    service_restart_performed = 0

    now = datetime.now(
        timezone.utc
    )

    ACTIVATION_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    output = (
        ACTIVATION_ROOT
        / (
            now.strftime(
                "%Y%m%dT%H%M%SZ"
            )
            + "-activation-gate.json"
        )
    )

    summary = {
        "status":
            "PASS",

        "phase":
            "3F.7",

        "observation_utc":
            now.isoformat(),

        "activation_state":
            activation_state,

        "contract_version":
            as_int(
                contract.get(
                    "contract_version"
                )
            ),

        "contract_control_mode":
            contract.get(
                "control_mode"
            ),

        "contract_activation_state":
            contract_activation_state,

        "approval_present":
            approval_present,

        "approval_chain_valid":
            approval_chain_valid,

        "proposal_sha_valid":
            proposal_sha_valid,

        "simulation_sha_valid":
            simulation_sha_valid,

        "runtime_fresh":
            runtime_fresh,

        "current_budget_state":
            safety.get(
                "budget_state"
            ),

        "current_review_count":
            review_count,

        "current_block_count":
            block_count,

        "current_safety_clear":
            safety_clear,

        "contract_runtime_publish_allowed":
            contract_publish_allowed,

        "contract_database_mutation_allowed":
            contract_db_allowed,

        "contract_automatic_application_allowed":
            contract_auto_apply,

        "controlled_activation_allowed":
            controlled_activation_allowed,

        "runtime_publish_performed":
            runtime_publish_performed,

        "database_mutation_performed":
            database_mutation_performed,

        "service_restart_performed":
            service_restart_performed,

        "seller_runtime_sha256":
            seller_sha,

        "buyer_runtime_sha256":
            buyer_sha,

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

            "phase_3e_safety":
                str(
                    SAFETY_FILE
                ),

            "phase_3f6_gate":
                str(
                    gate_path
                ),

            "approval_record":
                (
                    str(
                        approval_path
                    )
                    if approval_path
                    else None
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
        " Phase 3F.7 Controlled "
        "Activation Gate"
    )
    print("=" * 112)
    print()

    print(
        "Contract version:                  ",
        summary[
            "contract_version"
        ],
    )

    print(
        "Contract control mode:             ",
        summary[
            "contract_control_mode"
        ],
    )

    print(
        "Contract activation state:         ",
        contract_activation_state,
    )

    print()
    print(
        "Approval present:                  ",
        approval_present,
    )

    print(
        "Approval chain valid:              ",
        approval_chain_valid,
    )

    print(
        "Runtime fresh:                     ",
        runtime_fresh,
    )

    print()
    print(
        "Current gil-flow budget:           ",
        safety.get(
            "budget_state"
        ),
    )

    print(
        "Current REVIEW gates:              ",
        review_count,
    )

    print(
        "Current BLOCK gates:               ",
        block_count,
    )

    print(
        "Current safety clear:              ",
        safety_clear,
    )

    print()
    print(
        "Contract runtime publish allowed:  ",
        contract_publish_allowed,
    )

    print(
        "Contract DB mutation allowed:      ",
        contract_db_allowed,
    )

    print(
        "Automatic application allowed:     ",
        contract_auto_apply,
    )

    print()
    print(
        "Controlled activation allowed:     ",
        controlled_activation_allowed,
    )

    print()
    print(
        "Activation state:",
        activation_state,
    )

    print()
    print(
        "Runtime publish performed:         0"
    )

    print(
        "Database mutation performed:       0"
    )

    print(
        "Service restart performed:         0"
    )

    print()
    print(
        "Price change authorized:           0"
    )

    print(
        "Rate change authorized:            0"
    )

    print(
        "Stock change authorized:           0"
    )

    print(
        "Catalog change authorized:         0"
    )

    print(
        "Runtime mutation authorized:       0"
    )

    print(
        "Database mutation authorized:      0"
    )

    print(
        "Auto live promotion:               0"
    )

    print()
    print(
        "Gate:",
        output,
    )

    print()
    print(summary["status"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
