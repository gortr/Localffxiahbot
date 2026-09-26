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

RECOMMENDATION_ROOT = (
    ADAPTIVE_ROOT
    / "recommendations"
)

CONTRACT_FILE = (
    POLICY_ROOT
    / "adaptive-control-contract.json"
)


class RecommendationError(RuntimeError):
    pass


ALLOWED_STATES = {
    "NO_CHANGE",
    "INSUFFICIENT_EVIDENCE",
    "HOLD",
    "REVIEW_INCREASE",
    "REVIEW_DECREASE",
    "REVIEW_ADD",
    "REVIEW_REMOVE",
    "DEFER",
    "BLOCK",
}


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


def load_json(
    path: Path,
) -> dict:
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
        data = load_json(
            path
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
        raise RecommendationError(
            f"No passing {phase} summary "
            f"found in {directory}."
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
        raise RecommendationError(
            f"{label} grants unexpected "
            "authority: "
            + ", ".join(
                bad
            )
        )


def main() -> int:
    if not CONTRACT_FILE.exists():
        raise RecommendationError(
            "Missing 3F.1 control contract."
        )

    contract = load_json(
        CONTRACT_FILE
    )

    if contract.get(
        "status"
    ) != "PASS":
        raise RecommendationError(
            "3F.1 contract is not PASS."
        )

    if contract.get(
        "control_mode"
    ) != "PROPOSAL_ONLY":
        raise RecommendationError(
            "Contract is not PROPOSAL_ONLY."
        )

    if as_int(
        contract.get(
            "recommendation_generation_allowed"
        )
    ) != 1:
        raise RecommendationError(
            "Contract does not allow "
            "recommendation generation."
        )

    require_zero_authority(
        "3F.1 contract",
        contract,
    )

    eligibility_path, eligibility_summary = (
        latest_passing_summary(
            EVIDENCE_ROOT,
            "3F.2",
        )
    )

    require_zero_authority(
        "3F.2 eligibility",
        eligibility_summary,
    )

    if (
        eligibility_summary.get(
            "contract_sha256"
        )
        != sha256(
            CONTRACT_FILE
        )
    ):
        raise RecommendationError(
            "3F.2 eligibility was built "
            "against a different control contract."
        )

    eligibility_file_text = (
        eligibility_summary.get(
            "files",
            {},
        )
        .get(
            "eligibility"
        )
    )

    if not eligibility_file_text:
        raise RecommendationError(
            "3F.2 eligibility CSV missing."
        )

    eligibility_file = Path(
        eligibility_file_text
    )

    if not eligibility_file.exists():
        raise RecommendationError(
            f"Missing eligibility CSV: "
            f"{eligibility_file}"
        )

    eligibility = pd.read_csv(
        eligibility_file,
        low_memory=False,
    )

    if len(
        eligibility
    ) != 28:
        raise RecommendationError(
            "Expected 28 eligibility rows."
        )

    form_evidence_text = (
        eligibility_summary.get(
            "sources",
            {},
        )
        .get(
            "phase_3d5_form_evidence"
        )
    )

    if not form_evidence_text:
        raise RecommendationError(
            "3F.2 did not record the "
            "3D.5 form evidence source."
        )

    form_evidence_file = Path(
        form_evidence_text
    )

    if not form_evidence_file.exists():
        raise RecommendationError(
            "Missing referenced form evidence: "
            f"{form_evidence_file}"
        )

    form_evidence = pd.read_csv(
        form_evidence_file,
        low_memory=False,
    )

    if len(
        form_evidence
    ) != 28:
        raise RecommendationError(
            "Expected 28 form evidence rows."
        )

    evidence_by_form = (
        form_evidence.set_index(
            [
                "itemid",
                "stack",
            ]
        )
    )

    recommendations = []

    for _, row in eligibility.iterrows():
        iid = as_int(
            row[
                "itemid"
            ]
        )

        stack = as_int(
            row[
                "stack"
            ]
        )

        key = (
            iid,
            stack,
        )

        if key not in evidence_by_form.index:
            raise RecommendationError(
                f"Missing form evidence "
                f"for {iid}/{stack}."
            )

        evidence = evidence_by_form.loc[
            key
        ]

        eligibility_state = str(
            row[
                "eligibility_state"
            ]
        )

        eligibility_reason = str(
            row[
                "eligibility_reason"
            ]
        )

        control_scope = str(
            row[
                "control_scope"
            ]
        )

        item_recommendation = str(
            row[
                "item_recommendation"
            ]
        )

        item_reason = str(
            row[
                "item_recommendation_reason"
            ]
        )

        recommendation_eligible = as_int(
            row[
                "recommendation_eligible"
            ]
        )

        proposal_eligible = as_int(
            row[
                "bounded_proposal_eligible"
            ]
        )

        demand_sales = as_int(
            evidence[
                "ahbot_to_player_sales"
            ]
        )

        supply_sales = as_int(
            evidence[
                "player_to_ahbot_sales"
            ]
        )

        organic_trades = as_int(
            evidence[
                "organic_player_trades"
            ]
        )

        player_listing_opportunities = as_int(
            evidence[
                "player_listing_opportunities"
            ]
        )

        eligible_player_opportunities = as_int(
            evidence[
                "eligible_player_opportunities"
            ]
        )

        current_active_player = as_int(
            evidence[
                "current_active_player"
            ]
        )

        seller_price = as_float(
            evidence[
                "seller_price"
            ]
        )

        buyer_bid = as_float(
            evidence[
                "buyer_bid"
            ]
        )

        sell_rate = as_float(
            evidence[
                "sell_rate"
            ]
        )

        buy_rate = as_float(
            evidence[
                "buy_rate"
            ]
        )

        stock_target = as_float(
            evidence[
                "stock_target"
            ]
        )

        recommendation_state = None
        recommendation_domain = "NONE"
        recommendation_direction = "NONE"
        recommendation_reason = ""
        current_value = None

        # ==========================================
        # Fail-closed states first
        # ==========================================

        if eligibility_state == "BLOCKED":
            recommendation_state = "BLOCK"

            recommendation_reason = (
                "STRUCTURAL_SAFETY_BLOCK"
            )

        elif eligibility_state == "REVIEW_HOLD":
            recommendation_state = "HOLD"

            recommendation_reason = (
                "SAFETY_REVIEW_HOLD"
            )

        elif (
            eligibility_state
            == "INSUFFICIENT_EVIDENCE"
        ):
            recommendation_state = (
                "INSUFFICIENT_EVIDENCE"
            )

            recommendation_reason = (
                eligibility_reason
            )

        elif not recommendation_eligible:
            recommendation_state = "HOLD"

            recommendation_reason = (
                "FORM_NOT_RECOMMENDATION_ELIGIBLE"
            )

        else:
            # ======================================
            # Recommendation-eligible future state
            #
            # Item-level evidence does NOT blindly
            # propagate across both forms.
            # ======================================

            if item_recommendation == "KEEP":
                if (
                    demand_sales
                    or supply_sales
                    or organic_trades
                ):
                    recommendation_state = (
                        "NO_CHANGE"
                    )

                    recommendation_reason = (
                        "OBSERVED_FORM_SIGNAL_"
                        "SUPPORTS_CURRENT_POLICY"
                    )

                else:
                    recommendation_state = "HOLD"

                    recommendation_reason = (
                        "ITEM_SIGNAL_NOT_"
                        "FORM_SPECIFIC"
                    )

            elif (
                item_recommendation
                == "REVIEW_PRICE"
            ):
                if control_scope == "SELLER":
                    if (
                        organic_trades > 0
                        and demand_sales == 0
                    ):
                        recommendation_state = (
                            "REVIEW_DECREASE"
                        )

                        recommendation_domain = (
                            "seller_price"
                        )

                        recommendation_direction = (
                            "DECREASE"
                        )

                        current_value = (
                            seller_price
                        )

                        recommendation_reason = (
                            "ORGANIC_PLAYER_ACTIVITY_"
                            "WITHOUT_AHBOT_SELL_THROUGH"
                        )

                    else:
                        recommendation_state = "HOLD"

                        recommendation_reason = (
                            "SELLER_PRICE_DIRECTION_"
                            "NOT_FORM_SUPPORTED"
                        )

                elif control_scope == "BUYER":
                    if (
                        player_listing_opportunities > 0
                        and
                        eligible_player_opportunities == 0
                    ):
                        recommendation_state = (
                            "REVIEW_INCREASE"
                        )

                        recommendation_domain = (
                            "buyer_bid"
                        )

                        recommendation_direction = (
                            "INCREASE"
                        )

                        current_value = (
                            buyer_bid
                        )

                        recommendation_reason = (
                            "PLAYER_SUPPLY_EXISTS_"
                            "ABOVE_CURRENT_BID"
                        )

                    else:
                        recommendation_state = "HOLD"

                        recommendation_reason = (
                            "BUYER_PRICE_DIRECTION_"
                            "NOT_FORM_SUPPORTED"
                        )

                else:
                    recommendation_state = "HOLD"

                    recommendation_reason = (
                        "PRICE_REVIEW_DIRECTION_"
                        "AMBIGUOUS"
                    )

            elif (
                item_recommendation
                == "REVIEW_RATE"
            ):
                if (
                    control_scope == "BUYER"
                    and
                    eligible_player_opportunities > 0
                    and supply_sales == 0
                ):
                    recommendation_state = (
                        "REVIEW_INCREASE"
                    )

                    recommendation_domain = (
                        "buy_rate"
                    )

                    recommendation_direction = (
                        "INCREASE"
                    )

                    current_value = (
                        buy_rate
                    )

                    recommendation_reason = (
                        "ELIGIBLE_PLAYER_SUPPLY_"
                        "PERSISTS_WITHOUT_CAPTURE"
                    )

                elif (
                    control_scope == "SELLER"
                    and demand_sales > 0
                ):
                    recommendation_state = (
                        "REVIEW_INCREASE"
                    )

                    recommendation_domain = (
                        "sell_rate"
                    )

                    recommendation_direction = (
                        "INCREASE"
                    )

                    current_value = (
                        sell_rate
                    )

                    recommendation_reason = (
                        "PLAYER_DEMAND_PRESENT_"
                        "RATE_REVIEW_REQUESTED"
                    )

                else:
                    recommendation_state = "HOLD"

                    recommendation_reason = (
                        "RATE_DIRECTION_NOT_"
                        "FORM_SUPPORTED"
                    )

            elif (
                item_recommendation
                == "REVIEW_STOCK"
            ):
                if control_scope == "SELLER":
                    recommendation_state = (
                        "REVIEW_INCREASE"
                    )

                    recommendation_domain = (
                        "stock_target"
                    )

                    recommendation_direction = (
                        "INCREASE"
                    )

                    current_value = (
                        stock_target
                    )

                    recommendation_reason = (
                        "LONGITUDINAL_STOCK_"
                        "REVIEW_REQUESTED"
                    )

                else:
                    recommendation_state = "HOLD"

                    recommendation_reason = (
                        "STOCK_REVIEW_NOT_"
                        "SELLER_SCOPED"
                    )

            elif item_recommendation == "DEFER":
                recommendation_state = "DEFER"

                recommendation_reason = (
                    item_reason
                )

            elif item_recommendation == "OBSERVE":
                recommendation_state = "HOLD"

                recommendation_reason = (
                    "ITEM_REMAINS_OBSERVATIONAL"
                )

            else:
                recommendation_state = "HOLD"

                recommendation_reason = (
                    "NO_DIRECTIONAL_MAPPING_"
                    "FOR_ITEM_RECOMMENDATION"
                )

        if (
            recommendation_state
            not in ALLOWED_STATES
        ):
            raise RecommendationError(
                f"{iid}/{stack}: invalid "
                f"recommendation state "
                f"{recommendation_state}"
            )

        change_candidate = int(
            recommendation_state
            in {
                "REVIEW_INCREASE",
                "REVIEW_DECREASE",
                "REVIEW_ADD",
                "REVIEW_REMOVE",
            }
            and proposal_eligible
        )

        recommendations.append({
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
                control_scope,

            "eligibility_state":
                eligibility_state,

            "eligibility_reason":
                eligibility_reason,

            "recommendation_eligible":
                recommendation_eligible,

            "bounded_proposal_eligible":
                proposal_eligible,

            "item_recommendation":
                item_recommendation,

            "item_recommendation_reason":
                item_reason,

            "form_player_events":
                as_int(
                    row[
                        "form_player_events"
                    ]
                ),

            "ahbot_to_player_sales":
                demand_sales,

            "player_to_ahbot_sales":
                supply_sales,

            "organic_player_trades":
                organic_trades,

            "current_active_player":
                current_active_player,

            "player_listing_opportunities":
                player_listing_opportunities,

            "eligible_player_opportunities":
                eligible_player_opportunities,

            "seller_price":
                seller_price,

            "buyer_bid":
                buyer_bid,

            "sell_rate":
                sell_rate,

            "buy_rate":
                buy_rate,

            "stock_target":
                stock_target,

            "recommendation_state":
                recommendation_state,

            "recommendation_domain":
                recommendation_domain,

            "recommendation_direction":
                recommendation_direction,

            "current_value":
                current_value,

            "recommendation_reason":
                recommendation_reason,

            "change_proposal_candidate":
                change_candidate,

            "human_approval_required":
                1,

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

    result = pd.DataFrame(
        recommendations
    ).sort_values(
        [
            "pilot_lane",
            "itemid",
            "stack",
        ]
    )

    if len(
        result
    ) != 28:
        raise RecommendationError(
            "Expected 28 recommendation rows."
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
            result[
                column
            ].sum()
        ) != 0:
            raise RecommendationError(
                f"Unexpected authority in "
                f"{column}."
            )

    state_counts = (
        result[
            "recommendation_state"
        ]
        .value_counts()
        .sort_index()
        .to_dict()
    )

    directional_count = as_int(
        result[
            "recommendation_state"
        ]
        .isin(
            [
                "REVIEW_INCREASE",
                "REVIEW_DECREASE",
                "REVIEW_ADD",
                "REVIEW_REMOVE",
            ]
        )
        .sum()
    )

    no_change_count = as_int(
        (
            result[
                "recommendation_state"
            ]
            == "NO_CHANGE"
        ).sum()
    )

    insufficient_count = as_int(
        (
            result[
                "recommendation_state"
            ]
            == "INSUFFICIENT_EVIDENCE"
        ).sum()
    )

    hold_count = as_int(
        (
            result[
                "recommendation_state"
            ]
            == "HOLD"
        ).sum()
    )

    block_count = as_int(
        (
            result[
                "recommendation_state"
            ]
            == "BLOCK"
        ).sum()
    )

    change_candidate_count = as_int(
        result[
            "change_proposal_candidate"
        ].sum()
    )

    if block_count:
        generator_state = (
            "BLOCKED_RECOMMENDATIONS_PRESENT"
        )

    elif directional_count:
        generator_state = (
            "DIRECTIONAL_RECOMMENDATIONS_PRESENT"
        )

    elif no_change_count:
        generator_state = (
            "NO_CHANGE_RECOMMENDATIONS_PRESENT"
        )

    elif insufficient_count == len(
        result
    ):
        generator_state = (
            "ALL_FORMS_INSUFFICIENT_EVIDENCE"
        )

    else:
        generator_state = (
            "OBSERVATIONAL_RECOMMENDATIONS_ONLY"
        )

    now = datetime.now(
        timezone.utc
    )

    stamp = now.strftime(
        "%Y%m%dT%H%M%SZ"
    )

    RECOMMENDATION_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    recommendations_file = (
        RECOMMENDATION_ROOT
        / f"{stamp}-recommendations.csv"
    )

    summary_file = (
        RECOMMENDATION_ROOT
        / f"{stamp}-summary.json"
    )

    result.to_csv(
        recommendations_file,
        index=False,
    )

    summary = {
        "status":
            "PASS",

        "phase":
            "3F.3",

        "observation_utc":
            now.isoformat(),

        "generator_state":
            generator_state,

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
                result[
                    "itemid"
                ].nunique()
            ),

        "pilot_forms":
            len(
                result
            ),

        "recommendation_state_counts": {
            str(k):
                int(v)
            for k, v
            in state_counts.items()
        },

        "directional_recommendations":
            directional_count,

        "no_change_recommendations":
            no_change_count,

        "insufficient_evidence_recommendations":
            insufficient_count,

        "hold_recommendations":
            hold_count,

        "block_recommendations":
            block_count,

        "change_proposal_candidates":
            change_candidate_count,

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

            "eligibility_summary":
                str(
                    eligibility_path
                ),

            "eligibility_csv":
                str(
                    eligibility_file
                ),

            "form_evidence":
                str(
                    form_evidence_file
                ),
        },

        "files": {
            "recommendations":
                str(
                    recommendations_file
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
        " Phase 3F.3 Adaptive "
        "Recommendation Generator"
    )
    print("=" * 112)
    print()

    display_columns = [
        "itemid",
        "name",
        "pilot_lane",
        "form",
        "control_scope",
        "recommendation_eligible",
        "bounded_proposal_eligible",
        "form_player_events",
        "recommendation_state",
        "recommendation_domain",
        "recommendation_direction",
        "current_value",
        "recommendation_reason",
        "change_proposal_candidate",
    ]

    print(
        result[
            display_columns
        ].to_string(
            index=False
        )
    )

    print()
    print(
        "Generator state:                  ",
        generator_state,
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
        "Directional recommendations:      ",
        directional_count,
    )

    print(
        "No-change recommendations:        ",
        no_change_count,
    )

    print(
        "Insufficient-evidence recs:       ",
        insufficient_count,
    )

    print(
        "Hold recommendations:             ",
        hold_count,
    )

    print(
        "Block recommendations:            ",
        block_count,
    )

    print(
        "Change proposal candidates:       ",
        change_candidate_count,
    )

    print()
    print(
        "Human approval required:          1"
    )

    print(
        "Automatic application allowed:    0"
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
        "Recommendations:",
        recommendations_file,
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
