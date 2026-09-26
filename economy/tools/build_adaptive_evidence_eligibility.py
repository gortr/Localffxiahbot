from __future__ import annotations

import hashlib
import json
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

EVIDENCE_ROOT = (
    ADAPTIVE_ROOT
    / "evidence"
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

PHASE_3E_SAFETY = (
    OBS_ROOT
    / "gil-flow"
    / "safety"
    / "current-safety-summary.json"
)


class EligibilityError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open(
        "rb"
    ) as handle:
        for chunk in iter(
            lambda: handle.read(
                1024 * 1024
            ),
            b"",
        ):
            h.update(
                chunk
            )

    return h.hexdigest()


def as_int(value: Any) -> int:
    if value is None:
        return 0

    try:
        if pd.isna(value):
            return 0
    except TypeError:
        pass

    return int(
        float(value)
    )


def as_float(value: Any):
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass

    return float(value)


def require_file(
    path: Path,
) -> None:
    if not path.exists():
        raise EligibilityError(
            f"Missing required input: {path}"
        )


def load_json(
    path: Path,
) -> dict:
    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


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
                data.get(
                    key
                )
            )
            != 0
        ):
            bad.append(
                key
            )

    if bad:
        raise EligibilityError(
            f"{label} grants unexpected "
            "authority: "
            + ", ".join(
                bad
            )
        )


def load_referenced_json(
    parent: dict,
    key: str,
) -> tuple[Path, dict]:
    source_map = parent.get(
        "source_summaries",
        {},
    )

    value = source_map.get(
        key
    )

    if not value:
        raise EligibilityError(
            f"Missing source summary reference: "
            f"{key}"
        )

    path = Path(
        value
    )

    require_file(
        path
    )

    return (
        path,
        load_json(
            path
        ),
    )


def main() -> int:
    for path in [
        CONTRACT_FILE,
        PHASE_3D_CLOSEOUT,
        PHASE_3E_CLOSEOUT,
        PHASE_3E_SAFETY,
    ]:
        require_file(
            path
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

    phase_3e_safety = load_json(
        PHASE_3E_SAFETY
    )

    # --------------------------------------------------
    # Contract / closeout validation
    # --------------------------------------------------

    if contract.get(
        "status"
    ) != "PASS":
        raise EligibilityError(
            "3F.1 contract is not PASS."
        )

    if contract.get(
        "control_mode"
    ) != "PROPOSAL_ONLY":
        raise EligibilityError(
            "3F contract is not "
            "PROPOSAL_ONLY."
        )

    if contract.get(
        "current_activation_state"
    ) != "DESIGN_ONLY_LOCKED":
        raise EligibilityError(
            "Unexpected adaptive "
            "activation state."
        )

    if as_int(
        contract.get(
            "analysis_allowed"
        )
    ) != 1:
        raise EligibilityError(
            "Contract does not allow analysis."
        )

    if as_int(
        contract.get(
            "simulation_allowed"
        )
    ) != 1:
        raise EligibilityError(
            "Contract does not allow simulation."
        )

    if phase_3d.get(
        "status"
    ) != "PASS":
        raise EligibilityError(
            "Phase 3D closeout is not PASS."
        )

    if phase_3e.get(
        "status"
    ) != "PASS":
        raise EligibilityError(
            "Phase 3E closeout is not PASS."
        )

    if phase_3e_safety.get(
        "status"
    ) != "PASS":
        raise EligibilityError(
            "Phase 3E safety state is not PASS."
        )

    require_zero_authority(
        "3F contract",
        contract,
    )

    require_zero_authority(
        "3D closeout",
        phase_3d,
    )

    require_zero_authority(
        "3E closeout",
        phase_3e,
    )

    require_zero_authority(
        "3E safety",
        phase_3e_safety,
    )

    if as_int(
        phase_3e.get(
            "block_count"
        )
    ) != 0:
        raise EligibilityError(
            "Phase 3E has active BLOCK gates."
        )

    # --------------------------------------------------
    # Load the exact 3D.5 evidence source used by
    # the validated 3D closeout.
    # --------------------------------------------------

    rec_path, rec_summary = (
        load_referenced_json(
            phase_3d,
            "3D.5",
        )
    )

    if rec_summary.get(
        "status"
    ) != "PASS":
        raise EligibilityError(
            "Referenced 3D.5 summary "
            "is not PASS."
        )

    rec_files = rec_summary.get(
        "files",
        {},
    )

    item_matrix_path = Path(
        rec_files.get(
            "item_matrix",
            "",
        )
    )

    form_evidence_path = Path(
        rec_files.get(
            "form_evidence",
            "",
        )
    )

    require_file(
        item_matrix_path
    )

    require_file(
        form_evidence_path
    )

    item_matrix = pd.read_csv(
        item_matrix_path,
        low_memory=False,
    )

    form_evidence = pd.read_csv(
        form_evidence_path,
        low_memory=False,
    )

    if len(
        item_matrix
    ) != 14:
        raise EligibilityError(
            "Expected 14 pilot items."
        )

    if len(
        form_evidence
    ) != 28:
        raise EligibilityError(
            "Expected 28 pilot forms."
        )

    # --------------------------------------------------
    # Load current 3E safety evidence.
    # --------------------------------------------------

    safety_files = (
        phase_3e_safety.get(
            "files",
            {},
        )
    )

    item_budget_path = Path(
        safety_files.get(
            "item_budgets",
            "",
        )
    )

    form_spread_path = Path(
        safety_files.get(
            "form_spread_gates",
            "",
        )
    )

    require_file(
        item_budget_path
    )

    require_file(
        form_spread_path
    )

    item_budgets = pd.read_csv(
        item_budget_path,
        low_memory=False,
    )

    form_spreads = pd.read_csv(
        form_spread_path,
        low_memory=False,
    )

    item_matrix_by_id = (
        item_matrix
        .set_index(
            "itemid"
        )
    )

    item_budget_by_id = (
        item_budgets
        .set_index(
            "itemid"
        )
    )

    spread_index = (
        form_spreads
        .set_index(
            [
                "itemid",
                "stack",
            ]
        )
    )

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

    budget_state = str(
        phase_3e.get(
            "budget_state",
            ""
        )
    )

    global_watch_count = as_int(
        phase_3e.get(
            "watch_count"
        )
    )

    global_review_count = as_int(
        phase_3e.get(
            "review_count"
        )
    )

    global_block_count = as_int(
        phase_3e.get(
            "block_count"
        )
    )

    recommendation_allowed = as_int(
        contract.get(
            "recommendation_generation_allowed"
        )
    )

    proposal_allowed = as_int(
        contract.get(
            "proposal_generation_allowed"
        )
    )

    simulation_allowed = as_int(
        contract.get(
            "simulation_allowed"
        )
    )

    analysis_allowed = as_int(
        contract.get(
            "analysis_allowed"
        )
    )

    records = []

    # --------------------------------------------------
    # Per-form eligibility
    # --------------------------------------------------

    for _, form in form_evidence.iterrows():
        iid = as_int(
            form[
                "itemid"
            ]
        )

        stack = as_int(
            form[
                "stack"
            ]
        )

        if iid not in item_matrix_by_id.index:
            raise EligibilityError(
                f"Missing 3D.5 item evidence "
                f"for {iid}."
            )

        item = item_matrix_by_id.loc[
            iid
        ]

        budget_state_item = (
            "UNKNOWN"
        )

        budget_reason = (
            "NO_ITEM_BUDGET_RECORD"
        )

        buyer_exposure_share = None

        if iid in item_budget_by_id.index:
            budget = item_budget_by_id.loc[
                iid
            ]

            budget_state_item = str(
                budget[
                    "budget_state"
                ]
            )

            budget_reason = str(
                budget[
                    "budget_reason"
                ]
            )

            buyer_exposure_share = as_float(
                budget[
                    "buyer_expected_exposure_share"
                ]
            )

        spread_state = (
            "NOT_COMPARABLE"
        )

        spread_reason = (
            "NO_FORM_SPREAD_RECORD"
        )

        buyer_to_seller_ratio = None

        key = (
            iid,
            stack,
        )

        if key in spread_index.index:
            spread = spread_index.loc[
                key
            ]

            spread_state = str(
                spread[
                    "spread_state"
                ]
            )

            spread_reason = str(
                spread[
                    "spread_reason"
                ]
            )

            buyer_to_seller_ratio = (
                as_float(
                    spread[
                        "buyer_to_seller_ratio"
                    ]
                )
            )

        seller_enabled = as_int(
            form[
                "seller_enabled"
            ]
        )

        buyer_enabled = as_int(
            form[
                "buyer_enabled"
            ]
        )

        if (
            seller_enabled
            and buyer_enabled
        ):
            control_scope = (
                "TWO_SIDED"
            )

        elif seller_enabled:
            control_scope = (
                "SELLER"
            )

        elif buyer_enabled:
            control_scope = (
                "BUYER"
            )

        else:
            control_scope = (
                "OBSERVATION_ONLY"
            )

        demand_sales = as_int(
            form[
                "ahbot_to_player_sales"
            ]
        )

        supply_sales = as_int(
            form[
                "player_to_ahbot_sales"
            ]
        )

        organic_trades = as_int(
            form[
                "organic_player_trades"
            ]
        )

        form_player_events = (
            demand_sales
            + supply_sales
            + organic_trades
        )

        item_evidence_ready = as_int(
            item[
                "evidence_ready"
            ]
        )

        item_recommendation = str(
            item[
                "recommendation"
            ]
        )

        item_recommendation_reason = str(
            item[
                "recommendation_reason"
            ]
        )

        active_unknown = as_int(
            form[
                "current_active_unknown"
            ]
        )

        unclassified_events = as_int(
            form[
                "unclassified_events"
            ]
        )

        structural_block = int(
            global_block_count > 0
            or spread_state == "BLOCK"
            or active_unknown > 0
            or unclassified_events > 0
        )

        structural_review = int(
            global_review_count > 0
            or spread_state == "REVIEW"
            or budget_state_item == "REVIEW"
        )

        analysis_eligible = int(
            analysis_allowed
            and not structural_block
        )

        simulation_eligible = int(
            simulation_allowed
            and not structural_block
        )

        maturity_ready = int(
            market_evidence_mature
            and gil_flow_evidence_mature
        )

        recommendation_eligible = int(
            recommendation_allowed
            and not structural_block
            and not structural_review
            and maturity_ready
            and item_evidence_ready
        )

        bounded_proposal_eligible = int(
            proposal_allowed
            and recommendation_eligible
            and item_recommendation
            not in {
                "OBSERVE",
                "DEFER",
            }
        )

        if structural_block:
            eligibility_state = (
                "BLOCKED"
            )

            eligibility_reason = (
                "STRUCTURAL_SAFETY_BLOCK"
            )

        elif structural_review:
            eligibility_state = (
                "REVIEW_HOLD"
            )

            eligibility_reason = (
                "SAFETY_REVIEW_GATE_ACTIVE"
            )

        elif not market_evidence_mature:
            eligibility_state = (
                "INSUFFICIENT_EVIDENCE"
            )

            eligibility_reason = (
                "MARKET_RESPONSE_EVIDENCE_"
                "IMMATURE"
            )

        elif not gil_flow_evidence_mature:
            eligibility_state = (
                "INSUFFICIENT_EVIDENCE"
            )

            eligibility_reason = (
                "GIL_FLOW_EVIDENCE_"
                "IMMATURE"
            )

        elif not item_evidence_ready:
            eligibility_state = (
                "INSUFFICIENT_EVIDENCE"
            )

            eligibility_reason = (
                "ITEM_EVIDENCE_GATE_"
                "NOT_READY"
            )

        elif recommendation_eligible:
            eligibility_state = (
                "RECOMMENDATION_ELIGIBLE"
            )

            eligibility_reason = (
                "ALL_EVIDENCE_GATES_PASS"
            )

        else:
            eligibility_state = (
                "HOLD"
            )

            eligibility_reason = (
                "CONTRACT_OR_POLICY_HOLD"
            )

        records.append({
            "itemid":
                iid,

            "name":
                str(
                    form[
                        "name"
                    ]
                ),

            "pilot_lane":
                str(
                    form[
                        "pilot_lane"
                    ]
                ),

            "stack":
                stack,

            "form":
                str(
                    form[
                        "form"
                    ]
                ),

            "control_scope":
                control_scope,

            "seller_enabled":
                seller_enabled,

            "seller_price":
                as_float(
                    form[
                        "seller_price"
                    ]
                ),

            "buyer_enabled":
                buyer_enabled,

            "buyer_bid":
                as_float(
                    form[
                        "buyer_bid"
                    ]
                ),

            "signal_state":
                str(
                    form[
                        "signal_state"
                    ]
                ),

            "form_player_events":
                form_player_events,

            "ahbot_to_player_sales":
                demand_sales,

            "player_to_ahbot_sales":
                supply_sales,

            "organic_player_trades":
                organic_trades,

            "item_evidence_ready":
                item_evidence_ready,

            "item_recommendation":
                item_recommendation,

            "item_recommendation_reason":
                item_recommendation_reason,

            "market_evidence_mature":
                market_evidence_mature,

            "gil_flow_evidence_mature":
                gil_flow_evidence_mature,

            "global_budget_state":
                budget_state,

            "global_watch_count":
                global_watch_count,

            "global_review_count":
                global_review_count,

            "global_block_count":
                global_block_count,

            "item_budget_state":
                budget_state_item,

            "item_budget_reason":
                budget_reason,

            "buyer_expected_exposure_share":
                buyer_exposure_share,

            "direct_spread_state":
                spread_state,

            "direct_spread_reason":
                spread_reason,

            "buyer_to_seller_ratio":
                buyer_to_seller_ratio,

            "structural_block":
                structural_block,

            "structural_review":
                structural_review,

            "analysis_eligible":
                analysis_eligible,

            "simulation_eligible":
                simulation_eligible,

            "recommendation_eligible":
                recommendation_eligible,

            "bounded_proposal_eligible":
                bounded_proposal_eligible,

            "eligibility_state":
                eligibility_state,

            "eligibility_reason":
                eligibility_reason,

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

    eligibility = pd.DataFrame(
        records
    ).sort_values(
        [
            "pilot_lane",
            "itemid",
            "stack",
        ]
    )

    if len(
        eligibility
    ) != 28:
        raise EligibilityError(
            "Expected 28 eligibility rows."
        )

    # --------------------------------------------------
    # Validate zero authority
    # --------------------------------------------------

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
            eligibility[
                column
            ].sum()
        ) != 0:
            raise EligibilityError(
                f"Unexpected authority in "
                f"{column}."
            )

    analysis_count = as_int(
        eligibility[
            "analysis_eligible"
        ].sum()
    )

    simulation_count = as_int(
        eligibility[
            "simulation_eligible"
        ].sum()
    )

    recommendation_count = as_int(
        eligibility[
            "recommendation_eligible"
        ].sum()
    )

    proposal_count = as_int(
        eligibility[
            "bounded_proposal_eligible"
        ].sum()
    )

    blocked_count = as_int(
        (
            eligibility[
                "eligibility_state"
            ]
            == "BLOCKED"
        ).sum()
    )

    review_hold_count = as_int(
        (
            eligibility[
                "eligibility_state"
            ]
            == "REVIEW_HOLD"
        ).sum()
    )

    insufficient_count = as_int(
        (
            eligibility[
                "eligibility_state"
            ]
            == "INSUFFICIENT_EVIDENCE"
        ).sum()
    )

    eligible_count = as_int(
        (
            eligibility[
                "eligibility_state"
            ]
            == "RECOMMENDATION_ELIGIBLE"
        ).sum()
    )

    state_counts = (
        eligibility[
            "eligibility_state"
        ]
        .value_counts()
        .sort_index()
        .to_dict()
    )

    scope_counts = (
        eligibility[
            "control_scope"
        ]
        .value_counts()
        .sort_index()
        .to_dict()
    )

    now = datetime.now(
        timezone.utc
    )

    stamp = now.strftime(
        "%Y%m%dT%H%M%SZ"
    )

    EVIDENCE_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    eligibility_file = (
        EVIDENCE_ROOT
        / f"{stamp}-eligibility.csv"
    )

    summary_file = (
        EVIDENCE_ROOT
        / f"{stamp}-summary.json"
    )

    eligibility.to_csv(
        eligibility_file,
        index=False,
    )

    summary = {
        "status":
            "PASS",

        "phase":
            "3F.2",

        "observation_utc":
            now.isoformat(),

        "contract_sha256":
            sha256(
                CONTRACT_FILE
            ),

        "contract_version":
            as_int(
                contract.get(
                    "contract_version"
                )
            ),

        "control_mode":
            contract.get(
                "control_mode"
            ),

        "activation_state":
            contract.get(
                "current_activation_state"
            ),

        "pilot_items":
            int(
                eligibility[
                    "itemid"
                ].nunique()
            ),

        "pilot_forms":
            len(
                eligibility
            ),

        "market_evidence_mature":
            market_evidence_mature,

        "gil_flow_evidence_mature":
            gil_flow_evidence_mature,

        "global_budget_state":
            budget_state,

        "global_watch_count":
            global_watch_count,

        "global_review_count":
            global_review_count,

        "global_block_count":
            global_block_count,

        "analysis_eligible_forms":
            analysis_count,

        "simulation_eligible_forms":
            simulation_count,

        "recommendation_eligible_forms":
            recommendation_count,

        "bounded_proposal_eligible_forms":
            proposal_count,

        "blocked_forms":
            blocked_count,

        "review_hold_forms":
            review_hold_count,

        "insufficient_evidence_forms":
            insufficient_count,

        "recommendation_ready_forms":
            eligible_count,

        "eligibility_state_counts": {
            str(k):
                int(v)
            for k, v
            in state_counts.items()
        },

        "control_scope_counts": {
            str(k):
                int(v)
            for k, v
            in scope_counts.items()
        },

        "interpretation_contract": {
            "analysis_eligibility_grants_live_authority":
                False,

            "simulation_eligibility_grants_live_authority":
                False,

            "recommendation_eligibility_grants_live_authority":
                False,

            "proposal_eligibility_grants_live_authority":
                False,

            "human_approval_still_required":
                True,

            "automatic_application_allowed":
                False,
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

        "sources": {
            "contract":
                str(
                    CONTRACT_FILE
                ),

            "phase_3d_closeout":
                str(
                    PHASE_3D_CLOSEOUT
                ),

            "phase_3e_closeout":
                str(
                    PHASE_3E_CLOSEOUT
                ),

            "phase_3d5_summary":
                str(
                    rec_path
                ),

            "phase_3d5_item_matrix":
                str(
                    item_matrix_path
                ),

            "phase_3d5_form_evidence":
                str(
                    form_evidence_path
                ),

            "phase_3e_safety":
                str(
                    PHASE_3E_SAFETY
                ),

            "phase_3e_item_budgets":
                str(
                    item_budget_path
                ),

            "phase_3e_form_spreads":
                str(
                    form_spread_path
                ),
        },

        "files": {
            "eligibility":
                str(
                    eligibility_file
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
        " Phase 3F.2 Adaptive Evidence "
        "Eligibility Engine"
    )
    print("=" * 112)
    print()

    display_columns = [
        "itemid",
        "name",
        "pilot_lane",
        "form",
        "control_scope",
        "form_player_events",
        "item_evidence_ready",
        "market_evidence_mature",
        "gil_flow_evidence_mature",
        "item_budget_state",
        "direct_spread_state",
        "analysis_eligible",
        "simulation_eligible",
        "recommendation_eligible",
        "bounded_proposal_eligible",
        "eligibility_state",
        "eligibility_reason",
    ]

    print(
        eligibility[
            display_columns
        ].to_string(
            index=False
        )
    )

    print()
    print(
        "Contract version:                 ",
        summary[
            "contract_version"
        ],
    )

    print(
        "Control mode:                     ",
        summary[
            "control_mode"
        ],
    )

    print(
        "Activation state:                 ",
        summary[
            "activation_state"
        ],
    )

    print()
    print(
        "Pilot items:                      ",
        summary[
            "pilot_items"
        ],
    )

    print(
        "Pilot forms:                      ",
        summary[
            "pilot_forms"
        ],
    )

    print()
    print(
        "Market evidence mature:           ",
        market_evidence_mature,
    )

    print(
        "Gil-flow evidence mature:         ",
        gil_flow_evidence_mature,
    )

    print(
        "Gil-flow budget state:            ",
        budget_state,
    )

    print()
    print(
        "Analysis eligible forms:          ",
        analysis_count,
    )

    print(
        "Simulation eligible forms:        ",
        simulation_count,
    )

    print(
        "Recommendation eligible forms:    ",
        recommendation_count,
    )

    print(
        "Bounded proposal eligible forms:  ",
        proposal_count,
    )

    print()
    print(
        "Insufficient-evidence forms:      ",
        insufficient_count,
    )

    print(
        "Review-hold forms:                ",
        review_hold_count,
    )

    print(
        "Blocked forms:                    ",
        blocked_count,
    )

    print()
    print("Eligibility states:")

    for key in sorted(
        state_counts
    ):
        print(
            f"  {key:<28}",
            state_counts[
                key
            ],
        )

    print()
    print("Control scopes:")

    for key in sorted(
        scope_counts
    ):
        print(
            f"  {key:<28}",
            scope_counts[
                key
            ],
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
        "Runtime mutation authorized:      0"
    )

    print(
        "Database mutation authorized:     0"
    )

    print(
        "Auto live promotion:              0"
    )

    print()
    print(
        "Eligibility:",
        eligibility_file,
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
