from __future__ import annotations

import json
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

OUTPUT = (
    POLICY_ROOT
    / "adaptive-control-contract.json"
)


class ContractError(RuntimeError):
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
            bad.append(
                key
            )

    if bad:
        raise ContractError(
            f"{label} grants unexpected "
            "authority: "
            + ", ".join(bad)
        )


def main() -> int:
    for path in [
        PHASE_3D_CLOSEOUT,
        PHASE_3E_CLOSEOUT,
    ]:
        if not path.exists():
            raise ContractError(
                f"Missing required closeout: {path}"
            )

    phase_3d = json.loads(
        PHASE_3D_CLOSEOUT.read_text(
            encoding="utf-8",
        )
    )

    phase_3e = json.loads(
        PHASE_3E_CLOSEOUT.read_text(
            encoding="utf-8",
        )
    )

    if phase_3d.get("status") != "PASS":
        raise ContractError(
            "Phase 3D closeout is not PASS."
        )

    if phase_3e.get("status") != "PASS":
        raise ContractError(
            "Phase 3E closeout is not PASS."
        )

    if as_int(
        phase_3d.get(
            "engineering_complete"
        )
    ) != 1:
        raise ContractError(
            "Phase 3D engineering is incomplete."
        )

    if as_int(
        phase_3e.get(
            "engineering_complete"
        )
    ) != 1:
        raise ContractError(
            "Phase 3E engineering is incomplete."
        )

    if as_int(
        phase_3e.get(
            "safe_to_proceed_to_phase_3f_design"
        )
    ) != 1:
        raise ContractError(
            "Phase 3E does not permit "
            "Phase 3F design."
        )

    require_zero_authority(
        "Phase 3D",
        phase_3d,
    )

    require_zero_authority(
        "Phase 3E",
        phase_3e,
    )

    contract = {
        "status":
            "PASS",

        "phase":
            "3F.1",

        "contract_version":
            1,

        "control_mode":
            "PROPOSAL_ONLY",

        "current_activation_state":
            "DESIGN_ONLY_LOCKED",

        "phase_3f_design_allowed":
            1,

        "analysis_allowed":
            1,

        "recommendation_generation_allowed":
            1,

        "simulation_allowed":
            1,

        "proposal_generation_allowed":
            1,

        "live_runtime_publish_allowed":
            0,

        "live_database_mutation_allowed":
            0,

        "automatic_service_restart_allowed":
            0,

        "automatic_change_application_allowed":
            0,

        "human_approval_required":
            1,

        "control_domains": {
            "seller_price": {
                "analysis_allowed":
                    1,

                "proposal_allowed":
                    1,

                "live_apply_allowed":
                    0,
            },

            "buyer_bid": {
                "analysis_allowed":
                    1,

                "proposal_allowed":
                    1,

                "live_apply_allowed":
                    0,
            },

            "sell_rate": {
                "analysis_allowed":
                    1,

                "proposal_allowed":
                    1,

                "live_apply_allowed":
                    0,
            },

            "buy_rate": {
                "analysis_allowed":
                    1,

                "proposal_allowed":
                    1,

                "live_apply_allowed":
                    0,
            },

            "stock_target": {
                "analysis_allowed":
                    1,

                "proposal_allowed":
                    1,

                "live_apply_allowed":
                    0,
            },

            "catalog_membership": {
                "analysis_allowed":
                    1,

                "proposal_allowed":
                    1,

                "live_apply_allowed":
                    0,
            },
        },

        "hard_invariants": {
            "preserve_completed_sale_history":
                True,

            "normal_maintenance_may_delete_completed_history":
                False,

            "normal_maintenance_may_remove_only_active_unsold_bot_rows":
                True,

            "npc_vendor_relevance_required":
                True,

            "seller_may_undercut_npc_vendor":
                False,

            "npc_convenience_premium_policy_required":
                True,

            "buyer_bid_may_meet_or_exceed_seller_ask":
                False,

            "repeatable_craft_arbitrage_allowed":
                False,

            "player_listings_count_toward_total_market_stock":
                True,

            "catalog_auto_promotion_allowed":
                False,

            "market_history_auto_price_influence":
                False,

            "market_history_auto_stock_influence":
                False,

            "observation_auto_activation_allowed":
                False,

            "source_integrity_failure_is_fail_closed":
                True,
        },

        "evidence_dependencies": {
            "market_response": {
                "required":
                    True,

                "source_phase":
                    "3D",

                "current_evidence_state":
                    phase_3d.get(
                        "evidence_state"
                    ),

                "currently_mature":
                    as_int(
                        phase_3d.get(
                            "market_evidence_mature"
                        )
                    ),
            },

            "gil_flow": {
                "required":
                    True,

                "source_phase":
                    "3E",

                "current_evidence_state":
                    phase_3e.get(
                        "evidence_state"
                    ),

                "currently_mature":
                    as_int(
                        phase_3e.get(
                            "actual_flow_evidence_mature"
                        )
                    ),

                "budget_state":
                    phase_3e.get(
                        "budget_state"
                    ),

                "watch_count":
                    as_int(
                        phase_3e.get(
                            "watch_count"
                        )
                    ),

                "review_count":
                    as_int(
                        phase_3e.get(
                            "review_count"
                        )
                    ),

                "block_count":
                    as_int(
                        phase_3e.get(
                            "block_count"
                        )
                    ),
            },

            "craft_and_repeatability": {
                "required":
                    True,

                "expansion_requires_recipe_path_reaudit":
                    as_int(
                        phase_3e.get(
                            "full_recipe_path_reaudit_required_for_expansion"
                        )
                    ),
            },

            "npc_vendor_policy": {
                "required":
                    True,
            },
        },

        "proposal_states": [
            "NO_CHANGE",
            "INSUFFICIENT_EVIDENCE",
            "HOLD",
            "REVIEW_INCREASE",
            "REVIEW_DECREASE",
            "REVIEW_ADD",
            "REVIEW_REMOVE",
            "DEFER",
            "BLOCK",
        ],

        "proposal_requirements": {
            "must_include_reason":
                True,

            "must_include_evidence":
                True,

            "must_include_before_value":
                True,

            "must_include_proposed_value":
                True,

            "must_include_bound":
                True,

            "must_include_safety_checks":
                True,

            "must_include_rollback_plan":
                True,

            "must_include_human_approval_state":
                True,
        },

        "activation_requirements": {
            "human_approval":
                True,

            "fresh_validation":
                True,

            "runtime_backup":
                True,

            "atomic_publish":
                True,

            "post_publish_validation":
                True,

            "service_health_check":
                True,

            "post_activation_observation":
                True,

            "automatic_activation":
                False,
        },

        "phase_3f_authority": {
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
        },

        "source_closeouts": {
            "3D":
                str(
                    PHASE_3D_CLOSEOUT
                ),

            "3E":
                str(
                    PHASE_3E_CLOSEOUT
                ),
        },
    }

    POLICY_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT.write_text(
        json.dumps(
            contract,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print("=" * 110)
    print(
        " Phase 3F.1 Adaptive Control Contract"
    )
    print("=" * 110)
    print()

    print(
        "Contract version:                  ",
        contract[
            "contract_version"
        ],
    )

    print(
        "Control mode:                      ",
        contract[
            "control_mode"
        ],
    )

    print(
        "Activation state:                  ",
        contract[
            "current_activation_state"
        ],
    )

    print()
    print(
        "Phase 3F design allowed:           ",
        contract[
            "phase_3f_design_allowed"
        ],
    )

    print(
        "Analysis allowed:                  ",
        contract[
            "analysis_allowed"
        ],
    )

    print(
        "Recommendations allowed:           ",
        contract[
            "recommendation_generation_allowed"
        ],
    )

    print(
        "Simulation allowed:                ",
        contract[
            "simulation_allowed"
        ],
    )

    print(
        "Proposal generation allowed:       ",
        contract[
            "proposal_generation_allowed"
        ],
    )

    print()
    print(
        "Market evidence mature:            ",
        contract[
            "evidence_dependencies"
        ][
            "market_response"
        ][
            "currently_mature"
        ],
    )

    print(
        "Gil-flow evidence mature:          ",
        contract[
            "evidence_dependencies"
        ][
            "gil_flow"
        ][
            "currently_mature"
        ],
    )

    print(
        "Gil-flow budget state:             ",
        contract[
            "evidence_dependencies"
        ][
            "gil_flow"
        ][
            "budget_state"
        ],
    )

    print(
        "Gil-flow WATCH gates:              ",
        contract[
            "evidence_dependencies"
        ][
            "gil_flow"
        ][
            "watch_count"
        ],
    )

    print(
        "Gil-flow REVIEW gates:             ",
        contract[
            "evidence_dependencies"
        ][
            "gil_flow"
        ][
            "review_count"
        ],
    )

    print(
        "Gil-flow BLOCK gates:              ",
        contract[
            "evidence_dependencies"
        ][
            "gil_flow"
        ][
            "block_count"
        ],
    )

    print()
    print(
        "Human approval required:           ",
        contract[
            "human_approval_required"
        ],
    )

    print(
        "Live runtime publishing allowed:   ",
        contract[
            "live_runtime_publish_allowed"
        ],
    )

    print(
        "Live DB mutation allowed:          ",
        contract[
            "live_database_mutation_allowed"
        ],
    )

    print(
        "Automatic application allowed:     ",
        contract[
            "automatic_change_application_allowed"
        ],
    )

    print()
    print(
        "Completed history preserved:       ",
        int(
            contract[
                "hard_invariants"
            ][
                "preserve_completed_sale_history"
            ]
        ),
    )

    print(
        "NPC undercut allowed:              ",
        int(
            contract[
                "hard_invariants"
            ][
                "seller_may_undercut_npc_vendor"
            ]
        ),
    )

    print(
        "Buyer >= seller allowed:           ",
        int(
            contract[
                "hard_invariants"
            ][
                "buyer_bid_may_meet_or_exceed_seller_ask"
            ]
        ),
    )

    print(
        "Auto catalog promotion allowed:    ",
        int(
            contract[
                "hard_invariants"
            ][
                "catalog_auto_promotion_allowed"
            ]
        ),
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
        "Contract:",
        OUTPUT,
    )

    print()
    print(contract["status"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
