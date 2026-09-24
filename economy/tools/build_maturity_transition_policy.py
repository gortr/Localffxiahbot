from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
REPORTS = ROOT / "economy" / "reports"

RUNTIME_STATE_DIR = (
    ROOT
    / "economy"
    / "runtime-observations"
    / "maturity"
)

MATRIX_CSV = (
    REPORTS
    / "market-maturity-matrix.csv"
)

MATRIX_SUMMARY = (
    REPORTS
    / "market-maturity-summary.json"
)

PRICE_GATES_CSV = (
    REPORTS
    / "price-readiness-gates.csv"
)

PRICE_GATES_SUMMARY = (
    REPORTS
    / "price-readiness-gates-summary.json"
)

STOCK_GATES_CSV = (
    REPORTS
    / "stock-readiness-gates.csv"
)

STOCK_GATES_SUMMARY = (
    REPORTS
    / "stock-readiness-gates-summary.json"
)

STATE_CSV = (
    RUNTIME_STATE_DIR
    / "maturity-state.csv"
)

OUTPUT_CSV = (
    REPORTS
    / "maturity-transition-policy.csv"
)

SUMMARY_JSON = (
    REPORTS
    / "maturity-transition-summary.json"
)


STAGES = (
    "SEED",
    "OBSERVING",
    "DEVELOPING",
    "ESTABLISHED",
    "TRUSTED_OBSERVATION",
)

STAGE_RANK = {
    stage: index
    for index, stage in enumerate(STAGES)
}

PROMOTION_HOURS = {
    "OBSERVING": 6,
    "DEVELOPING": 24,
    "ESTABLISHED": 72,
    "TRUSTED_OBSERVATION": 168,
}


class MaturityTransitionError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise MaturityTransitionError(
            f"Missing required summary: {path}"
        )

    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


def require_pass(
    name: str,
    summary: dict[str, Any],
) -> None:
    if summary.get("status") != "PASS":
        raise MaturityTransitionError(
            f"{name} is not PASS."
        )

    if summary.get(
        "integrity_failures",
        0,
    ) != 0:
        raise MaturityTransitionError(
            f"{name} contains integrity failures."
        )


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass

    value = str(value).strip()

    if value.lower() in {
        "",
        "none",
        "nan",
        "null",
    }:
        return ""

    return value


def as_int(value: Any) -> int:
    if value is None:
        return 0

    try:
        if pd.isna(value):
            return 0
    except TypeError:
        pass

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def as_float(value: Any) -> float:
    if value is None:
        return 0.0

    try:
        if pd.isna(value):
            return 0.0
    except TypeError:
        pass

    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_timestamp(
    value: Any,
) -> datetime | None:
    text = clean_text(value)

    if not text:
        return None

    try:
        timestamp = pd.Timestamp(text)

        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(
                "UTC"
            )

        return timestamp.to_pydatetime()

    except Exception as exc:
        raise MaturityTransitionError(
            f"Invalid timestamp: {text!r}"
        ) from exc


def next_stage(
    current_stage: str,
) -> str | None:
    rank = STAGE_RANK[current_stage]

    if rank >= len(STAGES) - 1:
        return None

    return STAGES[
        rank + 1
    ]


def promotion_prerequisites(
    target_stage: str,
    row: pd.Series,
) -> tuple[bool, str]:
    supply_readiness = clean_text(
        row.get(
            "supply_readiness"
        )
    )

    matrix_stage = clean_text(
        row.get(
            "market_maturity"
        )
    )

    price_review = as_int(
        row.get(
            "price_signal_review_eligible"
        )
    )

    stock_review = as_int(
        row.get(
            "stock_target_signal_review_eligible"
        )
    )

    if target_stage == "OBSERVING":
        eligible = (
            supply_readiness
            in {
                "BASELINE_READY",
                "PERSISTENCE_READY",
            }
            and STAGE_RANK[
                matrix_stage
            ]
            >= STAGE_RANK[
                "OBSERVING"
            ]
        )

        return (
            eligible,
            "Requires baseline-ready supply telemetry.",
        )

    if target_stage == "DEVELOPING":
        eligible = (
            supply_readiness
            == "PERSISTENCE_READY"
            and STAGE_RANK[
                matrix_stage
            ]
            >= STAGE_RANK[
                "DEVELOPING"
            ]
        )

        return (
            eligible,
            (
                "Requires persistence-ready supply "
                "telemetry and DEVELOPING market "
                "maturity."
            ),
        )

    if target_stage == "ESTABLISHED":
        eligible = (
            supply_readiness
            == "PERSISTENCE_READY"
            and STAGE_RANK[
                matrix_stage
            ]
            >= STAGE_RANK[
                "ESTABLISHED"
            ]
            and stock_review == 1
        )

        return (
            eligible,
            (
                "Requires ESTABLISHED maturity plus "
                "the stock-readiness review gate."
            ),
        )

    if target_stage == "TRUSTED_OBSERVATION":
        eligible = (
            supply_readiness
            == "PERSISTENCE_READY"
            and matrix_stage
            == "TRUSTED_OBSERVATION"
            and price_review == 1
            and stock_review == 1
        )

        return (
            eligible,
            (
                "Requires TRUSTED_OBSERVATION plus "
                "both price and stock review gates."
            ),
        )

    return (
        False,
        "No promotion exists beyond this stage.",
    )


def main() -> int:
    matrix_summary = load_json(
        MATRIX_SUMMARY
    )

    price_summary = load_json(
        PRICE_GATES_SUMMARY
    )

    stock_summary = load_json(
        STOCK_GATES_SUMMARY
    )

    require_pass(
        "2C.1 maturity matrix",
        matrix_summary,
    )

    require_pass(
        "2C.2 price gates",
        price_summary,
    )

    require_pass(
        "2C.3 stock gates",
        stock_summary,
    )

    authority_fields = (
        "price_change_authorized",
        "stock_change_authorized",
        "active_listing_change_authorized",
        "activation_ready",
        "auto_live_promotions",
    )

    for summary_name, summary in (
        ("matrix", matrix_summary),
        ("price", price_summary),
        ("stock", stock_summary),
    ):
        for field in authority_fields:
            if summary.get(
                field,
                0,
            ) != 0:
                raise MaturityTransitionError(
                    f"{summary_name} unsafe state: "
                    f"{field}="
                    f"{summary.get(field)}"
                )

    for path in (
        MATRIX_CSV,
        PRICE_GATES_CSV,
        STOCK_GATES_CSV,
    ):
        if not path.exists():
            raise MaturityTransitionError(
                f"Missing input: {path}"
            )

    matrix = pd.read_csv(
        MATRIX_CSV
    )

    price = pd.read_csv(
        PRICE_GATES_CSV
    )

    stock = pd.read_csv(
        STOCK_GATES_CSV
    )

    expected_forms = int(
        matrix_summary[
            "configured_item_forms"
        ]
    )

    if len(matrix) != expected_forms:
        raise MaturityTransitionError(
            "Maturity matrix count mismatch."
        )

    if len(price) != expected_forms:
        raise MaturityTransitionError(
            "Price-gate count mismatch."
        )

    if len(stock) != expected_forms:
        raise MaturityTransitionError(
            "Stock-gate count mismatch."
        )

    price_view = price[
        [
            "itemid",
            "stack",
            "price_signal_review_eligible",
        ]
    ].copy()

    stock_view = stock[
        [
            "itemid",
            "stack",
            "stock_target_signal_review_eligible",
            "active_listing_review_eligible",
        ]
    ].copy()

    current = matrix.merge(
        price_view,
        on=[
            "itemid",
            "stack",
        ],
        how="left",
        validate="one_to_one",
    )

    current = current.merge(
        stock_view,
        on=[
            "itemid",
            "stack",
        ],
        how="left",
        validate="one_to_one",
    )

    if (
        current[
            "price_signal_review_eligible"
        ].isna().any()
        or current[
            "stock_target_signal_review_eligible"
        ].isna().any()
    ):
        raise MaturityTransitionError(
            "Readiness-gate join is incomplete."
        )

    invalid_matrix_stages = sorted(
        set(
            current[
                "market_maturity"
            ].astype(str)
        )
        - set(STAGES)
    )

    if invalid_matrix_stages:
        raise MaturityTransitionError(
            "Unexpected matrix stages: "
            + ", ".join(
                invalid_matrix_stages
            )
        )

    RUNTIME_STATE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    now = datetime.now(
        timezone.utc
    )

    now_text = now.isoformat()

    if STATE_CSV.exists():
        state = pd.read_csv(
            STATE_CSV
        )

        duplicate_state = int(
            state.duplicated(
                subset=[
                    "itemid",
                    "stack",
                ]
            ).sum()
        )

        if duplicate_state:
            raise MaturityTransitionError(
                "Maturity state ledger contains "
                f"{duplicate_state} duplicate forms."
            )

    else:
        state = pd.DataFrame(
            columns=[
                "itemid",
                "stack",
                "name",
                "recorded_stage",
                "stage_entered_at_utc",
                "qualification_target_stage",
                "qualification_started_at_utc",
                "last_evaluated_at_utc",
                "last_matrix_stage",
                "transition_count",
            ]
        )

    state_lookup = {
        (
            as_int(row["itemid"]),
            as_int(row["stack"]),
        ): row
        for _, row in state.iterrows()
    }

    output_rows: list[
        dict[str, Any]
    ] = []

    state_rows: list[
        dict[str, Any]
    ] = []

    for _, row in current.iterrows():
        itemid = as_int(
            row.get("itemid")
        )

        stack = as_int(
            row.get("stack")
        )

        name = clean_text(
            row.get("name")
        )

        matrix_stage = clean_text(
            row.get(
                "market_maturity"
            )
        )

        key = (
            itemid,
            stack,
        )

        previous = state_lookup.get(
            key
        )

        if previous is None:
            recorded_stage = "SEED"
            stage_entered_at = now
            qualification_target = ""
            qualification_started = None
            transition_count = 0

            if matrix_stage == "SEED":
                action = "INITIALIZE_SEED"
                action_reason = (
                    "New maturity ledger entry "
                    "initialized at SEED."
                )

            else:
                target = next_stage(
                    recorded_stage
                )

                eligible, reason = (
                    promotion_prerequisites(
                        target,
                        row,
                    )
                    if target
                    else (
                        False,
                        "No promotion available.",
                    )
                )

                if eligible:
                    qualification_target = (
                        target
                    )
                    qualification_started = now

                    action = (
                        "START_PROMOTION_CONFIRMATION"
                    )

                    action_reason = reason

                else:
                    action = "HOLD_CURRENT"
                    action_reason = (
                        "Matrix is above SEED, but "
                        "next-stage promotion gates "
                        "are not satisfied."
                    )

        else:
            recorded_stage = clean_text(
                previous.get(
                    "recorded_stage"
                )
            )

            if recorded_stage not in STAGE_RANK:
                raise MaturityTransitionError(
                    "Invalid recorded stage for "
                    f"{itemid}/{stack}: "
                    f"{recorded_stage!r}"
                )

            stage_entered_at = (
                parse_timestamp(
                    previous.get(
                        "stage_entered_at_utc"
                    )
                )
                or now
            )

            qualification_target = (
                clean_text(
                    previous.get(
                        "qualification_target_stage"
                    )
                )
            )

            qualification_started = (
                parse_timestamp(
                    previous.get(
                        "qualification_started_at_utc"
                    )
                )
            )

            transition_count = as_int(
                previous.get(
                    "transition_count"
                )
            )

            recorded_rank = (
                STAGE_RANK[
                    recorded_stage
                ]
            )

            matrix_rank = (
                STAGE_RANK[
                    matrix_stage
                ]
            )

            if matrix_rank < recorded_rank:
                old_stage = recorded_stage

                recorded_stage = (
                    matrix_stage
                )

                stage_entered_at = now
                qualification_target = ""
                qualification_started = None
                transition_count += 1

                action = (
                    "DEMOTE_IMMEDIATE"
                )

                action_reason = (
                    f"Evidence now supports only "
                    f"{matrix_stage}; demoted from "
                    f"{old_stage}."
                )

            elif matrix_rank == recorded_rank:
                qualification_target = ""
                qualification_started = None

                action = "HOLD_CURRENT"

                action_reason = (
                    "Recorded maturity matches "
                    "current evidence."
                )

            else:
                target = next_stage(
                    recorded_stage
                )

                if target is None:
                    qualification_target = ""
                    qualification_started = None

                    action = "HOLD_CURRENT"

                    action_reason = (
                        "Already at maximum maturity."
                    )

                else:
                    eligible, reason = (
                        promotion_prerequisites(
                            target,
                            row,
                        )
                    )

                    if not eligible:
                        qualification_target = ""
                        qualification_started = None

                        action = "HOLD_CURRENT"

                        action_reason = (
                            "Next-stage promotion "
                            "prerequisites are not "
                            "currently satisfied."
                        )

                    else:
                        required_hours = (
                            PROMOTION_HOURS[
                                target
                            ]
                        )

                        if (
                            qualification_target
                            != target
                            or qualification_started
                            is None
                        ):
                            qualification_target = (
                                target
                            )

                            qualification_started = (
                                now
                            )

                            action = (
                                "START_PROMOTION_CONFIRMATION"
                            )

                            action_reason = (
                                f"{reason} Confirmation "
                                f"window started for "
                                f"{target}."
                            )

                        else:
                            elapsed_hours = (
                                (
                                    now
                                    - qualification_started
                                ).total_seconds()
                                / 3600
                            )

                            if (
                                elapsed_hours
                                >= required_hours
                            ):
                                old_stage = (
                                    recorded_stage
                                )

                                recorded_stage = (
                                    target
                                )

                                stage_entered_at = (
                                    now
                                )

                                qualification_target = ""
                                qualification_started = None
                                transition_count += 1

                                action = (
                                    "PROMOTE_ONE_STAGE"
                                )

                                action_reason = (
                                    f"Promoted from "
                                    f"{old_stage} to "
                                    f"{target} after "
                                    f"{elapsed_hours:.2f} "
                                    "hours of continuous "
                                    "qualification."
                                )

                            else:
                                remaining = max(
                                    required_hours
                                    - elapsed_hours,
                                    0.0,
                                )

                                action = (
                                    "WAIT_PROMOTION_CONFIRMATION"
                                )

                                action_reason = (
                                    f"Qualification for "
                                    f"{target} has persisted "
                                    f"{elapsed_hours:.2f}/"
                                    f"{required_hours} hours; "
                                    f"{remaining:.2f} hours "
                                    "remain."
                                )

        if qualification_started:
            qualification_elapsed_hours = (
                (
                    now
                    - qualification_started
                ).total_seconds()
                / 3600
            )
        else:
            qualification_elapsed_hours = 0.0

        if qualification_target:
            qualification_required_hours = (
                PROMOTION_HOURS[
                    qualification_target
                ]
            )
        else:
            qualification_required_hours = 0

        recorded_supported = int(
            STAGE_RANK[
                recorded_stage
            ]
            <= STAGE_RANK[
                matrix_stage
            ]
        )

        output_rows.append({
            "itemid":
                itemid,

            "name":
                name,

            "stack":
                stack,

            "matrix_stage":
                matrix_stage,

            "recorded_stage":
                recorded_stage,

            "supply_readiness":
                clean_text(
                    row.get(
                        "supply_readiness"
                    )
                ),

            "evidence_maturity":
                clean_text(
                    row.get(
                        "evidence_maturity"
                    )
                ),

            "freshness_state":
                clean_text(
                    row.get(
                        "freshness_state"
                    )
                ),

            "price_signal_review_eligible":
                as_int(
                    row.get(
                        "price_signal_review_eligible"
                    )
                ),

            "stock_target_signal_review_eligible":
                as_int(
                    row.get(
                        "stock_target_signal_review_eligible"
                    )
                ),

            "active_listing_review_eligible":
                as_int(
                    row.get(
                        "active_listing_review_eligible"
                    )
                ),

            "qualification_target_stage":
                qualification_target,

            "qualification_elapsed_hours":
                round(
                    qualification_elapsed_hours,
                    4,
                ),

            "qualification_required_hours":
                qualification_required_hours,

            "transition_action":
                action,

            "transition_reason":
                action_reason,

            "transition_count":
                transition_count,

            "recorded_stage_supported":
                recorded_supported,

            # Promotion never grants authority.
            "promotion_effective_for_pricing":
                0,

            "promotion_effective_for_stock":
                0,

            "price_change_authorized":
                0,

            "stock_change_authorized":
                0,

            "active_listing_change_authorized":
                0,

            "activation_ready":
                0,

            "auto_live_promotion":
                0,
        })

        state_rows.append({
            "itemid":
                itemid,

            "stack":
                stack,

            "name":
                name,

            "recorded_stage":
                recorded_stage,

            "stage_entered_at_utc":
                stage_entered_at.isoformat(),

            "qualification_target_stage":
                qualification_target,

            "qualification_started_at_utc":
                (
                    qualification_started.isoformat()
                    if qualification_started
                    else ""
                ),

            "last_evaluated_at_utc":
                now_text,

            "last_matrix_stage":
                matrix_stage,

            "transition_count":
                transition_count,
        })

    transitions = pd.DataFrame(
        output_rows
    )

    new_state = pd.DataFrame(
        state_rows
    )

    action_counts = (
        transitions[
            "transition_action"
        ]
        .value_counts()
        .sort_index()
        .to_dict()
    )

    recorded_counts = {
        stage: int(
            (
                transitions[
                    "recorded_stage"
                ]
                == stage
            ).sum()
        )
        for stage in STAGES
    }

    matrix_counts = {
        stage: int(
            (
                transitions[
                    "matrix_stage"
                ]
                == stage
            ).sum()
        )
        for stage in STAGES
    }

    promotions = int(
        (
            transitions[
                "transition_action"
            ]
            == "PROMOTE_ONE_STAGE"
        ).sum()
    )

    demotions = int(
        (
            transitions[
                "transition_action"
            ]
            == "DEMOTE_IMMEDIATE"
        ).sum()
    )

    pending_promotions = int(
        transitions[
            "transition_action"
        ].isin(
            {
                "START_PROMOTION_CONFIRMATION",
                "WAIT_PROMOTION_CONFIRMATION",
            }
        ).sum()
    )

    unsupported_recorded = int(
        (
            transitions[
                "recorded_stage_supported"
            ]
            != 1
        ).sum()
    )

    integrity_failures = (
        unsupported_recorded
    )

    safety_columns = (
        "promotion_effective_for_pricing",
        "promotion_effective_for_stock",
        "price_change_authorized",
        "stock_change_authorized",
        "active_listing_change_authorized",
        "activation_ready",
        "auto_live_promotion",
    )

    for column in safety_columns:
        if (
            transitions[column]
            != 0
        ).any():
            integrity_failures += 1

    if len(transitions) != expected_forms:
        integrity_failures += 1

    if len(new_state) != expected_forms:
        integrity_failures += 1

    if sum(
        recorded_counts.values()
    ) != expected_forms:
        integrity_failures += 1

    if sum(
        matrix_counts.values()
    ) != expected_forms:
        integrity_failures += 1

    REPORTS.mkdir(
        parents=True,
        exist_ok=True,
    )

    transitions.to_csv(
        OUTPUT_CSV,
        index=False,
    )

    # Operational maturity state only.
    # This does not modify pricing, stock targets,
    # active AH listings, or runtime CSVs.
    new_state.to_csv(
        STATE_CSV,
        index=False,
    )

    summary = {
        "status":
            (
                "PASS"
                if integrity_failures == 0
                else "FAIL"
            ),

        "stage":
            "2C.4_PROMOTION_DEMOTION_RULES",

        "item_forms_evaluated":
            int(len(transitions)),

        "matrix_stage_counts":
            matrix_counts,

        "recorded_stage_counts":
            recorded_counts,

        "transition_action_counts": {
            str(key): int(value)
            for key, value
            in action_counts.items()
        },

        "promotions_this_run":
            promotions,

        "demotions_this_run":
            demotions,

        "pending_promotions":
            pending_promotions,

        "unsupported_recorded_stages":
            unsupported_recorded,

        "promotion_hours": {
            stage: int(hours)
            for stage, hours
            in PROMOTION_HOURS.items()
        },

        "integrity_failures":
            integrity_failures,

        "promotion_effective_for_pricing":
            0,

        "promotion_effective_for_stock":
            0,

        "price_change_authorized":
            0,

        "stock_change_authorized":
            0,

        "active_listing_change_authorized":
            0,

        "activation_ready":
            0,

        "auto_live_promotions":
            0,

        "generated_at_utc":
            now_text,
    }

    SUMMARY_JSON.write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 84)
    print(
        " Phase 2C.4 Promotion / "
        "Demotion Rules"
    )
    print("=" * 84)

    print()
    print(
        f"Item forms evaluated:            "
        f"{len(transitions):>6}"
    )

    print()
    print("Matrix stages:")

    for stage in STAGES:
        print(
            f"  {stage:<24}"
            f"{matrix_counts[stage]:>6}"
        )

    print()
    print("Recorded maturity stages:")

    for stage in STAGES:
        print(
            f"  {stage:<24}"
            f"{recorded_counts[stage]:>6}"
        )

    print()
    print("Transition actions:")

    for action, count in sorted(
        action_counts.items()
    ):
        print(
            f"  {action:<34}"
            f"{count:>6}"
        )

    print()
    print(
        f"Promotions this run:             "
        f"{promotions:>6}"
    )

    print(
        f"Demotions this run:              "
        f"{demotions:>6}"
    )

    print(
        f"Pending promotions:              "
        f"{pending_promotions:>6}"
    )

    print(
        f"Unsupported recorded stages:     "
        f"{unsupported_recorded:>6}"
    )

    print()
    print(
        f"Integrity failures:              "
        f"{integrity_failures:>6}"
    )

    print()
    print(
        "Promotion effective for pricing:    0"
    )

    print(
        "Promotion effective for stock:      0"
    )

    print(
        "Price change authorized:            0"
    )

    print(
        "Stock change authorized:            0"
    )

    print(
        "Active listing change authorized:   0"
    )

    print(
        "Activation ready:                   0"
    )

    print(
        "Auto live promotions:               0"
    )

    print()
    print(
        f"Policy: {OUTPUT_CSV}"
    )

    print(
        f"State:  {STATE_CSV}"
    )

    print(
        f"Summary: {SUMMARY_JSON}"
    )

    print()

    if integrity_failures:
        print("FAIL")

        raise MaturityTransitionError(
            "2C.4 promotion/demotion "
            "validation failed."
        )

    print("PASS")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
