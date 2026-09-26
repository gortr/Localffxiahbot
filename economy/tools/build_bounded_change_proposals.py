from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


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

RECOMMENDATION_ROOT = (
    ADAPTIVE_ROOT
    / "recommendations"
)

PROPOSAL_ROOT = (
    ADAPTIVE_ROOT
    / "proposals"
)

CONTRACT_FILE = (
    POLICY_ROOT
    / "adaptive-control-contract.json"
)


class ProposalError(RuntimeError):
    pass


ALLOWED_PROPOSAL_STATES = {
    "NO_PROPOSAL",
    "READY_FOR_SIMULATION",
    "DEFER_BOUND_UNPROVEN",
    "DEFER_MANUAL_REVIEW",
    "BLOCK",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


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
        directory.glob("*-summary.json")
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
        raise ProposalError(
            f"No passing {phase} summary found."
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
            )
            != 0
        ):
            bad.append(key)

    if bad:
        raise ProposalError(
            f"{label} grants unexpected authority: "
            + ", ".join(bad)
        )


def rate_step(
    current: float,
    *,
    relative_fraction: float,
    minimum_step: float,
    maximum_step: float,
) -> float:
    calculated = (
        abs(current)
        * relative_fraction
    )

    return min(
        max(
            calculated,
            minimum_step,
        ),
        maximum_step,
    )


def main() -> int:
    if not CONTRACT_FILE.exists():
        raise ProposalError(
            "Missing 3F.1 control contract."
        )

    contract = load_json(
        CONTRACT_FILE
    )

    if contract.get("status") != "PASS":
        raise ProposalError(
            "3F.1 contract is not PASS."
        )

    if contract.get(
        "control_mode"
    ) != "PROPOSAL_ONLY":
        raise ProposalError(
            "Contract is not PROPOSAL_ONLY."
        )

    if as_int(
        contract.get(
            "proposal_generation_allowed"
        )
    ) != 1:
        raise ProposalError(
            "Contract does not allow "
            "proposal generation."
        )

    require_zero_authority(
        "3F.1 contract",
        contract,
    )

    rec_summary_path, rec_summary = (
        latest_passing_summary(
            RECOMMENDATION_ROOT,
            "3F.3",
        )
    )

    require_zero_authority(
        "3F.3 recommendations",
        rec_summary,
    )

    if (
        rec_summary.get(
            "contract_sha256"
        )
        != sha256(
            CONTRACT_FILE
        )
    ):
        raise ProposalError(
            "3F.3 recommendations were built "
            "against a different contract."
        )

    rec_file_text = (
        rec_summary.get(
            "files",
            {},
        )
        .get(
            "recommendations"
        )
    )

    if not rec_file_text:
        raise ProposalError(
            "3F.3 recommendations CSV missing."
        )

    rec_file = Path(
        rec_file_text
    )

    if not rec_file.exists():
        raise ProposalError(
            f"Missing recommendations CSV: "
            f"{rec_file}"
        )

    recommendations = pd.read_csv(
        rec_file,
        low_memory=False,
    )

    if len(recommendations) != 28:
        raise ProposalError(
            "Expected 28 recommendation rows."
        )

    records = []

    for _, row in recommendations.iterrows():
        iid = as_int(
            row["itemid"]
        )

        stack = as_int(
            row["stack"]
        )

        recommendation_state = str(
            row[
                "recommendation_state"
            ]
        )

        domain = str(
            row[
                "recommendation_domain"
            ]
        )

        direction = str(
            row[
                "recommendation_direction"
            ]
        )

        change_candidate = as_int(
            row[
                "change_proposal_candidate"
            ]
        )

        current_value = as_float(
            row[
                "current_value"
            ]
        )

        seller_price = as_float(
            row[
                "seller_price"
            ]
        )

        buyer_bid = as_float(
            row[
                "buyer_bid"
            ]
        )

        proposal_state = (
            "NO_PROPOSAL"
        )

        proposal_reason = (
            "NO_DIRECTIONAL_CHANGE_CANDIDATE"
        )

        proposed_value = None
        lower_bound = None
        upper_bound = None

        bound_type = "NONE"

        safety_prerequisite = (
            "NONE"
        )

        if recommendation_state == "BLOCK":
            proposal_state = "BLOCK"

            proposal_reason = (
                "RECOMMENDATION_IS_BLOCKED"
            )

        elif not change_candidate:
            proposal_state = (
                "NO_PROPOSAL"
            )

            proposal_reason = (
                "FORM_NOT_BOUNDED_PROPOSAL_"
                "ELIGIBLE"
            )

        elif current_value is None:
            proposal_state = (
                "DEFER_BOUND_UNPROVEN"
            )

            proposal_reason = (
                "CURRENT_VALUE_UNAVAILABLE"
            )

        elif domain == "seller_price":
            if direction == "INCREASE":
                lower_bound = (
                    int(current_value)
                )

                upper_bound = max(
                    int(current_value) + 1,
                    math.ceil(
                        current_value
                        * 1.05
                    ),
                )

                proposed_value = (
                    upper_bound
                )

                bound_type = (
                    "MAX_PLUS_5_PERCENT"
                )

                safety_prerequisite = (
                    "NPC_RELEVANCE_RECHECK_"
                    "REQUIRED_IN_SIMULATION"
                )

                proposal_state = (
                    "READY_FOR_SIMULATION"
                )

                proposal_reason = (
                    "SELLER_PRICE_INCREASE_"
                    "BOUNDED_TO_5_PERCENT"
                )

            elif direction == "DECREASE":
                proposal_state = (
                    "DEFER_BOUND_UNPROVEN"
                )

                proposal_reason = (
                    "NPC_VENDOR_FLOOR_REQUIRED_"
                    "BEFORE_PRICE_DECREASE"
                )

                safety_prerequisite = (
                    "VERIFIED_NPC_VENDOR_FLOOR"
                )

            else:
                proposal_state = (
                    "DEFER_BOUND_UNPROVEN"
                )

                proposal_reason = (
                    "UNKNOWN_SELLER_PRICE_DIRECTION"
                )

        elif domain == "buyer_bid":
            if direction == "DECREASE":
                lower_bound = max(
                    1,
                    math.floor(
                        current_value
                        * 0.95
                    ),
                )

                upper_bound = (
                    int(current_value)
                )

                proposed_value = (
                    lower_bound
                )

                bound_type = (
                    "MAX_MINUS_5_PERCENT"
                )

                safety_prerequisite = (
                    "NONE"
                )

                proposal_state = (
                    "READY_FOR_SIMULATION"
                )

                proposal_reason = (
                    "BUYER_BID_DECREASE_"
                    "BOUNDED_TO_5_PERCENT"
                )

            elif direction == "INCREASE":
                proposal_state = (
                    "DEFER_BOUND_UNPROVEN"
                )

                proposal_reason = (
                    "CRAFT_SAFE_CEILING_REQUIRED_"
                    "BEFORE_BID_INCREASE"
                )

                safety_prerequisite = (
                    "VERIFIED_CRAFT_SAFE_CEILING"
                )

                if (
                    seller_price is not None
                    and seller_price > 0
                ):
                    upper_bound = max(
                        1,
                        int(
                            seller_price
                        )
                        - 1,
                    )

                    bound_type = (
                        "DIRECT_SPREAD_CEILING_ONLY"
                    )

            else:
                proposal_state = (
                    "DEFER_BOUND_UNPROVEN"
                )

                proposal_reason = (
                    "UNKNOWN_BUYER_BID_DIRECTION"
                )

        elif domain == "sell_rate":
            step = rate_step(
                current_value,
                relative_fraction=0.20,
                minimum_step=0.01,
                maximum_step=0.05,
            )

            if direction == "INCREASE":
                lower_bound = (
                    current_value
                )

                upper_bound = min(
                    1.0,
                    current_value
                    + step,
                )

                proposed_value = (
                    upper_bound
                )

            elif direction == "DECREASE":
                lower_bound = max(
                    0.0,
                    current_value
                    - step,
                )

                upper_bound = (
                    current_value
                )

                proposed_value = (
                    lower_bound
                )

            else:
                proposal_state = (
                    "DEFER_BOUND_UNPROVEN"
                )

                proposal_reason = (
                    "UNKNOWN_SELL_RATE_DIRECTION"
                )

                step = None

            if step is not None:
                proposed_value = round(
                    proposed_value,
                    6,
                )

                lower_bound = round(
                    lower_bound,
                    6,
                )

                upper_bound = round(
                    upper_bound,
                    6,
                )

                bound_type = (
                    "MAX_20_PERCENT_"
                    "RELATIVE_AND_0_05_ABSOLUTE"
                )

                safety_prerequisite = (
                    "SELLER_TURNOVER_SIMULATION"
                )

                proposal_state = (
                    "READY_FOR_SIMULATION"
                )

                proposal_reason = (
                    "SELL_RATE_CHANGE_"
                    "CONSERVATIVELY_BOUNDED"
                )

        elif domain == "buy_rate":
            step = rate_step(
                current_value,
                relative_fraction=0.25,
                minimum_step=0.001,
                maximum_step=0.005,
            )

            if direction == "INCREASE":
                lower_bound = (
                    current_value
                )

                upper_bound = min(
                    1.0,
                    current_value
                    + step,
                )

                proposed_value = (
                    upper_bound
                )

            elif direction == "DECREASE":
                lower_bound = max(
                    0.0,
                    current_value
                    - step,
                )

                upper_bound = (
                    current_value
                )

                proposed_value = (
                    lower_bound
                )

            else:
                proposal_state = (
                    "DEFER_BOUND_UNPROVEN"
                )

                proposal_reason = (
                    "UNKNOWN_BUY_RATE_DIRECTION"
                )

                step = None

            if step is not None:
                proposed_value = round(
                    proposed_value,
                    6,
                )

                lower_bound = round(
                    lower_bound,
                    6,
                )

                upper_bound = round(
                    upper_bound,
                    6,
                )

                bound_type = (
                    "MAX_25_PERCENT_"
                    "RELATIVE_AND_0_005_ABSOLUTE"
                )

                safety_prerequisite = (
                    "FAUCET_EXPOSURE_SIMULATION"
                )

                proposal_state = (
                    "READY_FOR_SIMULATION"
                )

                proposal_reason = (
                    "BUY_RATE_CHANGE_"
                    "CONSERVATIVELY_BOUNDED"
                )

        elif domain == "stock_target":
            current_int = int(
                round(
                    current_value
                )
            )

            if direction == "INCREASE":
                lower_bound = (
                    current_int
                )

                upper_bound = (
                    current_int
                    + 1
                )

                proposed_value = (
                    upper_bound
                )

            elif direction == "DECREASE":
                lower_bound = max(
                    0,
                    current_int - 1,
                )

                upper_bound = (
                    current_int
                )

                proposed_value = (
                    lower_bound
                )

            else:
                proposal_state = (
                    "DEFER_BOUND_UNPROVEN"
                )

                proposal_reason = (
                    "UNKNOWN_STOCK_DIRECTION"
                )

                proposed_value = None

            if proposed_value is not None:
                bound_type = (
                    "MAX_ONE_UNIT_PER_FORM"
                )

                safety_prerequisite = (
                    "PLAYER_SUPPLY_COUNTS_"
                    "TOWARD_TOTAL_MARKET_TARGET"
                )

                proposal_state = (
                    "READY_FOR_SIMULATION"
                )

                proposal_reason = (
                    "STOCK_CHANGE_BOUNDED_"
                    "TO_ONE_UNIT"
                )

        elif domain in {
            "catalog",
            "catalog_membership",
        }:
            proposal_state = (
                "DEFER_MANUAL_REVIEW"
            )

            proposal_reason = (
                "CATALOG_MEMBERSHIP_REQUIRES_"
                "EXPLICIT_REVIEW"
            )

            safety_prerequisite = (
                "RECIPE_PATH_AND_VENDOR_"
                "POLICY_REAUDIT"
            )

        else:
            proposal_state = (
                "DEFER_BOUND_UNPROVEN"
            )

            proposal_reason = (
                "UNSUPPORTED_PROPOSAL_DOMAIN"
            )

        if (
            proposal_state
            not in ALLOWED_PROPOSAL_STATES
        ):
            raise ProposalError(
                f"{iid}/{stack}: invalid "
                f"proposal state "
                f"{proposal_state}"
            )

        ready_for_simulation = int(
            proposal_state
            == "READY_FOR_SIMULATION"
        )

        proposal_id = (
            f"{iid}:"
            f"{stack}:"
            f"{domain}:"
            f"{direction}"
        )

        records.append({
            "proposal_id":
                proposal_id,

            "itemid":
                iid,

            "name":
                str(
                    row[
                        "name"
                    ]
                ),

            "pilot_lane":
                str(
                    row[
                        "pilot_lane"
                    ]
                ),

            "stack":
                stack,

            "form":
                str(
                    row[
                        "form"
                    ]
                ),

            "control_scope":
                str(
                    row[
                        "control_scope"
                    ]
                ),

            "recommendation_state":
                recommendation_state,

            "recommendation_domain":
                domain,

            "recommendation_direction":
                direction,

            "recommendation_reason":
                str(
                    row[
                        "recommendation_reason"
                    ]
                ),

            "change_proposal_candidate":
                change_candidate,

            "current_value":
                current_value,

            "proposed_value":
                proposed_value,

            "lower_bound":
                lower_bound,

            "upper_bound":
                upper_bound,

            "bound_type":
                bound_type,

            "safety_prerequisite":
                safety_prerequisite,

            "proposal_state":
                proposal_state,

            "proposal_reason":
                proposal_reason,

            "ready_for_simulation":
                ready_for_simulation,

            "rollback_value":
                current_value,

            "human_approval_required":
                1,

            "runtime_publish_allowed":
                0,

            "database_mutation_allowed":
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
        })

    proposals = pd.DataFrame(
        records
    ).sort_values(
        [
            "pilot_lane",
            "itemid",
            "stack",
        ]
    )

    if len(proposals) != 28:
        raise ProposalError(
            "Expected 28 proposal rows."
        )

    authority_columns = [
        "price_change_authorized",
        "rate_change_authorized",
        "stock_change_authorized",
        "catalog_change_authorized",
        "runtime_mutation_authorized",
        "database_mutation_authorized",
        "auto_live_promotion",
    ]

    for column in authority_columns:
        if as_int(
            proposals[
                column
            ].sum()
        ) != 0:
            raise ProposalError(
                f"Unexpected authority in "
                f"{column}."
            )

    candidate_count = as_int(
        proposals[
            "change_proposal_candidate"
        ].sum()
    )

    simulation_ready = as_int(
        proposals[
            "ready_for_simulation"
        ].sum()
    )

    deferred_unproven = as_int(
        (
            proposals[
                "proposal_state"
            ]
            == "DEFER_BOUND_UNPROVEN"
        ).sum()
    )

    deferred_manual = as_int(
        (
            proposals[
                "proposal_state"
            ]
            == "DEFER_MANUAL_REVIEW"
        ).sum()
    )

    blocked = as_int(
        (
            proposals[
                "proposal_state"
            ]
            == "BLOCK"
        ).sum()
    )

    no_proposal = as_int(
        (
            proposals[
                "proposal_state"
            ]
            == "NO_PROPOSAL"
        ).sum()
    )

    state_counts = (
        proposals[
            "proposal_state"
        ]
        .value_counts()
        .sort_index()
        .to_dict()
    )

    if blocked:
        proposal_set_state = (
            "BLOCKED_PROPOSALS_PRESENT"
        )

    elif simulation_ready:
        proposal_set_state = (
            "BOUNDED_PROPOSALS_READY_FOR_SIMULATION"
        )

    elif candidate_count:
        proposal_set_state = (
            "CANDIDATES_DEFERRED_BY_BOUND_SAFETY"
        )

    else:
        proposal_set_state = (
            "NO_CHANGE_PROPOSALS"
        )

    now = datetime.now(
        timezone.utc
    )

    stamp = now.strftime(
        "%Y%m%dT%H%M%SZ"
    )

    PROPOSAL_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    proposals_file = (
        PROPOSAL_ROOT
        / f"{stamp}-proposals.csv"
    )

    summary_file = (
        PROPOSAL_ROOT
        / f"{stamp}-summary.json"
    )

    proposals.to_csv(
        proposals_file,
        index=False,
    )

    summary = {
        "status":
            "PASS",

        "phase":
            "3F.4",

        "observation_utc":
            now.isoformat(),

        "proposal_set_state":
            proposal_set_state,

        "contract_version":
            as_int(
                contract.get(
                    "contract_version"
                )
            ),

        "contract_sha256":
            sha256(
                CONTRACT_FILE
            ),

        "control_mode":
            contract.get(
                "control_mode"
            ),

        "pilot_items":
            int(
                proposals[
                    "itemid"
                ].nunique()
            ),

        "pilot_forms":
            len(
                proposals
            ),

        "change_proposal_candidates":
            candidate_count,

        "simulation_ready_proposals":
            simulation_ready,

        "deferred_bound_unproven":
            deferred_unproven,

        "deferred_manual_review":
            deferred_manual,

        "blocked_proposals":
            blocked,

        "no_proposal_forms":
            no_proposal,

        "proposal_state_counts": {
            str(k):
                int(v)
            for k, v
            in state_counts.items()
        },

        "bound_policy": {
            "seller_price_increase_max_fraction":
                0.05,

            "seller_price_decrease_requires_verified_npc_floor":
                True,

            "buyer_bid_increase_requires_verified_craft_safe_ceiling":
                True,

            "buyer_bid_decrease_max_fraction":
                0.05,

            "sell_rate_max_relative_change":
                0.20,

            "sell_rate_max_absolute_change":
                0.05,

            "buy_rate_max_relative_change":
                0.25,

            "buy_rate_max_absolute_change":
                0.005,

            "stock_target_max_unit_change":
                1,

            "catalog_membership_requires_manual_review":
                True,
        },

        "interpretation_contract": {
            "proposal_is_live_change":
                False,

            "proposal_is_approved_change":
                False,

            "ready_for_simulation_is_approval":
                False,

            "human_approval_still_required":
                True,

            "automatic_application_allowed":
                False,
        },

        "human_approval_required":
            1,

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

            "recommendation_summary":
                str(
                    rec_summary_path
                ),

            "recommendations":
                str(
                    rec_file
                ),
        },

        "files": {
            "proposals":
                str(
                    proposals_file
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
        " Phase 3F.4 Bounded Change Proposals"
    )
    print("=" * 112)
    print()

    display_columns = [
        "itemid",
        "name",
        "pilot_lane",
        "form",
        "recommendation_state",
        "recommendation_domain",
        "recommendation_direction",
        "current_value",
        "proposed_value",
        "bound_type",
        "proposal_state",
        "proposal_reason",
        "ready_for_simulation",
    ]

    print(
        proposals[
            display_columns
        ].to_string(
            index=False
        )
    )

    print()
    print(
        "Proposal-set state:               ",
        proposal_set_state,
    )

    print()
    print(
        "Change proposal candidates:       ",
        candidate_count,
    )

    print(
        "Simulation-ready proposals:       ",
        simulation_ready,
    )

    print(
        "Deferred, bound unproven:          ",
        deferred_unproven,
    )

    print(
        "Deferred, manual review:           ",
        deferred_manual,
    )

    print(
        "Blocked proposals:                 ",
        blocked,
    )

    print(
        "No-proposal forms:                 ",
        no_proposal,
    )

    print()
    print("Proposal states:")

    for key in sorted(
        state_counts
    ):
        print(
            f"  {key:<28}",
            state_counts[key],
        )

    print()
    print(
        "Human approval required:           1"
    )

    print(
        "Automatic application allowed:     0"
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
        "Proposals:",
        proposals_file,
    )

    print(
        "Summary:",
        summary_file,
    )

    print()
    print(summary["status"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
