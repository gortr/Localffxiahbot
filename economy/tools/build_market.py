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

SELL_RUNTIME = GENERATED / "market-sell.csv"
BUY_RUNTIME = GENERATED / "market-buy.csv"

CUTOVER_SUMMARY = REPORTS / "unified-builder-cutover-summary.json"

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
        "compiled_seller_rows": EXPECTED_SELL,
        "compiled_buyer_rows": EXPECTED_BUY,
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


def validate_runtime_outputs() -> None:
    for path, expected in [
        (
            SELL_CUTOVER,
            EXPECTED_SELL,
        ),
        (
            BUY_CUTOVER,
            EXPECTED_BUY,
        ),
    ]:
        if not path.exists():
            raise UnifiedBuildError(
                f"Missing compiled output: {path}"
            )

        rows = csv_row_count(
            path
        )

        if rows != expected:
            raise UnifiedBuildError(
                f"{path.name}: expected {expected} rows, "
                f"found {rows}"
            )


def publish_runtime_outputs() -> None:
    validate_runtime_outputs()

    atomic_copy(
        SELL_CUTOVER,
        SELL_RUNTIME,
    )

    atomic_copy(
        BUY_CUTOVER,
        BUY_RUNTIME,
    )

    if csv_row_count(
        SELL_RUNTIME
    ) != EXPECTED_SELL:
        raise UnifiedBuildError(
            "Published seller runtime row count changed."
        )

    if csv_row_count(
        BUY_RUNTIME
    ) != EXPECTED_BUY:
        raise UnifiedBuildError(
            "Published buyer runtime row count changed."
        )

    print()
    print("Published unified runtime files:")
    print(f"  seller: {SELL_RUNTIME}")
    print(f"  buyer:  {BUY_RUNTIME}")


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
        TOOLS / "build_unified_seller_buyer_policy.py",
        TOOLS / "build_economy_maturity_policy.py",
        TOOLS / "build_unified_market_cutover.py",
    ]

    for script in pipeline:
        run_python(
            script
        )

    summary = validate_cutover_summary()

    if not args.validate_only:
        publish_runtime_outputs()

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
        f"Live sellers:           "
        f"{summary['compiled_seller_rows']}"
    )
    print(
        f"Live buyers:            "
        f"{summary['compiled_buyer_rows']}"
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
