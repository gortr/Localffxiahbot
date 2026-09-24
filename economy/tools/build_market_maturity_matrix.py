from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
REPORTS = ROOT / "economy" / "reports"

CENSUS_CSV = (
    REPORTS
    / "active-supply-census.csv"
)

CENSUS_SUMMARY = (
    REPORTS
    / "active-supply-census-summary.json"
)

PRESSURE_CSV = (
    REPORTS
    / "supply-pressure-report.csv"
)

PRESSURE_SUMMARY = (
    REPORTS
    / "supply-pressure-summary.json"
)

CONFIDENCE_CSV = (
    REPORTS
    / "market-history-confidence.csv"
)

CONFIDENCE_SUMMARY = (
    REPORTS
    / "market-history-confidence-summary.json"
)

OUTPUT_CSV = (
    REPORTS
    / "market-maturity-matrix.csv"
)

SUMMARY_JSON = (
    REPORTS
    / "market-maturity-summary.json"
)


MATURITY_STAGES = (
    "SEED",
    "OBSERVING",
    "DEVELOPING",
    "ESTABLISHED",
    "TRUSTED_OBSERVATION",
)


class MarketMaturityError(RuntimeError):
    pass


def load_json(
    path: Path,
) -> dict[str, Any]:
    if not path.exists():
        raise MarketMaturityError(
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
        raise MarketMaturityError(
            f"{name} is not PASS."
        )

    if summary.get(
        "integrity_failures",
        0,
    ) != 0:
        raise MarketMaturityError(
            f"{name} contains integrity failures."
        )


def as_int(
    value: Any,
) -> int:
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


def clean_text(
    value: Any,
) -> str:
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
        "nan",
        "none",
        "null",
    }:
        return ""

    return value


def supply_readiness(
    row: pd.Series,
) -> str:
    sufficient = as_int(
        row.get(
            "sufficient_baseline"
        )
    )

    persistent = as_int(
        row.get(
            "persistence_ready"
        )
    )

    if persistent:
        return "PERSISTENCE_READY"

    if sufficient:
        return "BASELINE_READY"

    return "BASELINE_BUILDING"


def maturity_state(
    row: pd.Series,
) -> tuple[str, str]:
    supply = clean_text(
        row.get(
            "supply_readiness"
        )
    )

    history = clean_text(
        row.get(
            "evidence_maturity"
        )
    )

    freshness = clean_text(
        row.get(
            "freshness_state"
        )
    )

    observations = as_int(
        row.get(
            "observations"
        )
    )

    if supply == "BASELINE_BUILDING":
        return (
            "SEED",
            (
                "Supply observation baseline "
                "is still building; "
                f"{observations} observations "
                "are currently available."
            ),
        )

    if supply == "BASELINE_READY":
        return (
            "OBSERVING",
            (
                "Supply baseline exists, but "
                "the persistence-readiness gate "
                "has not yet been reached."
            ),
        )

    if history in {
        "",
        "NO_DATA",
    }:
        return (
            "OBSERVING",
            (
                "Supply telemetry is persistence-ready, "
                "but no player-derived market history "
                "exists for this item-form."
            ),
        )

    if history == "SPARSE":
        return (
            "OBSERVING",
            (
                "Supply telemetry is persistence-ready, "
                "but player-derived market history "
                "remains sparse."
            ),
        )

    if freshness != "RECENT":
        return (
            "OBSERVING",
            (
                "Historical evidence exists, but it is "
                f"{freshness or 'UNKNOWN'} rather than "
                "RECENT; maturity promotion is capped."
            ),
        )

    if history == "DEVELOPING":
        return (
            "DEVELOPING",
            (
                "Persistence-ready supply telemetry "
                "and recent DEVELOPING market history."
            ),
        )

    if history == "ESTABLISHED":
        return (
            "ESTABLISHED",
            (
                "Persistence-ready supply telemetry "
                "and recent ESTABLISHED market history."
            ),
        )

    if history == "TRUSTED":
        return (
            "TRUSTED_OBSERVATION",
            (
                "Persistence-ready supply telemetry "
                "and recent TRUSTED market history. "
                "This remains observational and grants "
                "no deployment authority."
            ),
        )

    raise MarketMaturityError(
        "Unexpected history maturity: "
        f"{history!r}"
    )


def blockers(
    row: pd.Series,
) -> str:
    values: list[str] = []

    supply = clean_text(
        row.get(
            "supply_readiness"
        )
    )

    history = clean_text(
        row.get(
            "evidence_maturity"
        )
    )

    freshness = clean_text(
        row.get(
            "freshness_state"
        )
    )

    if supply == "BASELINE_BUILDING":
        values.append(
            "SUPPLY_BASELINE_INCOMPLETE"
        )

    elif supply == "BASELINE_READY":
        values.append(
            "SUPPLY_PERSISTENCE_INCOMPLETE"
        )

    if history in {
        "",
        "NO_DATA",
    }:
        values.append(
            "NO_PLAYER_HISTORY"
        )

    elif history == "SPARSE":
        values.append(
            "SPARSE_PLAYER_HISTORY"
        )

    elif history == "DEVELOPING":
        values.append(
            "HISTORY_NOT_ESTABLISHED"
        )

    elif history == "ESTABLISHED":
        values.append(
            "HISTORY_NOT_TRUSTED"
        )

    if (
        history
        not in {
            "",
            "NO_DATA",
        }
        and freshness != "RECENT"
    ):
        values.append(
            "HISTORY_NOT_RECENT"
        )

    return ";".join(
        values
    )


def main() -> int:
    census_summary = load_json(
        CENSUS_SUMMARY
    )

    pressure_summary = load_json(
        PRESSURE_SUMMARY
    )

    confidence_summary = load_json(
        CONFIDENCE_SUMMARY
    )

    require_pass(
        "2B.1 census",
        census_summary,
    )

    require_pass(
        "2B.2 pressure",
        pressure_summary,
    )

    require_pass(
        "2A.5 confidence",
        confidence_summary,
    )

    if not CENSUS_CSV.exists():
        raise MarketMaturityError(
            f"Missing census CSV: {CENSUS_CSV}"
        )

    if not PRESSURE_CSV.exists():
        raise MarketMaturityError(
            f"Missing pressure CSV: {PRESSURE_CSV}"
        )

    if not CONFIDENCE_CSV.exists():
        raise MarketMaturityError(
            f"Missing confidence CSV: {CONFIDENCE_CSV}"
        )

    census = pd.read_csv(
        CENSUS_CSV
    )

    pressure = pd.read_csv(
        PRESSURE_CSV
    )

    confidence = pd.read_csv(
        CONFIDENCE_CSV
    )

    expected_forms = int(
        census_summary[
            "configured_item_forms"
        ]
    )

    if len(census) != expected_forms:
        raise MarketMaturityError(
            "Census row count disagrees "
            "with census summary."
        )

    if len(pressure) != expected_forms:
        raise MarketMaturityError(
            "Pressure row count disagrees "
            "with configured item-form count."
        )

    base = census[
        [
            "itemid",
            "name",
            "stack",
            "configured_market_target",
            "ahbot_active_listings",
            "player_active_listings",
            "total_active_listings",
            "target_status",
        ]
    ].copy()

    pressure_columns = [
        "itemid",
        "stack",
        "observations",
        "observation_span_hours",
        "pressure_state",
        "sufficient_baseline",
        "persistence_ready",
        "player_presence_ratio",
        "player_meets_target_ratio",
        "bot_overhang_ratio",
        "player_target_persistent",
        "bot_overhang_persistent",
    ]

    base = base.merge(
        pressure[
            pressure_columns
        ],
        on=[
            "itemid",
            "stack",
        ],
        how="left",
        validate="one_to_one",
    )

    if base[
        "pressure_state"
    ].isna().any():
        raise MarketMaturityError(
            "One or more configured forms "
            "lack supply-pressure data."
        )

    history_columns = [
        "itemid",
        "stack",
        "evidence_maturity",
        "freshness_state",
        "player_involved_transactions",
        "organic_transactions",
        "ask_evidence_events",
        "bid_evidence_events",
        "valid_player_listing_durations",
        "trusted_and_recent",
        "trusted_shortfalls",
    ]

    available_history_columns = [
        column
        for column in history_columns
        if column in confidence.columns
    ]

    history = confidence[
        available_history_columns
    ].copy()

    base = base.merge(
        history,
        on=[
            "itemid",
            "stack",
        ],
        how="left",
        validate="one_to_one",
    )

    base[
        "evidence_maturity"
    ] = (
        base[
            "evidence_maturity"
        ]
        .fillna("NO_DATA")
    )

    base[
        "freshness_state"
    ] = (
        base[
            "freshness_state"
        ]
        .fillna("NO_DATA")
    )

    for column in (
        "player_involved_transactions",
        "organic_transactions",
        "ask_evidence_events",
        "bid_evidence_events",
        "valid_player_listing_durations",
        "trusted_and_recent",
    ):
        if column not in base.columns:
            base[column] = 0

        base[column] = (
            base[column]
            .fillna(0)
            .astype(int)
        )

    if "trusted_shortfalls" not in base.columns:
        base[
            "trusted_shortfalls"
        ] = ""

    base[
        "trusted_shortfalls"
    ] = (
        base[
            "trusted_shortfalls"
        ]
        .fillna("")
    )

    base[
        "supply_readiness"
    ] = base.apply(
        supply_readiness,
        axis=1,
    )

    stage_results = base.apply(
        maturity_state,
        axis=1,
    )

    base[
        "market_maturity"
    ] = [
        stage
        for stage, _ in stage_results
    ]

    base[
        "maturity_reason"
    ] = [
        reason
        for _, reason in stage_results
    ]

    base[
        "maturity_blockers"
    ] = base.apply(
        blockers,
        axis=1,
    )

    base[
        "price_readiness_candidate"
    ] = (
        (
            base[
                "market_maturity"
            ]
            == "TRUSTED_OBSERVATION"
        )
        & (
            base[
                "trusted_and_recent"
            ]
            == 1
        )
    ).astype(int)

    base[
        "stock_readiness_candidate"
    ] = (
        (
            base[
                "supply_readiness"
            ]
            == "PERSISTENCE_READY"
        )
        & (
            base[
                "market_maturity"
            ].isin(
                {
                    "ESTABLISHED",
                    "TRUSTED_OBSERVATION",
                }
            )
        )
    ).astype(int)

    # Readiness candidates are observational only.
    base[
        "price_change_authorized"
    ] = 0

    base[
        "stock_change_authorized"
    ] = 0

    base[
        "active_listing_change_authorized"
    ] = 0

    base[
        "activation_ready"
    ] = 0

    base[
        "auto_live_promotion"
    ] = 0

    stage_counts = {
        stage: int(
            (
                base[
                    "market_maturity"
                ]
                == stage
            ).sum()
        )
        for stage in MATURITY_STAGES
    }

    supply_counts = {
        state: int(
            (
                base[
                    "supply_readiness"
                ]
                == state
            ).sum()
        )
        for state in (
            "BASELINE_BUILDING",
            "BASELINE_READY",
            "PERSISTENCE_READY",
        )
    }

    history_counts = {
        state: int(
            (
                base[
                    "evidence_maturity"
                ]
                == state
            ).sum()
        )
        for state in (
            "NO_DATA",
            "SPARSE",
            "DEVELOPING",
            "ESTABLISHED",
            "TRUSTED",
        )
    }

    price_candidates = int(
        base[
            "price_readiness_candidate"
        ].sum()
    )

    stock_candidates = int(
        base[
            "stock_readiness_candidate"
        ].sum()
    )

    integrity_failures = 0

    if sum(
        stage_counts.values()
    ) != len(base):
        integrity_failures += 1

    if sum(
        supply_counts.values()
    ) != len(base):
        integrity_failures += 1

    if sum(
        history_counts.values()
    ) != len(base):
        integrity_failures += 1

    safety_columns = (
        "price_change_authorized",
        "stock_change_authorized",
        "active_listing_change_authorized",
        "activation_ready",
        "auto_live_promotion",
    )

    for column in safety_columns:
        if (
            base[column]
            != 0
        ).any():
            integrity_failures += 1

    if (
        base[
            "itemid"
        ]
        .astype(str)
        .str.len()
        .eq(0)
        .any()
    ):
        integrity_failures += 1

    output_columns = [
        "itemid",
        "name",
        "stack",

        "configured_market_target",
        "ahbot_active_listings",
        "player_active_listings",
        "total_active_listings",
        "target_status",

        "observations",
        "observation_span_hours",
        "pressure_state",
        "supply_readiness",
        "sufficient_baseline",
        "persistence_ready",

        "player_presence_ratio",
        "player_meets_target_ratio",
        "bot_overhang_ratio",
        "player_target_persistent",
        "bot_overhang_persistent",

        "evidence_maturity",
        "freshness_state",
        "player_involved_transactions",
        "organic_transactions",
        "ask_evidence_events",
        "bid_evidence_events",
        "valid_player_listing_durations",
        "trusted_and_recent",
        "trusted_shortfalls",

        "market_maturity",
        "maturity_reason",
        "maturity_blockers",

        "price_readiness_candidate",
        "stock_readiness_candidate",

        "price_change_authorized",
        "stock_change_authorized",
        "active_listing_change_authorized",
        "activation_ready",
        "auto_live_promotion",
    ]

    matrix = base[
        output_columns
    ].copy()

    REPORTS.mkdir(
        parents=True,
        exist_ok=True,
    )

    matrix.to_csv(
        OUTPUT_CSV,
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
            "2C.1_MARKET_MATURITY_READINESS_MATRIX",

        "configured_item_forms":
            int(len(matrix)),

        "supply_snapshot_count":
            int(
                pressure_summary[
                    "snapshots"
                ]
            ),

        "market_maturity_counts":
            stage_counts,

        "supply_readiness_counts":
            supply_counts,

        "history_maturity_counts":
            history_counts,

        "price_readiness_candidate_forms":
            price_candidates,

        "stock_readiness_candidate_forms":
            stock_candidates,

        "integrity_failures":
            integrity_failures,

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
            datetime.now(
                timezone.utc
            ).isoformat(),
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
        " Phase 2C.1 Market Maturity "
        "Readiness Matrix"
    )
    print("=" * 84)

    print()
    print(
        f"Configured item forms:           "
        f"{len(matrix):>6}"
    )

    print(
        f"Supply snapshots:                "
        f"{summary['supply_snapshot_count']:>6}"
    )

    print()
    print("Market maturity:")

    for stage in MATURITY_STAGES:
        print(
            f"  {stage:<24}"
            f"{stage_counts[stage]:>6}"
        )

    print()
    print("Supply readiness:")

    for state in (
        "BASELINE_BUILDING",
        "BASELINE_READY",
        "PERSISTENCE_READY",
    ):
        print(
            f"  {state:<24}"
            f"{supply_counts[state]:>6}"
        )

    print()
    print("History maturity:")

    for state in (
        "NO_DATA",
        "SPARSE",
        "DEVELOPING",
        "ESTABLISHED",
        "TRUSTED",
    ):
        print(
            f"  {state:<24}"
            f"{history_counts[state]:>6}"
        )

    print()
    print(
        f"Price-readiness candidates:      "
        f"{price_candidates:>6}"
    )

    print(
        f"Stock-readiness candidates:      "
        f"{stock_candidates:>6}"
    )

    print()
    print(
        f"Integrity failures:              "
        f"{integrity_failures:>6}"
    )

    print()
    print(
        "Price change authorized:             0"
    )

    print(
        "Stock change authorized:             0"
    )

    print(
        "Active listing change authorized:    0"
    )

    print(
        "Activation ready:                    0"
    )

    print(
        "Auto live promotions:                0"
    )

    print()
    print(
        f"Matrix:  {OUTPUT_CSV}"
    )

    print(
        f"Summary: {SUMMARY_JSON}"
    )

    print()

    if integrity_failures:
        print("FAIL")

        raise MarketMaturityError(
            "2C.1 maturity matrix "
            "failed integrity validation."
        )

    print("PASS")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
