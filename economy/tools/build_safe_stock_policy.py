from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

CENSUS_CSV = REPORTS / "active-supply-census.csv"
CENSUS_SUMMARY = REPORTS / "active-supply-census-summary.json"

PRESSURE_CSV = REPORTS / "supply-pressure-report.csv"
PRESSURE_SUMMARY = REPORTS / "supply-pressure-summary.json"

CONFIDENCE_CSV = REPORTS / "market-history-confidence.csv"
CONFIDENCE_SUMMARY = REPORTS / "market-history-confidence-summary.json"

SELLER_RUNTIME = GENERATED / "market-sell.csv"
BUYER_RUNTIME = GENERATED / "market-buy.csv"

OUTPUT_CSV = REPORTS / "safe-stock-policy.csv"
SUMMARY_JSON = REPORTS / "safe-stock-policy-summary.json"


class SafeStockPolicyError(RuntimeError):
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


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SafeStockPolicyError(
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
        raise SafeStockPolicyError(
            f"{name} is not PASS."
        )

    if summary.get(
        "integrity_failures",
        0,
    ) != 0:
        raise SafeStockPolicyError(
            f"{name} contains integrity failures."
        )


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


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass

    return str(value).strip()


def policy_action(row: pd.Series) -> tuple[str, str]:
    pressure = clean_text(
        row.get("pressure_state")
    )

    sufficient_baseline = as_int(
        row.get("sufficient_baseline")
    )

    player_target_persistent = as_int(
        row.get("player_target_persistent")
    )

    bot_overhang_persistent = as_int(
        row.get("bot_overhang_persistent")
    )

    maturity = clean_text(
        row.get("evidence_maturity")
    )

    freshness = clean_text(
        row.get("freshness_state")
    )

    observations = as_int(
        row.get("observations")
    )

    span_hours = as_float(
        row.get("observation_span_hours")
    )

    if not sufficient_baseline:
        return (
            "HOLD_BASELINE_OBSERVATION",
            (
                "Insufficient temporal baseline; "
                f"{observations} observations across "
                f"{span_hours:.2f} hours."
            ),
        )

    if pressure == "NO_PLAYER_PRESSURE":
        return (
            "HOLD_NO_PLAYER_PRESSURE",
            (
                "No persistent player supply detected; "
                "AHBot is supplying the configured "
                "market floor as intended."
            ),
        )

    if pressure == "TRANSIENT_PLAYER_SUPPLY":
        return (
            "OBSERVE_TRANSIENT_PLAYER_SUPPLY",
            (
                "Player supply exists but is not "
                "persistent enough to justify any "
                "stock-policy response."
            ),
        )

    if pressure == "SUSTAINED_PLAYER_SUPPLY":
        if (
            player_target_persistent
            and bot_overhang_persistent
        ):
            return (
                "REVIEW_PERSISTENT_BOT_OVERHANG",
                (
                    "Player supply persistently covers "
                    "the market target while excess "
                    "AHBot listings also persist. "
                    "Review active bot inventory only; "
                    "do not lower the market target "
                    "automatically."
                ),
            )

        if player_target_persistent:
            return (
                "HOLD_TARGET_PLAYER_SUPPLY_COVERS",
                (
                    "Player supply persistently covers "
                    "the configured target. AHBot "
                    "should naturally refrain from "
                    "restocking; target remains unchanged."
                ),
            )

        if bot_overhang_persistent:
            return (
                "REVIEW_PERSISTENT_BOT_OVERHANG",
                (
                    "Persistent AHBot overhang detected. "
                    "Review active bot inventory without "
                    "automatically changing the target."
                ),
            )

        return (
            "HOLD_TARGET_SUSTAINED_PLAYER_SUPPLY",
            (
                "Sustained player participation exists, "
                "but current evidence does not justify "
                "changing the market target."
            ),
        )

    raise SafeStockPolicyError(
        "Unexpected pressure state for "
        f"itemid={row.get('itemid')} "
        f"stack={row.get('stack')}: "
        f"{pressure!r}"
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

    for summary_name, summary in (
        ("census", census_summary),
        ("pressure", pressure_summary),
        ("confidence", confidence_summary),
    ):
        for field in (
            "activation_ready",
            "auto_live_promotions",
        ):
            if summary.get(field, 0) != 0:
                raise SafeStockPolicyError(
                    f"{summary_name} unsafe state: "
                    f"{field}={summary.get(field)}"
                )

    if census_summary.get(
        "stock_change_authorized",
        0,
    ) != 0:
        raise SafeStockPolicyError(
            "2B.1 has stock changes authorized."
        )

    if pressure_summary.get(
        "stock_change_authorized",
        0,
    ) != 0:
        raise SafeStockPolicyError(
            "2B.2 has stock changes authorized."
        )

    if not CENSUS_CSV.exists():
        raise SafeStockPolicyError(
            f"Missing census CSV: {CENSUS_CSV}"
        )

    if not PRESSURE_CSV.exists():
        raise SafeStockPolicyError(
            f"Missing pressure CSV: {PRESSURE_CSV}"
        )

    if not CONFIDENCE_CSV.exists():
        raise SafeStockPolicyError(
            f"Missing confidence CSV: {CONFIDENCE_CSV}"
        )

    if not SELLER_RUNTIME.exists():
        raise SafeStockPolicyError(
            f"Missing seller runtime: {SELLER_RUNTIME}"
        )

    if not BUYER_RUNTIME.exists():
        raise SafeStockPolicyError(
            f"Missing buyer runtime: {BUYER_RUNTIME}"
        )

    seller_before = sha256(
        SELLER_RUNTIME
    )

    buyer_before = sha256(
        BUYER_RUNTIME
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

    if len(census) != int(
        census_summary[
            "configured_item_forms"
        ]
    ):
        raise SafeStockPolicyError(
            "Census row count disagrees "
            "with census summary."
        )

    if len(pressure) != int(
        pressure_summary[
            "item_forms_evaluated"
        ]
    ):
        raise SafeStockPolicyError(
            "Pressure row count disagrees "
            "with pressure summary."
        )

    combined = census.merge(
        pressure,
        on=[
            "itemid",
            "stack",
        ],
        how="left",
        suffixes=(
            "_census",
            "_pressure",
        ),
        validate="one_to_one",
    )

    if combined[
        "pressure_state"
    ].isna().any():
        missing = combined[
            combined[
                "pressure_state"
            ].isna()
        ][
            [
                "itemid",
                "stack",
            ]
        ]

        raise SafeStockPolicyError(
            "Configured forms missing from "
            "pressure report:\n"
            + missing.to_string(
                index=False
            )
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
        "trusted_and_recent",
        "trusted_shortfalls",
    ]

    history = confidence[
        [
            column
            for column in history_columns
            if column in confidence.columns
        ]
    ].copy()

    combined = combined.merge(
        history,
        on=[
            "itemid",
            "stack",
        ],
        how="left",
        validate="one_to_one",
    )

    combined[
        "evidence_maturity"
    ] = (
        combined[
            "evidence_maturity"
        ]
        .fillna("NO_DATA")
    )

    combined[
        "freshness_state"
    ] = (
        combined[
            "freshness_state"
        ]
        .fillna("NO_DATA")
    )

    for column in (
        "player_involved_transactions",
        "organic_transactions",
        "ask_evidence_events",
        "bid_evidence_events",
        "trusted_and_recent",
    ):
        if column not in combined.columns:
            combined[column] = 0

        combined[column] = (
            combined[column]
            .fillna(0)
            .astype(int)
        )

    if "trusted_shortfalls" not in combined.columns:
        combined[
            "trusted_shortfalls"
        ] = ""

    combined[
        "trusted_shortfalls"
    ] = (
        combined[
            "trusted_shortfalls"
        ]
        .fillna("")
    )

    actions = combined.apply(
        policy_action,
        axis=1,
    )

    combined[
        "policy_action"
    ] = [
        action
        for action, _ in actions
    ]

    combined[
        "policy_reason"
    ] = [
        reason
        for _, reason in actions
    ]

    combined[
        "current_market_target"
    ] = combined[
        "configured_market_target_census"
    ].astype(int)

    # 2B.3 does not alter targets.
    combined[
        "recommended_market_target"
    ] = combined[
        "current_market_target"
    ]

    combined[
        "recommended_target_delta"
    ] = 0

    combined[
        "target_change_review_eligible"
    ] = 0

    combined[
        "active_listing_review_eligible"
    ] = (
        combined[
            "policy_action"
        ]
        == "REVIEW_PERSISTENT_BOT_OVERHANG"
    ).astype(int)

    # Even REVIEW states are human review only.
    combined[
        "stock_change_authorized"
    ] = 0

    combined[
        "active_listing_change_authorized"
    ] = 0

    combined[
        "history_stock_influence_ready"
    ] = 0

    combined[
        "activation_ready"
    ] = 0

    combined[
        "auto_live_promotion"
    ] = 0

    output_columns = [
        "itemid",
        "name_census",
        "stack",
        "current_market_target",
        "recommended_market_target",
        "recommended_target_delta",

        "ahbot_active_listings",
        "player_active_listings",
        "total_active_listings",
        "target_status",

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

        "evidence_maturity",
        "freshness_state",
        "player_involved_transactions",
        "organic_transactions",
        "ask_evidence_events",
        "bid_evidence_events",
        "trusted_and_recent",
        "trusted_shortfalls",

        "policy_action",
        "policy_reason",

        "target_change_review_eligible",
        "active_listing_review_eligible",

        "stock_change_authorized",
        "active_listing_change_authorized",
        "history_stock_influence_ready",
        "activation_ready",
        "auto_live_promotion",
    ]

    policy = combined[
        [
            column
            for column in output_columns
            if column in combined.columns
        ]
    ].copy()

    if (
        "name_census"
        in policy.columns
    ):
        policy = policy.rename(
            columns={
                "name_census":
                    "name"
            }
        )

    action_counts = (
        policy[
            "policy_action"
        ]
        .value_counts()
        .sort_index()
        .to_dict()
    )

    target_changes = int(
        (
            policy[
                "recommended_target_delta"
            ]
            != 0
        ).sum()
    )

    active_listing_reviews = int(
        policy[
            "active_listing_review_eligible"
        ].sum()
    )

    persistence_ready_forms = int(
        policy[
            "persistence_ready"
        ].sum()
    )

    integrity_failures = 0

    if target_changes != 0:
        integrity_failures += 1

    safety_columns = (
        "stock_change_authorized",
        "active_listing_change_authorized",
        "history_stock_influence_ready",
        "activation_ready",
        "auto_live_promotion",
    )

    for column in safety_columns:
        if (
            policy[column]
            != 0
        ).any():
            integrity_failures += 1

    seller_after = sha256(
        SELLER_RUNTIME
    )

    buyer_after = sha256(
        BUYER_RUNTIME
    )

    seller_unchanged = int(
        seller_before
        == seller_after
    )

    buyer_unchanged = int(
        buyer_before
        == buyer_after
    )

    if not seller_unchanged:
        integrity_failures += 1

    if not buyer_unchanged:
        integrity_failures += 1

    summary = {
        "status":
            (
                "PASS"
                if integrity_failures == 0
                else "FAIL"
            ),

        "stage":
            "2B.3_SAFE_STOCK_TARGET_POLICY",

        "item_forms_evaluated":
            int(len(policy)),

        "policy_action_counts":
            {
                str(key): int(value)
                for key, value
                in action_counts.items()
            },

        "persistence_ready_forms":
            persistence_ready_forms,

        "active_listing_review_eligible_forms":
            active_listing_reviews,

        "target_change_review_eligible_forms":
            int(
                policy[
                    "target_change_review_eligible"
                ].sum()
            ),

        "recommended_target_changes":
            target_changes,

        "seller_runtime_sha256_before":
            seller_before,

        "seller_runtime_sha256_after":
            seller_after,

        "buyer_runtime_sha256_before":
            buyer_before,

        "buyer_runtime_sha256_after":
            buyer_after,

        "seller_runtime_unchanged":
            seller_unchanged,

        "buyer_runtime_unchanged":
            buyer_unchanged,

        "integrity_failures":
            integrity_failures,

        "stock_change_authorized":
            0,

        "active_listing_change_authorized":
            0,

        "history_stock_influence_ready":
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

    REPORTS.mkdir(
        parents=True,
        exist_ok=True,
    )

    policy.to_csv(
        OUTPUT_CSV,
        index=False,
    )

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
        " Phase 2B.3 Safe Stock-Target Policy"
    )
    print("=" * 84)

    print()
    print(
        f"Item forms evaluated:            "
        f"{len(policy):>6}"
    )

    print(
        f"Persistence-ready forms:         "
        f"{persistence_ready_forms:>6}"
    )

    print()
    print("Policy actions:")

    for action, count in sorted(
        action_counts.items()
    ):
        print(
            f"  {action:<42}"
            f"{count:>6}"
        )

    print()
    print(
        f"Active-listing review eligible:  "
        f"{active_listing_reviews:>6}"
    )

    print(
        f"Target-change review eligible:   "
        f"{int(policy['target_change_review_eligible'].sum()):>6}"
    )

    print(
        f"Recommended target changes:      "
        f"{target_changes:>6}"
    )

    print()
    print(
        f"Seller runtime unchanged:        "
        f"{seller_unchanged:>6}"
    )

    print(
        f"Buyer runtime unchanged:         "
        f"{buyer_unchanged:>6}"
    )

    print()
    print(
        f"Integrity failures:              "
        f"{integrity_failures:>6}"
    )

    print()
    print(
        "Stock change authorized:             0"
    )

    print(
        "Active listing change authorized:    0"
    )

    print(
        "History stock influence ready:       0"
    )

    print(
        "Activation ready:                    0"
    )

    print(
        "Auto live promotions:                0"
    )

    print()
    print(
        f"Policy:  {OUTPUT_CSV}"
    )

    print(
        f"Summary: {SUMMARY_JSON}"
    )

    print()

    if integrity_failures:
        print("FAIL")
        raise SafeStockPolicyError(
            "2B.3 policy failed safety validation."
        )

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
