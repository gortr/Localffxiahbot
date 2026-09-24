from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


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

CENSUS_TOOL = (
    TOOLS
    / "build_active_supply_census.py"
)

CENSUS_CSV = (
    REPORTS
    / "active-supply-census.csv"
)

CENSUS_SUMMARY = (
    REPORTS
    / "active-supply-census-summary.json"
)

SELLER_RUNTIME = (
    GENERATED
    / "market-sell.csv"
)

BUYER_RUNTIME = (
    GENERATED
    / "market-buy.csv"
)

INDEX_CSV = (
    OBSERVATION_DIR
    / "snapshot-index.csv"
)


class SupplySnapshotError(RuntimeError):
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


def main() -> int:
    if not CENSUS_TOOL.exists():
        raise SupplySnapshotError(
            f"Missing census tool: {CENSUS_TOOL}"
        )

    if not SELLER_RUNTIME.exists():
        raise SupplySnapshotError(
            f"Missing seller runtime: {SELLER_RUNTIME}"
        )

    if not BUYER_RUNTIME.exists():
        raise SupplySnapshotError(
            f"Missing buyer runtime: {BUYER_RUNTIME}"
        )

    seller_before = sha256(
        SELLER_RUNTIME
    )

    buyer_before = sha256(
        BUYER_RUNTIME
    )

    subprocess.run(
        [
            sys.executable,
            str(CENSUS_TOOL),
        ],
        cwd=ROOT,
        check=True,
    )

    seller_after = sha256(
        SELLER_RUNTIME
    )

    buyer_after = sha256(
        BUYER_RUNTIME
    )

    if seller_before != seller_after:
        raise SupplySnapshotError(
            "Seller runtime changed while "
            "building census."
        )

    if buyer_before != buyer_after:
        raise SupplySnapshotError(
            "Buyer runtime changed while "
            "building census."
        )

    if not CENSUS_CSV.exists():
        raise SupplySnapshotError(
            "Census CSV was not generated."
        )

    if not CENSUS_SUMMARY.exists():
        raise SupplySnapshotError(
            "Census summary was not generated."
        )

    summary = json.loads(
        CENSUS_SUMMARY.read_text(
            encoding="utf-8",
        )
    )

    if summary.get("status") != "PASS":
        raise SupplySnapshotError(
            "2B.1 census is not PASS."
        )

    if summary.get(
        "integrity_failures"
    ) != 0:
        raise SupplySnapshotError(
            "2B.1 census contains "
            "integrity failures."
        )

    for field in (
        "stock_change_authorized",
        "history_stock_influence_ready",
        "activation_ready",
        "auto_live_promotions",
    ):
        if summary.get(field) != 0:
            raise SupplySnapshotError(
                f"Unsafe census state: "
                f"{field}={summary.get(field)}"
            )

    census = pd.read_csv(
        CENSUS_CSV
    )

    expected_rows = int(
        summary[
            "configured_item_forms"
        ]
    )

    if len(census) != expected_rows:
        raise SupplySnapshotError(
            "Census row count does not "
            "match summary."
        )

    now = datetime.now(
        timezone.utc
    )

    snapshot_id = (
        now.strftime(
            "%Y%m%dT%H%M%S"
        )
        + f"{now.microsecond:06d}Z"
    )

    observed_at = (
        now.isoformat()
    )

    census.insert(
        0,
        "snapshot_id",
        snapshot_id,
    )

    census.insert(
        1,
        "observed_at_utc",
        observed_at,
    )

    census["seller_runtime_sha256"] = (
        seller_after
    )

    census["buyer_runtime_sha256"] = (
        buyer_after
    )

    # Permanent safety firewall.
    census["stock_change_authorized"] = 0
    census["activation_ready"] = 0
    census["auto_live_promotion"] = 0

    OBSERVATION_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    snapshot_path = (
        OBSERVATION_DIR
        / f"{snapshot_id}.csv.gz"
    )

    census.to_csv(
        snapshot_path,
        index=False,
        compression="gzip",
    )

    index_row = pd.DataFrame([
        {
            "snapshot_id":
                snapshot_id,

            "observed_at_utc":
                observed_at,

            "snapshot_file":
                snapshot_path.name,

            "configured_item_forms":
                expected_rows,

            "global_active_auction_rows":
                int(
                    summary[
                        "global_active_auction_rows"
                    ]
                ),

            "global_ahbot_active_rows":
                int(
                    summary[
                        "global_ahbot_active_rows"
                    ]
                ),

            "global_player_active_rows":
                int(
                    summary[
                        "global_player_active_rows"
                    ]
                ),

            "global_unknown_active_rows":
                int(
                    summary[
                        "global_unknown_active_rows"
                    ]
                ),

            "seller_runtime_sha256":
                seller_after,

            "buyer_runtime_sha256":
                buyer_after,

            "stock_change_authorized":
                0,

            "activation_ready":
                0,

            "auto_live_promotion":
                0,
        }
    ])

    if INDEX_CSV.exists():
        previous = pd.read_csv(
            INDEX_CSV
        )

        if snapshot_id in set(
            previous[
                "snapshot_id"
            ].astype(str)
        ):
            raise SupplySnapshotError(
                "Duplicate snapshot id."
            )

        index = pd.concat(
            [
                previous,
                index_row,
            ],
            ignore_index=True,
        )
    else:
        index = index_row

    index.to_csv(
        INDEX_CSV,
        index=False,
    )

    print()
    print("=" * 84)
    print(
        " Phase 2B.2 Active Supply Snapshot"
    )
    print("=" * 84)

    print()
    print(
        f"Snapshot ID:                    "
        f"{snapshot_id}"
    )

    print(
        f"Configured forms:               "
        f"{len(census):>6}"
    )

    print(
        f"Active listings:                "
        f"{summary['global_active_auction_rows']:>6}"
    )

    print(
        f"AHBot listings:                 "
        f"{summary['global_ahbot_active_rows']:>6}"
    )

    print(
        f"Player listings:                "
        f"{summary['global_player_active_rows']:>6}"
    )

    print(
        f"Unknown listings:               "
        f"{summary['global_unknown_active_rows']:>6}"
    )

    print()
    print(
        f"Seller runtime unchanged:       "
        f"{int(seller_before == seller_after):>6}"
    )

    print(
        f"Buyer runtime unchanged:        "
        f"{int(buyer_before == buyer_after):>6}"
    )

    print()
    print(
        "Stock change authorized:            0"
    )

    print(
        "Activation ready:                   0"
    )

    print(
        "Auto live promotions:               0"
    )

    print()
    print(
        f"Snapshot: {snapshot_path}"
    )

    print(
        f"Index:    {INDEX_CSV}"
    )

    print()
    print("PASS")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
