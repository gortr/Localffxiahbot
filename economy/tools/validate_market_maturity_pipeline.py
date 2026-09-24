from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
TOOLS = ROOT / "economy" / "tools"
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

SELLER_RUNTIME = GENERATED / "market-sell.csv"
BUYER_RUNTIME = GENERATED / "market-buy.csv"

MATRIX_TOOL = (
    TOOLS
    / "build_market_maturity_matrix.py"
)

PRICE_TOOL = (
    TOOLS
    / "build_price_readiness_gates.py"
)

STOCK_TOOL = (
    TOOLS
    / "build_stock_readiness_gates.py"
)

TRANSITION_TOOL = (
    TOOLS
    / "build_maturity_transition_policy.py"
)

MATRIX_CSV = (
    REPORTS
    / "market-maturity-matrix.csv"
)

PRICE_CSV = (
    REPORTS
    / "price-readiness-gates.csv"
)

STOCK_CSV = (
    REPORTS
    / "stock-readiness-gates.csv"
)

TRANSITION_CSV = (
    REPORTS
    / "maturity-transition-policy.csv"
)

MATRIX_SUMMARY = (
    REPORTS
    / "market-maturity-summary.json"
)

PRICE_SUMMARY = (
    REPORTS
    / "price-readiness-gates-summary.json"
)

STOCK_SUMMARY = (
    REPORTS
    / "stock-readiness-gates-summary.json"
)

TRANSITION_SUMMARY = (
    REPORTS
    / "maturity-transition-summary.json"
)

VALIDATION_SUMMARY = (
    REPORTS
    / "market-maturity-validation-summary.json"
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


class MaturityValidationError(RuntimeError):
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
        raise MaturityValidationError(
            f"Missing summary: {path}"
        )

    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


def run_stage(path: Path) -> None:
    if not path.exists():
        raise MaturityValidationError(
            f"Missing tool: {path}"
        )

    print()
    print("=" * 84)
    print(f" RUNNING {path.name}")
    print("=" * 84)
    print()

    subprocess.run(
        [
            sys.executable,
            str(path),
        ],
        cwd=ROOT,
        check=True,
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


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass

    return str(value).strip()


def main() -> int:
    for path in (
        SELLER_RUNTIME,
        BUYER_RUNTIME,
    ):
        if not path.exists():
            raise MaturityValidationError(
                f"Missing runtime file: {path}"
            )

    seller_before = sha256(
        SELLER_RUNTIME
    )

    buyer_before = sha256(
        BUYER_RUNTIME
    )

    print()
    print("=" * 84)
    print(
        " Phase 2C.5 End-to-End "
        "Market Maturity Validation"
    )
    print("=" * 84)

    print()
    print("Runtime hashes before:")

    print(
        f"  seller: {seller_before}"
    )

    print(
        f"  buyer:  {buyer_before}"
    )

    # This intentionally does NOT:
    # - create another supply snapshot
    # - alter market runtime CSVs
    # - alter AH listings
    #
    # It rebuilds only the current 2C
    # observational/maturity layers.
    for stage in (
        MATRIX_TOOL,
        PRICE_TOOL,
        STOCK_TOOL,
        TRANSITION_TOOL,
    ):
        run_stage(stage)

    seller_after = sha256(
        SELLER_RUNTIME
    )

    buyer_after = sha256(
        BUYER_RUNTIME
    )

    matrix_summary = load_json(
        MATRIX_SUMMARY
    )

    price_summary = load_json(
        PRICE_SUMMARY
    )

    stock_summary = load_json(
        STOCK_SUMMARY
    )

    transition_summary = load_json(
        TRANSITION_SUMMARY
    )

    summaries = (
        (
            "maturity matrix",
            matrix_summary,
        ),
        (
            "price gates",
            price_summary,
        ),
        (
            "stock gates",
            stock_summary,
        ),
        (
            "transition policy",
            transition_summary,
        ),
    )

    failures = 0

    for name, summary in summaries:
        if summary.get(
            "status"
        ) != "PASS":
            print(
                f"FAIL: {name} status="
                f"{summary.get('status')}"
            )
            failures += 1

        if as_int(
            summary.get(
                "integrity_failures"
            )
        ) != 0:
            print(
                f"FAIL: {name} contains "
                "integrity failures."
            )
            failures += 1

    expected_forms = as_int(
        matrix_summary.get(
            "configured_item_forms"
        )
    )

    count_values = {
        "matrix":
            expected_forms,

        "price":
            as_int(
                price_summary.get(
                    "item_forms_evaluated"
                )
            ),

        "stock":
            as_int(
                stock_summary.get(
                    "item_forms_evaluated"
                )
            ),

        "transition":
            as_int(
                transition_summary.get(
                    "item_forms_evaluated"
                )
            ),
    }

    if len(
        set(
            count_values.values()
        )
    ) != 1:
        print(
            "FAIL: 2C item-form counts disagree:"
        )

        for name, value in count_values.items():
            print(
                f"  {name}: {value}"
            )

        failures += 1

    for path in (
        MATRIX_CSV,
        PRICE_CSV,
        STOCK_CSV,
        TRANSITION_CSV,
    ):
        if not path.exists():
            print(
                f"FAIL: missing output {path}"
            )
            failures += 1

    matrix = pd.read_csv(
        MATRIX_CSV
    )

    price = pd.read_csv(
        PRICE_CSV
    )

    stock = pd.read_csv(
        STOCK_CSV
    )

    transition = pd.read_csv(
        TRANSITION_CSV
    )

    for name, frame in (
        ("matrix", matrix),
        ("price", price),
        ("stock", stock),
        ("transition", transition),
    ):
        if len(frame) != expected_forms:
            print(
                f"FAIL: {name} CSV has "
                f"{len(frame)} rows; "
                f"expected {expected_forms}."
            )

            failures += 1

        duplicates = int(
            frame.duplicated(
                subset=[
                    "itemid",
                    "stack",
                ]
            ).sum()
        )

        if duplicates:
            print(
                f"FAIL: {name} contains "
                f"{duplicates} duplicate "
                "item-forms."
            )

            failures += 1

    key_columns = [
        "itemid",
        "stack",
    ]

    matrix_keys = set(
        map(
            tuple,
            matrix[
                key_columns
            ].to_records(
                index=False
            ),
        )
    )

    for name, frame in (
        ("price", price),
        ("stock", stock),
        ("transition", transition),
    ):
        frame_keys = set(
            map(
                tuple,
                frame[
                    key_columns
                ].to_records(
                    index=False
                ),
            )
        )

        if frame_keys != matrix_keys:
            print(
                f"FAIL: {name} item-form "
                "key set differs from matrix."
            )
            failures += 1

    joined = (
        matrix[
            [
                "itemid",
                "stack",
                "market_maturity",
                "price_readiness_candidate",
                "stock_readiness_candidate",
            ]
        ]
        .merge(
            price[
                [
                    "itemid",
                    "stack",
                    "price_signal_review_eligible",
                    "readiness_consistent",
                ]
            ],
            on=[
                "itemid",
                "stack",
            ],
            how="inner",
            validate="one_to_one",
        )
        .merge(
            stock[
                [
                    "itemid",
                    "stack",
                    "stock_target_signal_review_eligible",
                    "active_listing_review_eligible",
                    "target_readiness_consistent",
                    "listing_readiness_consistent",
                ]
            ],
            on=[
                "itemid",
                "stack",
            ],
            how="inner",
            validate="one_to_one",
        )
        .merge(
            transition[
                [
                    "itemid",
                    "stack",
                    "matrix_stage",
                    "recorded_stage",
                    "transition_action",
                    "recorded_stage_supported",
                ]
            ],
            on=[
                "itemid",
                "stack",
            ],
            how="inner",
            validate="one_to_one",
        )
    )

    if len(joined) != expected_forms:
        print(
            "FAIL: combined 2C dataset "
            "is incomplete."
        )
        failures += 1

    price_disagreements = int(
        (
            joined[
                "price_readiness_candidate"
            ].astype(int)
            != joined[
                "price_signal_review_eligible"
            ].astype(int)
        ).sum()
    )

    if price_disagreements:
        print(
            "FAIL: matrix/price readiness "
            f"disagreements={price_disagreements}"
        )
        failures += 1

    stock_disagreements = int(
        (
            joined[
                "stock_readiness_candidate"
            ].astype(int)
            != joined[
                "stock_target_signal_review_eligible"
            ].astype(int)
        ).sum()
    )

    if stock_disagreements:
        print(
            "FAIL: matrix/stock readiness "
            f"disagreements={stock_disagreements}"
        )
        failures += 1

    price_consistency_failures = int(
        (
            price[
                "readiness_consistent"
            ].astype(int)
            != 1
        ).sum()
    )

    stock_target_consistency_failures = int(
        (
            stock[
                "target_readiness_consistent"
            ].astype(int)
            != 1
        ).sum()
    )

    stock_listing_consistency_failures = int(
        (
            stock[
                "listing_readiness_consistent"
            ].astype(int)
            != 1
        ).sum()
    )

    if price_consistency_failures:
        print(
            "FAIL: price readiness internal "
            f"inconsistencies="
            f"{price_consistency_failures}"
        )
        failures += 1

    if stock_target_consistency_failures:
        print(
            "FAIL: stock-target readiness "
            f"inconsistencies="
            f"{stock_target_consistency_failures}"
        )
        failures += 1

    if stock_listing_consistency_failures:
        print(
            "FAIL: active-listing readiness "
            f"inconsistencies="
            f"{stock_listing_consistency_failures}"
        )
        failures += 1

    matrix_stage_disagreements = int(
        (
            joined[
                "market_maturity"
            ].astype(str)
            != joined[
                "matrix_stage"
            ].astype(str)
        ).sum()
    )

    if matrix_stage_disagreements:
        print(
            "FAIL: transition matrix-stage "
            f"disagreements="
            f"{matrix_stage_disagreements}"
        )
        failures += 1

    unsupported_recorded = 0
    invalid_stage_rows = 0

    for _, row in joined.iterrows():
        matrix_stage = clean_text(
            row[
                "market_maturity"
            ]
        )

        recorded_stage = clean_text(
            row[
                "recorded_stage"
            ]
        )

        if (
            matrix_stage
            not in STAGE_RANK
            or recorded_stage
            not in STAGE_RANK
        ):
            invalid_stage_rows += 1
            continue

        if (
            STAGE_RANK[
                recorded_stage
            ]
            > STAGE_RANK[
                matrix_stage
            ]
        ):
            unsupported_recorded += 1

    if invalid_stage_rows:
        print(
            "FAIL: invalid maturity stage "
            f"rows={invalid_stage_rows}"
        )
        failures += 1

    if unsupported_recorded:
        print(
            "FAIL: recorded stage exceeds "
            f"matrix support for "
            f"{unsupported_recorded} forms."
        )
        failures += 1

    transition_supported_failures = int(
        (
            transition[
                "recorded_stage_supported"
            ].astype(int)
            != 1
        ).sum()
    )

    if transition_supported_failures:
        print(
            "FAIL: transition policy reports "
            f"{transition_supported_failures} "
            "unsupported recorded stages."
        )
        failures += 1

    # A promotion is allowed to move only one rung
    # per evaluation. Demotion may fall multiple
    # stages immediately.
    promotion_rows = transition[
        transition[
            "transition_action"
        ]
        == "PROMOTE_ONE_STAGE"
    ]

    invalid_promotions = 0

    for _, row in promotion_rows.iterrows():
        matrix_stage = clean_text(
            row[
                "matrix_stage"
            ]
        )

        recorded_stage = clean_text(
            row[
                "recorded_stage"
            ]
        )

        if (
            matrix_stage
            not in STAGE_RANK
            or recorded_stage
            not in STAGE_RANK
        ):
            invalid_promotions += 1
            continue

        if (
            STAGE_RANK[
                recorded_stage
            ]
            > STAGE_RANK[
                matrix_stage
            ]
        ):
            invalid_promotions += 1

    if invalid_promotions:
        print(
            "FAIL: invalid promotion rows="
            f"{invalid_promotions}"
        )
        failures += 1

    # All authority remains hard-disabled.
    authority_checks = (
        (
            "matrix",
            matrix_summary,
            (
                "price_change_authorized",
                "stock_change_authorized",
                "active_listing_change_authorized",
                "activation_ready",
                "auto_live_promotions",
            ),
        ),
        (
            "price",
            price_summary,
            (
                "price_signal_use_authorized",
                "price_change_authorized",
                "activation_ready",
                "auto_live_promotions",
            ),
        ),
        (
            "stock",
            stock_summary,
            (
                "stock_signal_use_authorized",
                "stock_target_change_authorized",
                "active_listing_change_authorized",
                "activation_ready",
                "auto_live_promotions",
            ),
        ),
        (
            "transition",
            transition_summary,
            (
                "promotion_effective_for_pricing",
                "promotion_effective_for_stock",
                "price_change_authorized",
                "stock_change_authorized",
                "active_listing_change_authorized",
                "activation_ready",
                "auto_live_promotions",
            ),
        ),
    )

    authority_failures = 0

    for (
        name,
        summary,
        fields,
    ) in authority_checks:
        for field in fields:
            if as_int(
                summary.get(
                    field,
                    0,
                )
            ) != 0:
                print(
                    f"FAIL: {name} has "
                    f"{field}="
                    f"{summary.get(field)}"
                )

                authority_failures += 1

    failures += authority_failures

    seller_unchanged = int(
        seller_before
        == seller_after
    )

    buyer_unchanged = int(
        buyer_before
        == buyer_after
    )

    if not seller_unchanged:
        print(
            "FAIL: seller runtime changed."
        )
        failures += 1

    if not buyer_unchanged:
        print(
            "FAIL: buyer runtime changed."
        )
        failures += 1

    maturity_counts = {
        stage: int(
            (
                matrix[
                    "market_maturity"
                ]
                == stage
            ).sum()
        )
        for stage in STAGES
    }

    recorded_counts = {
        stage: int(
            (
                transition[
                    "recorded_stage"
                ]
                == stage
            ).sum()
        )
        for stage in STAGES
    }

    action_counts = (
        transition[
            "transition_action"
        ]
        .value_counts()
        .sort_index()
        .to_dict()
    )

    price_review_forms = int(
        price[
            "price_signal_review_eligible"
        ].sum()
    )

    stock_review_forms = int(
        stock[
            "stock_target_signal_review_eligible"
        ].sum()
    )

    listing_review_forms = int(
        stock[
            "active_listing_review_eligible"
        ].sum()
    )

    summary = {
        "status":
            (
                "PASS"
                if failures == 0
                else "FAIL"
            ),

        "stage":
            "2C.5_END_TO_END_MATURITY_VALIDATION",

        "item_forms":
            expected_forms,

        "supply_snapshots":
            as_int(
                matrix_summary.get(
                    "supply_snapshot_count"
                )
            ),

        "matrix_maturity_counts":
            maturity_counts,

        "recorded_maturity_counts":
            recorded_counts,

        "transition_action_counts": {
            str(key): int(value)
            for key, value
            in action_counts.items()
        },

        "price_signal_review_eligible_forms":
            price_review_forms,

        "stock_target_signal_review_eligible_forms":
            stock_review_forms,

        "active_listing_review_eligible_forms":
            listing_review_forms,

        "price_readiness_disagreements":
            price_disagreements,

        "stock_readiness_disagreements":
            stock_disagreements,

        "unsupported_recorded_stages":
            unsupported_recorded,

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
            failures,

        "price_signal_use_authorized":
            0,

        "stock_signal_use_authorized":
            0,

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
    }

    VALIDATION_SUMMARY.write_text(
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
        " FINAL 2C.5 MATURITY VALIDATION"
    )
    print("=" * 84)

    print()
    print(
        f"Item forms:                      "
        f"{expected_forms:>6}"
    )

    print(
        f"Supply snapshots:                "
        f"{summary['supply_snapshots']:>6}"
    )

    print()
    print("Matrix maturity:")

    for stage in STAGES:
        print(
            f"  {stage:<24}"
            f"{maturity_counts[stage]:>6}"
        )

    print()
    print("Recorded maturity:")

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
        f"Price-signal review eligible:    "
        f"{price_review_forms:>6}"
    )

    print(
        f"Stock-target review eligible:    "
        f"{stock_review_forms:>6}"
    )

    print(
        f"Active-listing review eligible:  "
        f"{listing_review_forms:>6}"
    )

    print()
    print(
        f"Price readiness disagreements:   "
        f"{price_disagreements:>6}"
    )

    print(
        f"Stock readiness disagreements:   "
        f"{stock_disagreements:>6}"
    )

    print(
        f"Unsupported recorded stages:     "
        f"{unsupported_recorded:>6}"
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
        f"{failures:>6}"
    )

    print()
    print(
        "Price signal use authorized:        0"
    )

    print(
        "Stock signal use authorized:        0"
    )

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
        f"Summary: {VALIDATION_SUMMARY}"
    )

    print()

    if failures:
        print("FAIL")

        raise MaturityValidationError(
            "2C.5 maturity validation failed."
        )

    print("PASS")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
