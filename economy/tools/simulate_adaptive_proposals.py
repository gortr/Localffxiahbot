from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ffxiahbot.config import Config


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

PROPOSAL_ROOT = (
    ADAPTIVE_ROOT
    / "proposals"
)

SIMULATION_ROOT = (
    ADAPTIVE_ROOT
    / "simulation"
)

GIL_FLOW_ROOT = (
    OBS_ROOT
    / "gil-flow"
)

FAUCET_ROOT = (
    GIL_FLOW_ROOT
    / "faucet"
)

SINK_ROOT = (
    GIL_FLOW_ROOT
    / "sink"
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

CONFIG_FILE = (
    ROOT
    / "bin"
    / "config.yaml"
)

SELLER_RUNTIME = (
    GENERATED
    / "market-sell.csv"
)

BUYER_RUNTIME = (
    GENERATED
    / "market-buy.csv"
)


class SimulationError(RuntimeError):
    pass


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
        raise SimulationError(
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
                data.get(key)
            )
            != 0
        ):
            bad.append(key)

    if bad:
        raise SimulationError(
            f"{label} grants unexpected "
            "authority: "
            + ", ".join(bad)
        )


def concentration(
    values: pd.Series,
) -> dict:
    values = (
        values
        .fillna(0)
        .astype(float)
        .clip(lower=0)
    )

    total = float(
        values.sum()
    )

    if total <= 0:
        return {
            "largest_share":
                0.0,

            "top5_share":
                0.0,

            "hhi":
                0.0,
        }

    shares = (
        values
        / total
    ).sort_values(
        ascending=False
    )

    return {
        "largest_share":
            round(
                float(
                    shares.iloc[0]
                ),
                6,
            ),

        "top5_share":
            round(
                float(
                    shares.iloc[:5].sum()
                ),
                6,
            ),

        "hhi":
            round(
                float(
                    (
                        shares
                        * shares
                    ).sum()
                ),
                6,
            ),
    }


def classify_band(
    value: float,
    watch: float,
    review: float,
) -> str:
    if value > review:
        return "REVIEW"

    if value > watch:
        return "WATCH"

    return "NORMAL"


def runtime_field(
    domain: str,
    stack: int,
) -> tuple[str, str]:
    suffix = (
        "stacks"
        if stack
        else "single"
    )

    mapping = {
        "seller_price":
            (
                "seller",
                f"price_{suffix}",
            ),

        "buyer_bid":
            (
                "buyer",
                f"price_{suffix}",
            ),

        "sell_rate":
            (
                "seller",
                f"sell_rate_{suffix}",
            ),

        "buy_rate":
            (
                "buyer",
                f"buy_rate_{suffix}",
            ),

        "stock_target":
            (
                "seller",
                f"stock_{suffix}",
            ),
    }

    if domain not in mapping:
        raise SimulationError(
            f"Unsupported simulation domain: "
            f"{domain}"
        )

    return mapping[domain]


def current_form_value(
    frame: pd.DataFrame,
    itemid: int,
    column: str,
):
    rows = frame[
        frame["itemid"].astype(int)
        == itemid
    ]

    if len(rows) != 1:
        raise SimulationError(
            f"{itemid}: expected exactly one "
            f"runtime row."
        )

    return as_float(
        rows.iloc[0][
            column
        ]
    )


def set_form_value(
    frame: pd.DataFrame,
    itemid: int,
    column: str,
    value: float,
) -> None:
    mask = (
        frame["itemid"].astype(int)
        == itemid
    )

    if as_int(
        mask.sum()
    ) != 1:
        raise SimulationError(
            f"{itemid}: expected one runtime "
            f"row while applying simulation."
        )

    frame.loc[
        mask,
        column,
    ] = value


def compute_economy_metrics(
    seller: pd.DataFrame,
    buyer: pd.DataFrame,
    *,
    tick_seconds: int,
    restock_seconds: int,
    thresholds: dict,
) -> dict:
    if tick_seconds <= 0:
        raise SimulationError(
            "Invalid tick."
        )

    if restock_seconds <= 0:
        raise SimulationError(
            "Invalid restock interval."
        )

    cycles_per_day = (
        86400.0
        / tick_seconds
    )

    restocks_per_day = (
        86400.0
        / restock_seconds
    )

    buyer_item_exposure: dict[int, float] = {}

    expected_faucet = 0.0

    for _, row in buyer.iterrows():
        iid = as_int(
            row["itemid"]
        )

        item_total = 0.0

        for stack in [0, 1]:
            enabled = as_int(
                row[
                    "buy_stacks"
                    if stack
                    else "buy_single"
                ]
            )

            if not enabled:
                continue

            bid = as_float(
                row[
                    "price_stacks"
                    if stack
                    else "price_single"
                ]
            ) or 0.0

            rate = as_float(
                row[
                    "buy_rate_stacks"
                    if stack
                    else "buy_rate_single"
                ]
            ) or 0.0

            exposure = (
                cycles_per_day
                * rate
                * bid
            )

            item_total += exposure
            expected_faucet += exposure

        buyer_item_exposure[
            iid
        ] = item_total

    seller_target_value = 0.0

    for _, row in seller.iterrows():
        for stack in [0, 1]:
            enabled = as_int(
                row[
                    "sell_stacks"
                    if stack
                    else "sell_single"
                ]
            )

            if not enabled:
                continue

            price = as_float(
                row[
                    "price_stacks"
                    if stack
                    else "price_single"
                ]
            ) or 0.0

            stock = as_float(
                row[
                    "stock_stacks"
                    if stack
                    else "stock_single"
                ]
            ) or 0.0

            seller_target_value += (
                price
                * stock
            )

    notional_turnover = (
        seller_target_value
        * restocks_per_day
    )

    if notional_turnover > 0:
        stress_ratio = (
            expected_faucet
            / notional_turnover
        )
    else:
        stress_ratio = math.inf

    exposure_series = pd.Series(
        buyer_item_exposure,
        dtype=float,
    )

    conc = concentration(
        exposure_series
    )

    direct_review = 0
    direct_block = 0

    spread_rows = []

    seller_index = (
        seller.set_index(
            "itemid"
        )
    )

    buyer_index = (
        buyer.set_index(
            "itemid"
        )
    )

    common_ids = sorted(
        set(
            seller_index.index.astype(int)
        )
        & set(
            buyer_index.index.astype(int)
        )
    )

    spread_review_threshold = as_float(
        thresholds.get(
            "direct_spread_review"
        )
    )

    spread_block_threshold = as_float(
        thresholds.get(
            "direct_spread_block"
        )
    )

    if spread_review_threshold is None:
        raise SimulationError(
            "Missing direct spread review "
            "threshold."
        )

    if spread_block_threshold is None:
        raise SimulationError(
            "Missing direct spread block "
            "threshold."
        )

    for iid in common_ids:
        sr = seller_index.loc[
            iid
        ]

        br = buyer_index.loc[
            iid
        ]

        for stack in [0, 1]:
            seller_enabled = as_int(
                sr[
                    "sell_stacks"
                    if stack
                    else "sell_single"
                ]
            )

            buyer_enabled = as_int(
                br[
                    "buy_stacks"
                    if stack
                    else "buy_single"
                ]
            )

            if not (
                seller_enabled
                and buyer_enabled
            ):
                continue

            seller_price = as_float(
                sr[
                    "price_stacks"
                    if stack
                    else "price_single"
                ]
            )

            buyer_bid = as_float(
                br[
                    "price_stacks"
                    if stack
                    else "price_single"
                ]
            )

            if (
                seller_price is None
                or seller_price <= 0
                or buyer_bid is None
            ):
                continue

            ratio = (
                buyer_bid
                / seller_price
            )

            if (
                ratio
                >= spread_block_threshold
            ):
                state = "BLOCK"
                direct_block += 1

            elif (
                ratio
                >= spread_review_threshold
            ):
                state = "REVIEW"
                direct_review += 1

            else:
                state = "NORMAL"

            spread_rows.append({
                "itemid":
                    iid,

                "stack":
                    stack,

                "buyer_bid":
                    buyer_bid,

                "seller_price":
                    seller_price,

                "ratio":
                    ratio,

                "state":
                    state,
            })

    stress_watch = as_float(
        thresholds.get(
            "global_stress_watch"
        )
    )

    stress_review = as_float(
        thresholds.get(
            "global_stress_review"
        )
    )

    item_watch = as_float(
        thresholds.get(
            "item_share_watch"
        )
    )

    item_review = as_float(
        thresholds.get(
            "item_share_review"
        )
    )

    top5_watch = as_float(
        thresholds.get(
            "top5_share_watch"
        )
    )

    top5_review = as_float(
        thresholds.get(
            "top5_share_review"
        )
    )

    hhi_watch = as_float(
        thresholds.get(
            "hhi_watch"
        )
    )

    hhi_review = as_float(
        thresholds.get(
            "hhi_review"
        )
    )

    required = [
        stress_watch,
        stress_review,
        item_watch,
        item_review,
        top5_watch,
        top5_review,
        hhi_watch,
        hhi_review,
    ]

    if any(
        value is None
        for value in required
    ):
        raise SimulationError(
            "One or more 3E.5 policy "
            "thresholds are missing."
        )

    return {
        "expected_faucet_per_day":
            round(
                expected_faucet,
                6,
            ),

        "seller_target_value":
            round(
                seller_target_value,
                6,
            ),

        "notional_turnover_reference_per_day":
            round(
                notional_turnover,
                6,
            ),

        "global_stress_ratio":
            round(
                stress_ratio,
                6,
            ),

        "global_stress_state":
            classify_band(
                stress_ratio,
                stress_watch,
                stress_review,
            ),

        "largest_buyer_item_share":
            conc[
                "largest_share"
            ],

        "largest_item_state":
            classify_band(
                conc[
                    "largest_share"
                ],
                item_watch,
                item_review,
            ),

        "buyer_top5_share":
            conc[
                "top5_share"
            ],

        "top5_state":
            classify_band(
                conc[
                    "top5_share"
                ],
                top5_watch,
                top5_review,
            ),

        "buyer_hhi":
            conc[
                "hhi"
            ],

        "hhi_state":
            classify_band(
                conc[
                    "hhi"
                ],
                hhi_watch,
                hhi_review,
            ),

        "direct_spread_review_forms":
            direct_review,

        "direct_spread_block_forms":
            direct_block,

        "spread_rows":
            spread_rows,
    }


def latest_event_file(
    directory: Path,
    phase: str,
    key: str,
) -> pd.DataFrame:
    _, summary = latest_passing_summary(
        directory,
        phase,
    )

    file_text = (
        summary.get(
            "files",
            {},
        )
        .get(
            key
        )
    )

    if not file_text:
        return pd.DataFrame()

    path = Path(
        file_text
    )

    if not path.exists():
        raise SimulationError(
            f"Missing referenced event file: "
            f"{path}"
        )

    return pd.read_csv(
        path,
        low_memory=False,
    )


def main() -> int:
    required = [
        CONTRACT_FILE,
        SAFETY_FILE,
        CONFIG_FILE,
        SELLER_RUNTIME,
        BUYER_RUNTIME,
    ]

    for path in required:
        if not path.exists():
            raise SimulationError(
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
        raise SimulationError(
            "3F.1 contract is not PASS."
        )

    if contract.get(
        "control_mode"
    ) != "PROPOSAL_ONLY":
        raise SimulationError(
            "Contract is not PROPOSAL_ONLY."
        )

    if as_int(
        contract.get(
            "simulation_allowed"
        )
    ) != 1:
        raise SimulationError(
            "Contract does not allow simulation."
        )

    if safety.get(
        "status"
    ) != "PASS":
        raise SimulationError(
            "3E.5 safety policy is not PASS."
        )

    require_zero_authority(
        "3F.1 contract",
        contract,
    )

    require_zero_authority(
        "3E.5 safety",
        safety,
    )

    proposal_summary_path, proposal_summary = (
        latest_passing_summary(
            PROPOSAL_ROOT,
            "3F.4",
        )
    )

    require_zero_authority(
        "3F.4 proposals",
        proposal_summary,
    )

    if (
        proposal_summary.get(
            "contract_sha256"
        )
        != sha256(
            CONTRACT_FILE
        )
    ):
        raise SimulationError(
            "3F.4 proposals were built "
            "against a different contract."
        )

    proposals_path_text = (
        proposal_summary.get(
            "files",
            {},
        )
        .get(
            "proposals"
        )
    )

    if not proposals_path_text:
        raise SimulationError(
            "3F.4 proposal CSV missing."
        )

    proposals_path = Path(
        proposals_path_text
    )

    if not proposals_path.exists():
        raise SimulationError(
            f"Missing proposal CSV: "
            f"{proposals_path}"
        )

    proposals = pd.read_csv(
        proposals_path,
        low_memory=False,
    )

    if len(proposals) != 28:
        raise SimulationError(
            "Expected 28 proposal rows."
        )

    seller_sha_before = sha256(
        SELLER_RUNTIME
    )

    buyer_sha_before = sha256(
        BUYER_RUNTIME
    )

    seller_live = pd.read_csv(
        SELLER_RUNTIME,
        low_memory=False,
    )

    buyer_live = pd.read_csv(
        BUYER_RUNTIME,
        low_memory=False,
    )

    seller_sim = seller_live.copy(
        deep=True
    )

    buyer_sim = buyer_live.copy(
        deep=True
    )

    config = Config.from_yaml(
        CONFIG_FILE
    )

    tick_seconds = as_int(
        config.tick
    )

    restock_seconds = as_int(
        config.restock
    )

    thresholds = safety.get(
        "policy_thresholds",
        {},
    )

    baseline_metrics = (
        compute_economy_metrics(
            seller_live,
            buyer_live,
            tick_seconds=tick_seconds,
            restock_seconds=restock_seconds,
            thresholds=thresholds,
        )
    )

    faucet_events = latest_event_file(
        FAUCET_ROOT,
        "3E.2",
        "events",
    )

    sink_events = latest_event_file(
        SINK_ROOT,
        "3E.3",
        "events",
    )

    simulation_rows = []

    stale_proposals = 0
    unresolved_external = 0
    simulated_changes = 0

    ready_rows = proposals[
        proposals[
            "proposal_state"
        ]
        == "READY_FOR_SIMULATION"
    ]

    for _, row in proposals.iterrows():
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

        domain = str(
            row[
                "recommendation_domain"
            ]
        )

        proposal_state = str(
            row[
                "proposal_state"
            ]
        )

        prerequisite = str(
            row[
                "safety_prerequisite"
            ]
        )

        proposed_value = as_float(
            row[
                "proposed_value"
            ]
        )

        expected_current = as_float(
            row[
                "current_value"
            ]
        )

        simulation_state = (
            "SKIPPED_NO_PROPOSAL"
        )

        simulation_reason = (
            "NO_SIMULATION_READY_CHANGE"
        )

        live_value = None
        current_matches = None
        prerequisite_satisfied = 1

        replay_supported = 0
        historical_events = 0
        historical_events_compatible = 0

        if proposal_state == "BLOCK":
            simulation_state = (
                "BLOCKED_INPUT_PROPOSAL"
            )

            simulation_reason = (
                "INPUT_PROPOSAL_IS_BLOCKED"
            )

        elif (
            proposal_state
            != "READY_FOR_SIMULATION"
        ):
            simulation_state = (
                "SKIPPED_NO_PROPOSAL"
            )

            simulation_reason = (
                proposal_state
            )

        else:
            target, column = runtime_field(
                domain,
                stack,
            )

            frame = (
                seller_sim
                if target == "seller"
                else buyer_sim
            )

            live_frame = (
                seller_live
                if target == "seller"
                else buyer_live
            )

            live_value = current_form_value(
                live_frame,
                iid,
                column,
            )

            if (
                expected_current is None
                or live_value is None
            ):
                current_matches = 0

            else:
                current_matches = int(
                    math.isclose(
                        live_value,
                        expected_current,
                        rel_tol=1e-9,
                        abs_tol=1e-9,
                    )
                )

            if not current_matches:
                stale_proposals += 1

                simulation_state = (
                    "STALE_PROPOSAL"
                )

                simulation_reason = (
                    "LIVE_VALUE_CHANGED_SINCE_"
                    "PROPOSAL_GENERATION"
                )

            elif proposed_value is None:
                simulation_state = (
                    "INVALID_PROPOSAL_VALUE"
                )

                simulation_reason = (
                    "PROPOSED_VALUE_MISSING"
                )

            else:
                if (
                    prerequisite
                    == "NPC_RELEVANCE_RECHECK_"
                    "REQUIRED_IN_SIMULATION"
                ):
                    prerequisite_satisfied = 0

                    unresolved_external += 1

                set_form_value(
                    frame,
                    iid,
                    column,
                    proposed_value,
                )

                simulated_changes += 1

                if prerequisite_satisfied:
                    simulation_state = (
                        "SIMULATED"
                    )

                    simulation_reason = (
                        "IN_MEMORY_CHANGE_APPLIED"
                    )

                else:
                    simulation_state = (
                        "SIMULATED_WITH_UNRESOLVED_"
                        "EXTERNAL_PREREQUISITE"
                    )

                    simulation_reason = (
                        "NPC_POLICY_REQUIRES_"
                        "EXTERNAL_REVALIDATION"
                    )

                # ----------------------------------
                # Limited transaction compatibility
                # replay.
                #
                # This does not predict demand.
                # ----------------------------------

                if domain == "buyer_bid":
                    replay_supported = 1

                    if len(
                        faucet_events
                    ):
                        subset = faucet_events[
                            (
                                faucet_events[
                                    "itemid"
                                ].astype(int)
                                == iid
                            )
                            & (
                                faucet_events[
                                    "stack"
                                ].astype(int)
                                == stack
                            )
                        ]

                        historical_events = len(
                            subset
                        )

                        if historical_events:
                            historical_events_compatible = int(
                                (
                                    subset[
                                        "ask_price"
                                    ].astype(float)
                                    <= proposed_value
                                ).sum()
                            )

                elif domain == "seller_price":
                    replay_supported = 1

                    if len(
                        sink_events
                    ):
                        subset = sink_events[
                            (
                                sink_events[
                                    "itemid"
                                ].astype(int)
                                == iid
                            )
                            & (
                                sink_events[
                                    "stack"
                                ].astype(int)
                                == stack
                            )
                        ]

                        historical_events = len(
                            subset
                        )

                        if historical_events:
                            historical_events_compatible = int(
                                (
                                    subset[
                                        "paid_price"
                                    ].astype(float)
                                    >= proposed_value
                                ).sum()
                            )

        simulation_rows.append({
            "proposal_id":
                str(
                    row[
                        "proposal_id"
                    ]
                ),

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

            "domain":
                domain,

            "direction":
                str(
                    row[
                        "recommendation_direction"
                    ]
                ),

            "proposal_state":
                proposal_state,

            "expected_current_value":
                expected_current,

            "live_current_value":
                live_value,

            "current_value_matches":
                current_matches,

            "proposed_value":
                proposed_value,

            "safety_prerequisite":
                prerequisite,

            "prerequisite_satisfied":
                prerequisite_satisfied,

            "simulation_state":
                simulation_state,

            "simulation_reason":
                simulation_reason,

            "historical_replay_supported":
                replay_supported,

            "historical_events_checked":
                historical_events,

            "historical_events_compatible":
                historical_events_compatible,

            "runtime_mutation_performed":
                0,

            "database_mutation_performed":
                0,

            "human_approval_required":
                1,

            "automatic_application_allowed":
                0,
        })

    simulation = pd.DataFrame(
        simulation_rows
    )

    simulated_metrics = (
        compute_economy_metrics(
            seller_sim,
            buyer_sim,
            tick_seconds=tick_seconds,
            restock_seconds=restock_seconds,
            thresholds=thresholds,
        )
    )

    # ----------------------------------------------
    # Batch safety evaluation
    # ----------------------------------------------

    review_conditions = []

    block_conditions = []

    if (
        simulated_metrics[
            "direct_spread_block_forms"
        ]
        > 0
    ):
        block_conditions.append(
            "DIRECT_BUYER_SELLER_ARBITRAGE"
        )

    if (
        simulated_metrics[
            "direct_spread_review_forms"
        ]
        > 0
    ):
        review_conditions.append(
            "DIRECT_SPREAD_REVIEW"
        )

    for key, label in [
        (
            "global_stress_state",
            "GLOBAL_STRESS",
        ),
        (
            "largest_item_state",
            "LARGEST_BUYER_ITEM_SHARE",
        ),
        (
            "top5_state",
            "BUYER_TOP5_SHARE",
        ),
        (
            "hhi_state",
            "BUYER_HHI",
        ),
    ]:
        state = simulated_metrics[
            key
        ]

        if state == "REVIEW":
            review_conditions.append(
                label
            )

    # Existing WATCH is allowed. A WATCH does not
    # become approval or a veto by itself.

    if stale_proposals:
        block_conditions.append(
            "STALE_PROPOSALS"
        )

    simulation_ready_count = len(
        ready_rows
    )

    if simulation_ready_count == 0:
        simulation_set_state = (
            "EMPTY_PROPOSAL_SET_VALID"
        )

        safe_for_human_review = 0

    elif block_conditions:
        simulation_set_state = (
            "SIMULATION_BLOCKED"
        )

        safe_for_human_review = 0

    elif unresolved_external:
        simulation_set_state = (
            "SIMULATION_DEFERRED_"
            "EXTERNAL_PREREQUISITE"
        )

        safe_for_human_review = 0

    elif review_conditions:
        simulation_set_state = (
            "SIMULATION_REVIEW_REQUIRED"
        )

        safe_for_human_review = 0

    else:
        simulation_set_state = (
            "SAFE_FOR_HUMAN_REVIEW"
        )

        safe_for_human_review = 1

    seller_sha_after = sha256(
        SELLER_RUNTIME
    )

    buyer_sha_after = sha256(
        BUYER_RUNTIME
    )

    if (
        seller_sha_before
        != seller_sha_after
    ):
        raise SimulationError(
            "Seller runtime changed during "
            "dry-run simulation."
        )

    if (
        buyer_sha_before
        != buyer_sha_after
    ):
        raise SimulationError(
            "Buyer runtime changed during "
            "dry-run simulation."
        )

    metric_rows = []

    for key in [
        "expected_faucet_per_day",
        "seller_target_value",
        "notional_turnover_reference_per_day",
        "global_stress_ratio",
        "largest_buyer_item_share",
        "buyer_top5_share",
        "buyer_hhi",
        "direct_spread_review_forms",
        "direct_spread_block_forms",
    ]:
        before = baseline_metrics[
            key
        ]

        after = simulated_metrics[
            key
        ]

        if (
            isinstance(
                before,
                (int, float),
            )
            and isinstance(
                after,
                (int, float),
            )
        ):
            delta = (
                after
                - before
            )
        else:
            delta = None

        metric_rows.append({
            "metric":
                key,

            "before":
                before,

            "after":
                after,

            "delta":
                delta,
        })

    metrics_df = pd.DataFrame(
        metric_rows
    )

    now = datetime.now(
        timezone.utc
    )

    stamp = now.strftime(
        "%Y%m%dT%H%M%SZ"
    )

    SIMULATION_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    simulation_file = (
        SIMULATION_ROOT
        / f"{stamp}-proposal-simulation.csv"
    )

    metrics_file = (
        SIMULATION_ROOT
        / f"{stamp}-metric-deltas.csv"
    )

    summary_file = (
        SIMULATION_ROOT
        / f"{stamp}-summary.json"
    )

    simulation.to_csv(
        simulation_file,
        index=False,
    )

    metrics_df.to_csv(
        metrics_file,
        index=False,
    )

    summary = {
        "status":
            "PASS",

        "phase":
            "3F.5",

        "observation_utc":
            now.isoformat(),

        "simulation_set_state":
            simulation_set_state,

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

        "proposal_summary":
            str(
                proposal_summary_path
            ),

        "proposal_sha256":
            sha256(
                proposals_path
            ),

        "proposal_rows":
            len(
                proposals
            ),

        "simulation_ready_proposals":
            simulation_ready_count,

        "simulated_changes":
            simulated_changes,

        "stale_proposals":
            stale_proposals,

        "unresolved_external_prerequisites":
            unresolved_external,

        "safe_for_human_review":
            safe_for_human_review,

        "review_conditions":
            review_conditions,

        "block_conditions":
            block_conditions,

        "baseline_metrics":
            {
                k:
                    v
                for k, v
                in baseline_metrics.items()
                if k != "spread_rows"
            },

        "simulated_metrics":
            {
                k:
                    v
                for k, v
                in simulated_metrics.items()
                if k != "spread_rows"
            },

        "backtest_contract": {
            "scope":
                "ACTUAL_TRANSACTION_COMPATIBILITY_ONLY",

            "predicts_demand":
                False,

            "predicts_new_supply":
                False,

            "replays_rng":
                False,

            "buyer_bid_replay":
                (
                    "checks whether previously "
                    "observed purchased asks remain "
                    "eligible under proposed bid"
                ),

            "seller_price_replay":
                (
                    "checks whether previously "
                    "observed buyer payments would "
                    "still satisfy proposed ask"
                ),
        },

        "interpretation_contract": {
            "simulation_is_live_change":
                False,

            "simulation_is_approval":
                False,

            "safe_for_human_review_is_approval":
                False,

            "human_approval_required":
                True,

            "automatic_application_allowed":
                False,
        },

        "seller_runtime_sha256_before":
            seller_sha_before,

        "seller_runtime_sha256_after":
            seller_sha_after,

        "buyer_runtime_sha256_before":
            buyer_sha_before,

        "buyer_runtime_sha256_after":
            buyer_sha_after,

        "runtime_mutation_performed":
            0,

        "database_mutation_performed":
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

        "files": {
            "proposal_simulation":
                str(
                    simulation_file
                ),

            "metric_deltas":
                str(
                    metrics_file
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
        " Phase 3F.5 Dry-Run Simulation "
        "/ Backtesting"
    )
    print("=" * 112)
    print()

    print(
        "Proposal rows:                    ",
        len(
            proposals
        ),
    )

    print(
        "Simulation-ready proposals:       ",
        simulation_ready_count,
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
        "Unresolved external prerequisites:",
        unresolved_external,
    )

    print()
    print("BASELINE VS SIMULATED")
    print()

    print(
        metrics_df.to_string(
            index=False
        )
    )

    print()
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

    print(
        "Safe for human review:            ",
        safe_for_human_review,
    )

    print()
    print(
        "Simulation state:",
        simulation_set_state,
    )

    print()
    print(
        "Seller runtime unchanged:         ",
        int(
            seller_sha_before
            == seller_sha_after
        ),
    )

    print(
        "Buyer runtime unchanged:          ",
        int(
            buyer_sha_before
            == buyer_sha_after
        ),
    )

    print()
    print(
        "Runtime mutation performed:       0"
    )

    print(
        "Database mutation performed:      0"
    )

    print(
        "Human approval still required:    1"
    )

    print(
        "Automatic application allowed:    0"
    )

    print()
    print(
        "Simulation:",
        simulation_file,
    )

    print(
        "Metric deltas:",
        metrics_file,
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
