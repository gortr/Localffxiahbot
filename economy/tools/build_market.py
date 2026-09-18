from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


ROOT = Path.home() / "ffxiahbot"
TOOLS = ROOT / "economy" / "tools"
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"
BASELINES = ROOT / "economy" / "baselines"

LEGACY_BUILDER = TOOLS / "build_market_legacy.py"

SELL_PHASE0 = GENERATED / "market-sell-phase0.csv"
BUY_PHASE0 = GENERATED / "market-buy-phase0.csv"

SELL_CUTOVER = GENERATED / "market-sell-unified-cutover.csv"
BUY_CUTOVER = GENERATED / "market-buy-unified-cutover.csv"

SELL_PRICING_CUTOVER = GENERATED / "market-sell-pricing-cutover.csv"
BUY_PRICING_CUTOVER = GENERATED / "market-buy-pricing-cutover.csv"

SELLER_TRANSITION_CUTOVER = (
    GENERATED
    / "market-sell-seller-transition-cutover.csv"
)

SELL_RUNTIME = GENERATED / "market-sell.csv"
BUY_RUNTIME = GENERATED / "market-buy.csv"

RUNTIME_BACKUPS = ROOT / "economy" / "runtime-backups"

CUTOVER_SUMMARY = REPORTS / "unified-builder-cutover-summary.json"
PRICING_CUTOVER_SUMMARY = REPORTS / "safe-pricing-cutover-summary.json"

SELLER_TRANSITION_SUMMARY = (
    REPORTS
    / "safe-seller-transition-summary.json"
)

SELLER_CUTOVER_SUMMARY = (
    REPORTS
    / "safe-seller-cutover-summary.json"
)
OVERRIDE_SUMMARY = REPORTS / "candidate-policy-override-summary.json"

EFFECTIVE_SELL = GENERATED / "market-sell-effective-baseline.csv"
EFFECTIVE_BUY = GENERATED / "market-buy-effective-baseline.csv"

FROZEN_SELL = BASELINES / "market-sell-seed-v1.csv"
FROZEN_BUY = BASELINES / "market-buy-seed-v1.csv"
FROZEN_MANIFEST = BASELINES / "seed-v1-manifest.json"

EXPECTED_SELL = 167
EXPECTED_BUY = 159


class UnifiedBuildError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def run_python(script: Path, args: Sequence[str] = ()) -> None:
    if not script.exists():
        raise UnifiedBuildError(
            f"Required tool does not exist: {script}"
        )

    command = [
        sys.executable,
        str(script),
        *args,
    ]

    print()
    print("=" * 72)
    print(f"RUN: {' '.join(command)}")
    print("=" * 72)

    subprocess.run(
        command,
        cwd=ROOT,
        check=True,
    )


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    ) as temp:
        temp_path = Path(temp.name)

    try:
        shutil.copy2(
            source,
            temp_path,
        )
        temp_path.replace(
            destination
        )
    except Exception:
        temp_path.unlink(
            missing_ok=True
        )
        raise


def csv_row_count(path: Path) -> int:
    with path.open(
        "r",
        encoding="utf-8",
        errors="replace",
    ) as handle:
        lines = sum(
            1 for _ in handle
        )

    return max(
        0,
        lines - 1,
    )


def freeze_seed_baseline() -> None:
    BASELINES.mkdir(
        parents=True,
        exist_ok=True,
    )

    if (
        FROZEN_SELL.exists()
        or FROZEN_BUY.exists()
        or FROZEN_MANIFEST.exists()
    ):
        if not (
            FROZEN_SELL.exists()
            and FROZEN_BUY.exists()
            and FROZEN_MANIFEST.exists()
        ):
            raise UnifiedBuildError(
                "Seed-v1 baseline is partially present. "
                "Refusing to overwrite it."
            )

        return

    if not SELL_PHASE0.exists():
        raise UnifiedBuildError(
            f"Missing seed seller baseline: {SELL_PHASE0}"
        )

    if not BUY_PHASE0.exists():
        raise UnifiedBuildError(
            f"Missing seed buyer baseline: {BUY_PHASE0}"
        )

    if csv_row_count(
        SELL_PHASE0
    ) != EXPECTED_SELL:
        raise UnifiedBuildError(
            "Cannot freeze seller baseline: "
            f"expected {EXPECTED_SELL} rows."
        )

    if csv_row_count(
        BUY_PHASE0
    ) != EXPECTED_BUY:
        raise UnifiedBuildError(
            "Cannot freeze buyer baseline: "
            f"expected {EXPECTED_BUY} rows."
        )

    atomic_copy(
        SELL_PHASE0,
        FROZEN_SELL,
    )

    atomic_copy(
        BUY_PHASE0,
        FROZEN_BUY,
    )

    manifest = {
        "baseline": "SEED_V1",
        "created_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),
        "seller_rows":
            EXPECTED_SELL,
        "buyer_rows":
            EXPECTED_BUY,
        "seller_sha256":
            sha256(
                FROZEN_SELL
            ),
        "buyer_sha256":
            sha256(
                FROZEN_BUY
            ),
        "source_seller":
            str(
                SELL_PHASE0
            ),
        "source_buyer":
            str(
                BUY_PHASE0
            ),
        "notes": (
            "Immutable pre-unified-cutover runtime baseline. "
            "Do not regenerate or overwrite."
        ),
    }

    FROZEN_MANIFEST.write_text(
        json.dumps(
            manifest,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print("Frozen immutable SEED_V1 baseline:")
    print(f"  {FROZEN_SELL}")
    print(f"  {FROZEN_BUY}")
    print(f"  {FROZEN_MANIFEST}")


def validate_override_summary() -> dict:
    if not OVERRIDE_SUMMARY.exists():
        raise UnifiedBuildError(
            f"Missing override summary: {OVERRIDE_SUMMARY}"
        )

    if not EFFECTIVE_SELL.exists():
        raise UnifiedBuildError(
            f"Missing effective seller baseline: {EFFECTIVE_SELL}"
        )

    if not EFFECTIVE_BUY.exists():
        raise UnifiedBuildError(
            f"Missing effective buyer baseline: {EFFECTIVE_BUY}"
        )

    summary = json.loads(
        OVERRIDE_SUMMARY.read_text(
            encoding="utf-8"
        )
    )

    effective_sell_rows = csv_row_count(
        EFFECTIVE_SELL
    )

    effective_buy_rows = csv_row_count(
        EFFECTIVE_BUY
    )

    required = {
        "status": "PASS",
        "raw_seller_baseline": EXPECTED_SELL,
        "raw_buyer_baseline": EXPECTED_BUY,
        "effective_seller_baseline":
            effective_sell_rows,
        "effective_buyer_baseline":
            effective_buy_rows,
        "activation_ready": 0,
        "auto_live_promotions": 0,
    }

    failures = []

    for key, expected in required.items():
        actual = summary.get(
            key
        )

        if actual != expected:
            failures.append(
                f"{key}: expected={expected!r}, "
                f"actual={actual!r}"
            )

    if failures:
        raise UnifiedBuildError(
            "Candidate-policy override validation "
            "did not pass:\n  "
            + "\n  ".join(
                failures
            )
        )

    return summary


def validate_cutover_summary() -> dict:
    if not CUTOVER_SUMMARY.exists():
        raise UnifiedBuildError(
            f"Missing cutover summary: {CUTOVER_SUMMARY}"
        )

    summary = json.loads(
        CUTOVER_SUMMARY.read_text(
            encoding="utf-8"
        )
    )

    required = {
        "status": "PASS",
        "compiled_seller_rows": csv_row_count(EFFECTIVE_SELL),
        "compiled_buyer_rows": csv_row_count(EFFECTIVE_BUY),
        "seller_semantic_match": True,
        "buyer_semantic_match": True,
        "hard_audit_errors": 0,
        "auto_live_promotions": 0,
    }

    failures = []

    for key, expected in required.items():
        actual = summary.get(
            key
        )

        if actual != expected:
            failures.append(
                f"{key}: expected {expected!r}, "
                f"got {actual!r}"
            )

    if failures:
        raise UnifiedBuildError(
            "Unified cutover validation did not pass:\n  "
            + "\n  ".join(
                failures
            )
        )

    return summary


def validate_pricing_cutover_summary() -> dict:
    if not PRICING_CUTOVER_SUMMARY.exists():
        raise UnifiedBuildError(
            "Missing safe-pricing cutover summary: "
            f"{PRICING_CUTOVER_SUMMARY}"
        )

    summary = json.loads(
        PRICING_CUTOVER_SUMMARY.read_text(
            encoding="utf-8"
        )
    )

    failures = []

    expected = {
        "status": "PASS",
        "runtime_seller_rows":
            csv_row_count(SELL_CUTOVER),
        "runtime_buyer_rows":
            csv_row_count(BUY_CUTOVER),
        "cutover_seller_rows":
            csv_row_count(SELL_PRICING_CUTOVER),
        "cutover_buyer_rows":
            csv_row_count(BUY_PRICING_CUTOVER),
        "seller_additions": 0,
        "seller_removals": 0,
        "seller_price_changes": 0,
        "buyer_additions": 0,
        "residual_single_craft_violations": 0,
        "residual_stack_craft_violations": 0,
        "buyer_ge_seller_violations": 0,
        "history_price_influence_ready": 0,
        "history_stock_influence_ready": 0,
        "activation_ready": 0,
        "auto_live_promotions": 0,
        "deployment_change_authorized": 0,
    }

    for key, expected_value in expected.items():
        actual = summary.get(key)

        if actual != expected_value:
            failures.append(
                f"{key}: expected {expected_value!r}, "
                f"got {actual!r}"
            )

    try:
        source_buyers = int(
            summary["runtime_buyer_rows"]
        )
        final_buyers = int(
            summary["cutover_buyer_rows"]
        )
        deferred = int(
            summary["buyer_deferred_rows"]
        )
        capped = int(
            summary["buyer_capped_rows"]
        )
        unchanged = int(
            summary["buyer_unchanged_rows"]
        )
    except (KeyError, TypeError, ValueError):
        failures.append(
            "Buyer cutover accounting fields are missing "
            "or invalid."
        )
    else:
        if final_buyers != source_buyers - deferred:
            failures.append(
                "Final buyer count does not equal source "
                "buyers minus deferred buyers."
            )

        if unchanged + capped + deferred != source_buyers:
            failures.append(
                "Buyer cutover action counts do not account "
                "for every source buyer."
            )

    if failures:
        raise UnifiedBuildError(
            "Safe-pricing cutover validation did not pass:\n  "
            + "\n  ".join(failures)
        )

    return summary


def validate_seller_transition_summary() -> dict:
    if not SELLER_TRANSITION_SUMMARY.exists():
        raise UnifiedBuildError(
            "Missing safe seller transition summary: "
            f"{SELLER_TRANSITION_SUMMARY}"
        )

    summary = json.loads(
        SELLER_TRANSITION_SUMMARY.read_text(
            encoding="utf-8"
        )
    )

    required = {
        "status":
            "PASS",

        "stage":
            "1C.9c_SAFE_SELLER_TRANSITION_AUDIT",

        "live_seller_rows":
            csv_row_count(
                SELL_PRICING_CUTOVER
            ),

        "safe_transition_items":
            3,

        "safe_transition_itemids":
            [744, 1634, 8740],

        "total_safety_failures":
            0,

        "history_price_influence_ready":
            0,

        "history_stock_influence_ready":
            0,

        "deployment_change_authorized":
            0,

        "activation_ready":
            0,

        "auto_live_promotions":
            0,
    }

    failures = []

    for key, expected in required.items():
        actual = summary.get(key)

        if actual != expected:
            failures.append(
                f"{key}: expected={expected!r}, "
                f"actual={actual!r}"
            )

    safety = summary.get(
        "safety_failures",
        {}
    )

    if not isinstance(
        safety,
        dict,
    ):
        failures.append(
            "safety_failures is not a dict."
        )
    else:
        nonzero = {
            key: value
            for key, value in safety.items()
            if int(value) != 0
        }

        if nonzero:
            failures.append(
                "Seller transition safety "
                f"failures present: {nonzero}"
            )

    expected_targets = {
        "744": {
            "single": 351,
            "stack": 4201,
        },
        "1634": {
            "single": 601,
            "stack": 7201,
        },
        "8740": {
            "single": 249,
            "stack": 2989,
        },
    }

    actual_targets = summary.get(
        "safe_transition_targets",
        {}
    )

    if actual_targets != expected_targets:
        failures.append(
            "safe_transition_targets changed: "
            f"expected={expected_targets!r}, "
            f"actual={actual_targets!r}"
        )

    if failures:
        raise UnifiedBuildError(
            "Safe seller transition validation "
            "did not pass:\n  "
            + "\n  ".join(failures)
        )

    return summary


def validate_seller_cutover_summary(
    pricing_summary: dict,
    transition_summary: dict,
) -> dict:
    if not SELLER_CUTOVER_SUMMARY.exists():
        raise UnifiedBuildError(
            "Missing safe seller cutover summary: "
            f"{SELLER_CUTOVER_SUMMARY}"
        )

    if not SELLER_TRANSITION_CUTOVER.exists():
        raise UnifiedBuildError(
            "Missing safe seller cutover candidate: "
            f"{SELLER_TRANSITION_CUTOVER}"
        )

    summary = json.loads(
        SELLER_CUTOVER_SUMMARY.read_text(
            encoding="utf-8"
        )
    )

    required = {
        "status":
            "PASS",

        "stage":
            "1C.9d_SAFE_SELLER_CUTOVER",

        "source_seller_rows":
            int(
                pricing_summary[
                    "cutover_seller_rows"
                ]
            ),

        "cutover_seller_rows":
            csv_row_count(
                SELLER_TRANSITION_CUTOVER
            ),

        "seller_additions":
            0,

        "seller_removals":
            0,

        "seller_changed_rows":
            3,

        "seller_changed_cells":
            6,

        "seller_changed_itemids":
            [744, 1634, 8740],

        "candidate_differs_from_source":
            1,

        "history_price_influence_ready":
            0,

        "history_stock_influence_ready":
            0,

        "deployment_change_authorized":
            0,

        "activation_ready":
            0,

        "auto_live_promotions":
            0,

        "candidate_ready":
            1,
    }

    failures = []

    for key, expected in required.items():
        actual = summary.get(key)

        if actual != expected:
            failures.append(
                f"{key}: expected={expected!r}, "
                f"actual={actual!r}"
            )

    if (
        int(
            transition_summary[
                "live_seller_rows"
            ]
        )
        != int(
            summary[
                "source_seller_rows"
            ]
        )
    ):
        failures.append(
            "1C.9c/1C.9d seller row counts "
            "do not agree."
        )

    source_hash = sha256(
        SELL_PRICING_CUTOVER
    )

    candidate_hash = sha256(
        SELLER_TRANSITION_CUTOVER
    )

    if (
        summary.get(
            "source_sha256"
        )
        != source_hash
    ):
        failures.append(
            "1C.9d source SHA256 does not "
            "match pricing-safe seller candidate."
        )

    if (
        summary.get(
            "candidate_sha256"
        )
        != candidate_hash
    ):
        failures.append(
            "1C.9d candidate SHA256 does not "
            "match seller-transition candidate."
        )

    if (
        csv_row_count(
            SELL_PRICING_CUTOVER
        )
        != csv_row_count(
            SELLER_TRANSITION_CUTOVER
        )
    ):
        failures.append(
            "Seller transition changed seller "
            "row count."
        )

    if failures:
        raise UnifiedBuildError(
            "Safe seller cutover validation "
            "did not pass:\n  "
            + "\n  ".join(failures)
        )

    return summary


def validate_runtime_outputs(
    pricing_summary: dict,
    seller_summary: dict,
) -> None:
    expected = [
        (
            SELLER_TRANSITION_CUTOVER,
            int(
                seller_summary[
                    "cutover_seller_rows"
                ]
            ),
        ),
        (
            BUY_PRICING_CUTOVER,
            int(
                pricing_summary[
                    "cutover_buyer_rows"
                ]
            ),
        ),
    ]

    for path, expected_rows in expected:
        if not path.exists():
            raise UnifiedBuildError(
                f"Missing pricing-safe output: {path}"
            )

        rows = csv_row_count(path)

        if rows != expected_rows:
            raise UnifiedBuildError(
                f"{path.name}: expected "
                f"{expected_rows} rows, found {rows}"
            )


def publish_runtime_outputs(
    pricing_summary: dict,
    seller_summary: dict,
) -> None:
    validate_runtime_outputs(
        pricing_summary,
        seller_summary,
    )

    if not SELL_RUNTIME.exists():
        raise UnifiedBuildError(
            f"Missing current seller runtime: {SELL_RUNTIME}"
        )

    if not BUY_RUNTIME.exists():
        raise UnifiedBuildError(
            f"Missing current buyer runtime: {BUY_RUNTIME}"
        )

    target_sell_hash = sha256(
        SELLER_TRANSITION_CUTOVER
    )
    target_buy_hash = sha256(
        BUY_PRICING_CUTOVER
    )

    current_sell_hash = sha256(
        SELL_RUNTIME
    )
    current_buy_hash = sha256(
        BUY_RUNTIME
    )

    if (
        current_sell_hash == target_sell_hash
        and current_buy_hash == target_buy_hash
    ):
        print()
        print(
            "Runtime outputs already match "
            "final safe market cutover."
        )
        return

    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%S.%fZ"
    )

    backup_dir = (
        RUNTIME_BACKUPS
        / timestamp
    )

    backup_dir.mkdir(
        parents=True,
        exist_ok=False,
    )

    backup_sell = (
        backup_dir
        / SELL_RUNTIME.name
    )

    backup_buy = (
        backup_dir
        / BUY_RUNTIME.name
    )

    shutil.copy2(
        SELL_RUNTIME,
        backup_sell,
    )

    shutil.copy2(
        BUY_RUNTIME,
        backup_buy,
    )

    try:
        atomic_copy(
            SELLER_TRANSITION_CUTOVER,
            SELL_RUNTIME,
        )

        atomic_copy(
            BUY_PRICING_CUTOVER,
            BUY_RUNTIME,
        )

        if (
            sha256(SELL_RUNTIME)
            != target_sell_hash
        ):
            raise UnifiedBuildError(
                "Published seller runtime hash "
                "does not match final seller source."
            )

        if (
            sha256(BUY_RUNTIME)
            != target_buy_hash
        ):
            raise UnifiedBuildError(
                "Published buyer runtime hash "
                "does not match pricing-safe source."
            )

        if csv_row_count(
            SELL_RUNTIME
        ) != int(
            seller_summary[
                "cutover_seller_rows"
            ]
        ):
            raise UnifiedBuildError(
                "Published seller runtime row "
                "count changed."
            )

        if csv_row_count(
            BUY_RUNTIME
        ) != int(
            pricing_summary[
                "cutover_buyer_rows"
            ]
        ):
            raise UnifiedBuildError(
                "Published buyer runtime row "
                "count changed."
            )

    except Exception as exc:
        try:
            atomic_copy(
                backup_sell,
                SELL_RUNTIME,
            )

            atomic_copy(
                backup_buy,
                BUY_RUNTIME,
            )
        except Exception as rollback_exc:
            raise UnifiedBuildError(
                "Pricing-safe runtime publication failed "
                "and automatic rollback also failed. "
                f"Backup directory: {backup_dir}"
            ) from rollback_exc

        raise UnifiedBuildError(
            "Pricing-safe runtime publication failed. "
            "Previous runtime files were restored."
        ) from exc

    print()
    print("Published pricing-safe runtime files:")
    print(f"  seller: {SELL_RUNTIME}")
    print(f"  buyer:  {BUY_RUNTIME}")
    print(f"  backup: {backup_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Unified AHBot economy build orchestrator. "
            "Runs the legacy SEED generator, canonical policy "
            "layers, cutover validation, and optional atomic "
            "runtime publication."
        )
    )

    parser.add_argument(
        "--validate-only",
        action="store_true",
        help=(
            "Run validation/policy stages without publishing "
            "market-sell.csv and market-buy.csv."
        ),
    )

    parser.add_argument(
        "--skip-legacy",
        action="store_true",
        help=(
            "Do not rerun build_market_legacy.py. Use only when "
            "the Phase-0 generated inputs are already current."
        ),
    )

    parser.add_argument(
        "--no-freeze-baseline",
        action="store_true",
        help=(
            "Do not create economy/baselines/SEED_V1 files. "
            "Normally leave this off."
        ),
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.no_freeze_baseline:
        freeze_seed_baseline()

    if not args.skip_legacy:
        legacy_args = (
            ["--validate-only"]
            if args.validate_only
            else []
        )

        run_python(
            LEGACY_BUILDER,
            legacy_args,
        )

    # Stable canonical layers. Audit/research discovery tools are
    # intentionally not rerun here; these consume the approved generated
    # policy sidecars produced during 1B.3-1B.5.
    pipeline = [
        TOOLS / "build_vendor_policy.py",
        TOOLS / "build_canonical_market_policy.py",
        TOOLS / "apply_candidate_policy_overrides.py",
        TOOLS / "build_unified_seller_buyer_policy.py",
        TOOLS / "build_economy_maturity_policy.py",
        TOOLS / "build_unified_market_cutover.py",
        TOOLS / "build_safe_pricing_cutover.py",
        TOOLS / "build_safe_seller_transition_audit.py",
        TOOLS / "build_safe_seller_cutover.py",
    ]

    for script in pipeline:
        run_python(
            script
        )

    validate_override_summary()
    summary = validate_cutover_summary()
    pricing_summary = validate_pricing_cutover_summary()

    transition_summary = (
        validate_seller_transition_summary()
    )

    seller_summary = (
        validate_seller_cutover_summary(
            pricing_summary,
            transition_summary,
        )
    )

    if not args.validate_only:
        publish_runtime_outputs(
            pricing_summary,
            seller_summary,
        )

    print()
    print("=" * 72)
    print(" UNIFIED MARKET BUILD COMPLETE")
    print("=" * 72)
    print(
        f"Status:                 "
        f"{summary['status']}"
    )
    print(
        f"Server maturity:        "
        f"{summary.get('server_maturity', 'UNKNOWN')}"
    )
    print(
        f"Final seller rows:      "
        f"{seller_summary['cutover_seller_rows']}"
    )
    print(
        f"Final buyer rows:       "
        f"{pricing_summary['cutover_buyer_rows']}"
    )
    print(
        f"Buyer craft caps:       "
        f"{pricing_summary['buyer_capped_rows']}"
    )
    print(
        f"Buyer deferrals:        "
        f"{pricing_summary['buyer_deferred_rows']}"
    )
    print(
        f"Seller normalizations: "
        f"{seller_summary['seller_changed_rows']:>6}"
    )
    print(
        f"Seller changed cells:  "
        f"{seller_summary['seller_changed_cells']:>6}"
    )
    print(
        f"Staged sellers:         "
        f"{summary.get('staged_sellers', 0)}"
    )
    print(
        f"Staged buyers:          "
        f"{summary.get('staged_buyers', 0)}"
    )
    print(
        f"Hard audit errors:      "
        f"{summary['hard_audit_errors']}"
    )
    print(
        f"Auto live promotions:   "
        f"{summary['auto_live_promotions']}"
    )

    if args.validate_only:
        print(
            "Runtime publication:    SKIPPED (--validate-only)"
        )
    else:
        print(
            "Runtime publication:    PASS"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
