from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path.home() / "ffxiahbot"
TOOLS = ROOT / "economy" / "tools"
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

OBSERVATION_DIR = (
    ROOT
    / "economy"
    / "runtime-observations"
    / "active-supply"
)

SNAPSHOT_INDEX = (
    OBSERVATION_DIR
    / "snapshot-index.csv"
)

SELLER_RUNTIME = (
    GENERATED
    / "market-sell.csv"
)

BUYER_RUNTIME = (
    GENERATED
    / "market-buy.csv"
)

HISTORY_VALIDATOR = (
    TOOLS
    / "validate_market_history_pipeline.py"
)

SNAPSHOT_RECORDER = (
    TOOLS
    / "record_active_supply_snapshot.py"
)

SUPPLY_VALIDATOR = (
    TOOLS
    / "validate_supply_normalization_pipeline.py"
)

MATURITY_VALIDATOR = (
    TOOLS
    / "validate_market_maturity_pipeline.py"
)

HISTORY_SUMMARY = (
    REPORTS
    / "market-history-validation-summary.json"
)

SUPPLY_SUMMARY = (
    REPORTS
    / "supply-normalization-validation-summary.json"
)

MATURITY_SUMMARY = (
    REPORTS
    / "market-maturity-validation-summary.json"
)

OUTPUT_SUMMARY = (
    REPORTS
    / "market-intelligence-refresh-summary.json"
)


class MarketIntelligenceRefreshError(RuntimeError):
    pass


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Refresh the complete read-only AHBot "
            "market intelligence pipeline."
        )
    )

    parser.add_argument(
        "--skip-snapshot",
        action="store_true",
        help=(
            "Refresh all intelligence without "
            "recording a new supply snapshot."
        ),
    )

    return parser.parse_args()


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


def load_json(
    path: Path,
) -> dict[str, Any]:
    if not path.exists():
        raise MarketIntelligenceRefreshError(
            f"Missing summary: {path}"
        )

    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


def count_snapshots() -> int:
    if not SNAPSHOT_INDEX.exists():
        return 0

    with SNAPSHOT_INDEX.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        reader = csv.DictReader(
            handle
        )

        return sum(
            1
            for _ in reader
        )


def run_stage(
    title: str,
    path: Path,
) -> None:
    if not path.exists():
        raise MarketIntelligenceRefreshError(
            f"Missing tool: {path}"
        )

    print()
    print("=" * 84)
    print(f" {title}")
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
        return int(value)
    except (TypeError, ValueError):
        return 0


def main() -> int:
    args = parse_args()

    for path in (
        SELLER_RUNTIME,
        BUYER_RUNTIME,
    ):
        if not path.exists():
            raise MarketIntelligenceRefreshError(
                f"Missing runtime file: {path}"
            )

    seller_before = sha256(
        SELLER_RUNTIME
    )

    buyer_before = sha256(
        BUYER_RUNTIME
    )

    snapshots_before = (
        count_snapshots()
    )

    started_at = datetime.now(
        timezone.utc
    )

    print()
    print("=" * 84)
    print(
        " Phase 2D.1 Unified Market "
        "Intelligence Refresh"
    )
    print("=" * 84)

    print()
    print(
        f"Snapshot mode:                  "
        f"{'SKIP' if args.skip_snapshot else 'RECORD'}"
    )

    print(
        f"Snapshots before:               "
        f"{snapshots_before:>6}"
    )

    print()
    print("Runtime hashes before:")

    print(
        f"  seller: {seller_before}"
    )

    print(
        f"  buyer:  {buyer_before}"
    )

    # -------------------------------------------------
    # 2A
    # -------------------------------------------------

    run_stage(
        "REFRESHING 2A MARKET HISTORY",
        HISTORY_VALIDATOR,
    )

    # -------------------------------------------------
    # Supply snapshot
    # -------------------------------------------------

    if not args.skip_snapshot:
        run_stage(
            "RECORDING ACTIVE SUPPLY SNAPSHOT",
            SNAPSHOT_RECORDER,
        )
    else:
        print()
        print("=" * 84)
        print(
            " ACTIVE SUPPLY SNAPSHOT SKIPPED"
        )
        print("=" * 84)

        print()
        print(
            "Existing observation history "
            "will be reused."
        )

    # -------------------------------------------------
    # 2B
    # -------------------------------------------------

    run_stage(
        "REFRESHING 2B SUPPLY INTELLIGENCE",
        SUPPLY_VALIDATOR,
    )

    # -------------------------------------------------
    # 2C
    # -------------------------------------------------

    run_stage(
        "REFRESHING 2C MARKET MATURITY",
        MATURITY_VALIDATOR,
    )

    # -------------------------------------------------
    # Final validation
    # -------------------------------------------------

    history = load_json(
        HISTORY_SUMMARY
    )

    supply = load_json(
        SUPPLY_SUMMARY
    )

    maturity = load_json(
        MATURITY_SUMMARY
    )

    seller_after = sha256(
        SELLER_RUNTIME
    )

    buyer_after = sha256(
        BUYER_RUNTIME
    )

    snapshots_after = (
        count_snapshots()
    )

    failures = 0

    for name, summary in (
        ("2A history", history),
        ("2B supply", supply),
        ("2C maturity", maturity),
    ):
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
                "integrity_failures",
                0,
            )
        ) != 0:
            print(
                f"FAIL: {name} contains "
                "integrity failures."
            )
            failures += 1

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

    if args.skip_snapshot:
        snapshot_delta_expected = 0
    else:
        snapshot_delta_expected = 1

    snapshot_delta = (
        snapshots_after
        - snapshots_before
    )

    snapshot_count_valid = int(
        snapshot_delta
        == snapshot_delta_expected
    )

    if not snapshot_count_valid:
        print(
            "FAIL: unexpected supply snapshot "
            f"delta={snapshot_delta}; "
            f"expected={snapshot_delta_expected}"
        )
        failures += 1

    supply_snapshot_count = as_int(
        supply.get(
            "snapshots"
        )
    )

    maturity_snapshot_count = as_int(
        maturity.get(
            "supply_snapshots"
        )
    )

    supply_snapshot_count_matches = int(
        supply_snapshot_count
        == snapshots_after
    )

    maturity_snapshot_count_matches = int(
        maturity_snapshot_count
        == snapshots_after
    )

    if not supply_snapshot_count_matches:
        print(
            "FAIL: 2B snapshot count does "
            "not match observation index."
        )
        failures += 1

    if not maturity_snapshot_count_matches:
        print(
            "FAIL: 2C snapshot count does "
            "not match observation index."
        )
        failures += 1

    # Mutation authority must remain disabled.
    authority_checks = (
        (
            "2B",
            supply,
            (
                "deployment_change_authorized",
                "stock_change_authorized",
                "active_listing_change_authorized",
                "history_stock_influence_ready",
                "activation_ready",
                "auto_live_promotions",
            ),
        ),
        (
            "2C",
            maturity,
            (
                "price_signal_use_authorized",
                "stock_signal_use_authorized",
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

    finished_at = datetime.now(
        timezone.utc
    )

    duration_seconds = (
        finished_at
        - started_at
    ).total_seconds()

    final_summary = {
        "status":
            (
                "PASS"
                if failures == 0
                else "FAIL"
            ),

        "stage":
            "2D.1_UNIFIED_MARKET_INTELLIGENCE_REFRESH",

        "snapshot_mode":
            (
                "SKIP"
                if args.skip_snapshot
                else "RECORD"
            ),

        "snapshots_before":
            snapshots_before,

        "snapshots_after":
            snapshots_after,

        "snapshot_delta":
            snapshot_delta,

        "snapshot_delta_expected":
            snapshot_delta_expected,

        "snapshot_count_valid":
            snapshot_count_valid,

        "supply_snapshot_count_matches":
            supply_snapshot_count_matches,

        "maturity_snapshot_count_matches":
            maturity_snapshot_count_matches,

        "history_status":
            history.get(
                "status"
            ),

        "supply_status":
            supply.get(
                "status"
            ),

        "maturity_status":
            maturity.get(
                "status"
            ),

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

        "authority_failures":
            authority_failures,

        "integrity_failures":
            failures,

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

        "started_at_utc":
            started_at.isoformat(),

        "finished_at_utc":
            finished_at.isoformat(),

        "duration_seconds":
            round(
                duration_seconds,
                3,
            ),
    }

    OUTPUT_SUMMARY.write_text(
        json.dumps(
            final_summary,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 84)
    print(
        " FINAL MARKET INTELLIGENCE REFRESH"
    )
    print("=" * 84)

    print()
    print(
        f"2A history:                     "
        f"{history.get('status')}"
    )

    print(
        f"2B supply:                      "
        f"{supply.get('status')}"
    )

    print(
        f"2C maturity:                    "
        f"{maturity.get('status')}"
    )

    print()
    print(
        f"Snapshots before:               "
        f"{snapshots_before:>6}"
    )

    print(
        f"Snapshots after:                "
        f"{snapshots_after:>6}"
    )

    print(
        f"Snapshot delta:                 "
        f"{snapshot_delta:>6}"
    )

    print()
    print(
        f"Seller runtime unchanged:       "
        f"{seller_unchanged:>6}"
    )

    print(
        f"Buyer runtime unchanged:        "
        f"{buyer_unchanged:>6}"
    )

    print()
    print(
        f"Authority failures:             "
        f"{authority_failures:>6}"
    )

    print(
        f"Integrity failures:             "
        f"{failures:>6}"
    )

    print()
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
        f"Summary: {OUTPUT_SUMMARY}"
    )

    print()

    if failures:
        print("FAIL")

        raise MarketIntelligenceRefreshError(
            "Unified market-intelligence "
            "refresh failed."
        )

    print("PASS")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
