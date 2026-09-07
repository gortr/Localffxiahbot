from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd


# ============================================================
# Paths
# ============================================================

ROOT = Path.home() / "ffxiahbot"

TOOLS = ROOT / "economy" / "tools"
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

VENDOR_FILE = GENERATED / "vendor-items.csv"
MASTER_FILE = GENERATED / "master-market.csv"
SELLER_FILE = GENERATED / "market-sell-phase0.csv"
BUYER_FILE = GENERATED / "market-buy-phase0.csv"

SUMMARY_FILE = REPORTS / "market-build-summary.json"

BUILD_VENDOR = TOOLS / "build_vendor_index.py"
BUILD_SELLER = TOOLS / "build_phase0.py"
BUILD_BUYER = TOOLS / "build_buyer_phase0.py"


# Files that must survive a failed build unchanged.
PRODUCTION_FILES = [
    VENDOR_FILE,
    MASTER_FILE,
    SELLER_FILE,
    BUYER_FILE,
]


# ============================================================
# Helpers
# ============================================================

class MarketBuildError(RuntimeError):
    pass


def as_bool_series(series: pd.Series) -> pd.Series:
    """
    Normalize CSV boolean-ish values.

    Handles:
        True / False
        1 / 0
        yes / no
        strings produced by pandas CSV round-trips
    """

    return (
        series.astype(str)
        .str.strip()
        .str.lower()
        .isin(
            {
                "true",
                "1",
                "yes",
                "y",
            }
        )
    )


def require_columns(
    frame: pd.DataFrame,
    columns: set[str],
    label: str,
) -> None:
    missing = sorted(
        columns - set(frame.columns)
    )

    if missing:
        raise MarketBuildError(
            f"{label} is missing required columns: "
            + ", ".join(missing)
        )


def require_unique_itemids(
    frame: pd.DataFrame,
    label: str,
) -> None:
    duplicates = frame[
        frame["itemid"].duplicated(
            keep=False
        )
    ]

    if not duplicates.empty:
        ids = sorted(
            duplicates["itemid"]
            .astype(int)
            .unique()
            .tolist()
        )

        preview = ids[:20]

        raise MarketBuildError(
            f"{label} contains duplicate item IDs: "
            f"{preview}"
        )


def run_builder(
    script: Path,
    label: str,
) -> None:
    print()
    print(
        "======================================"
    )
    print(
        f" {label}"
    )
    print(
        "======================================"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script),
        ],
        cwd=ROOT,
        check=False,
    )

    if result.returncode != 0:
        raise MarketBuildError(
            f"{label} failed with exit code "
            f"{result.returncode}"
        )


# ============================================================
# Backup / rollback
# ============================================================

def snapshot_production_files() -> dict[Path, bytes | None]:
    """
    Save the current production outputs in memory.

    None means the file did not exist before this build.
    """

    snapshots: dict[
        Path,
        bytes | None,
    ] = {}

    for path in PRODUCTION_FILES:
        if path.exists():
            snapshots[path] = path.read_bytes()
        else:
            snapshots[path] = None

    return snapshots


def restore_production_files(
    snapshots: dict[Path, bytes | None],
) -> None:
    print()
    print(
        "Restoring previous production files..."
    )

    for path, contents in snapshots.items():
        if contents is None:
            if path.exists():
                path.unlink()

            continue

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        path.write_bytes(
            contents
        )


# ============================================================
# Load generated economy
# ============================================================

def load_outputs() -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    for path in [
        MASTER_FILE,
        SELLER_FILE,
        BUYER_FILE,
    ]:
        if not path.exists():
            raise MarketBuildError(
                f"Expected output was not generated: {path}"
            )

    master = pd.read_csv(
        MASTER_FILE
    )

    seller = pd.read_csv(
        SELLER_FILE
    )

    buyer = pd.read_csv(
        BUYER_FILE
    )

    return (
        master,
        seller,
        buyer,
    )


# ============================================================
# Safety audit
# ============================================================

def validate_market(
    master: pd.DataFrame,
    seller: pd.DataFrame,
    buyer: pd.DataFrame,
) -> dict:
    """
    Validate the relationship between the master market,
    seller catalog, and buyer catalog.

    Any failure prevents the newly generated production
    files from replacing the previously working economy.
    """

    master_required = {
        "itemid",
        "name",
        "market_class",
        "allowed",
        "vendor_item",
        "base_sell",
        "stack_size",
        "provenance",
        "rejection_reason",
    }

    market_required = {
        "itemid",
        "name",
        "sell_single",
        "buy_single",
        "price_single",
        "stock_single",
        "buy_rate_single",
        "sell_rate_single",
        "sell_stacks",
        "buy_stacks",
        "price_stacks",
        "stock_stacks",
        "buy_rate_stacks",
        "sell_rate_stacks",
    }

    require_columns(
        master,
        master_required,
        "master-market.csv",
    )

    require_columns(
        seller,
        market_required,
        "market-sell-phase0.csv",
    )

    require_columns(
        buyer,
        market_required,
        "market-buy-phase0.csv",
    )

    require_unique_itemids(
        master,
        "master-market.csv",
    )

    require_unique_itemids(
        seller,
        "market-sell-phase0.csv",
    )

    require_unique_itemids(
        buyer,
        "market-buy-phase0.csv",
    )

    if master.empty:
        raise MarketBuildError(
            "Master market is empty."
        )

    if seller.empty:
        raise MarketBuildError(
            "Seller catalog is empty."
        )

    if buyer.empty:
        raise MarketBuildError(
            "Buyer catalog is empty."
        )

    # --------------------------------------------------------
    # Master approval
    # --------------------------------------------------------

    master_lookup = master.set_index(
        "itemid",
        drop=False,
    )

    allowed_mask = as_bool_series(
        master["allowed"]
    )

    allowed_ids = set(
        master.loc[
            allowed_mask,
            "itemid",
        ].astype(int)
    )

    seller_ids = set(
        seller["itemid"].astype(int)
    )

    buyer_ids = set(
        buyer["itemid"].astype(int)
    )

    illegal_seller_ids = sorted(
        seller_ids - allowed_ids
    )

    if illegal_seller_ids:
        raise MarketBuildError(
            "Seller contains items not approved by "
            "master market: "
            f"{illegal_seller_ids[:20]}"
        )

    # Buyer v1 must be a subset of seller-approved goods.
    buyer_outside_seller = sorted(
        buyer_ids - seller_ids
    )

    if buyer_outside_seller:
        raise MarketBuildError(
            "Buyer contains items outside the Seed "
            "seller catalog: "
            f"{buyer_outside_seller[:20]}"
        )

    # --------------------------------------------------------
    # Seller direction safety
    # --------------------------------------------------------

    if not (
        seller["buy_single"].astype(int) == 0
    ).all():
        raise MarketBuildError(
            "Seller CSV has buy_single enabled."
        )

    if not (
        seller["buy_stacks"].astype(int) == 0
    ).all():
        raise MarketBuildError(
            "Seller CSV has buy_stacks enabled."
        )

    enabled_single_sellers = (
        seller["sell_single"].astype(int) == 1
    )

    enabled_stack_sellers = (
        seller["sell_stacks"].astype(int) == 1
    )

    if (
        seller.loc[
            enabled_single_sellers,
            "price_single",
        ]
        .astype(int)
        .le(0)
        .any()
    ):
        raise MarketBuildError(
            "Seller contains a non-positive single price."
        )

    if (
        seller.loc[
            enabled_stack_sellers,
            "price_stacks",
        ]
        .astype(int)
        .le(0)
        .any()
    ):
        raise MarketBuildError(
            "Seller contains a non-positive stack price."
        )

    # --------------------------------------------------------
    # Buyer direction safety
    # --------------------------------------------------------

    if not (
        buyer["sell_single"].astype(int) == 0
    ).all():
        raise MarketBuildError(
            "Buyer CSV has sell_single enabled."
        )

    if not (
        buyer["sell_stacks"].astype(int) == 0
    ).all():
        raise MarketBuildError(
            "Buyer CSV has sell_stacks enabled."
        )

    if not (
        buyer["buy_single"].astype(int) == 1
    ).all():
        raise MarketBuildError(
            "Buyer CSV contains buy_single disabled."
        )

    if not (
        buyer["buy_stacks"].astype(int) == 1
    ).all():
        raise MarketBuildError(
            "Buyer CSV contains buy_stacks disabled."
        )

    if (
        buyer["price_single"]
        .astype(int)
        .le(0)
        .any()
    ):
        raise MarketBuildError(
            "Buyer contains a non-positive single bid."
        )

    if (
        buyer["price_stacks"]
        .astype(int)
        .le(0)
        .any()
    ):
        raise MarketBuildError(
            "Buyer contains a non-positive stack bid."
        )

    # --------------------------------------------------------
    # Vendor buyer safety
    # --------------------------------------------------------

    buyer_master = buyer[
        [
            "itemid",
            "name",
        ]
    ].merge(
        master[
            [
                "itemid",
                "market_class",
                "vendor_item",
            ]
        ],
        on="itemid",
        how="left",
        validate="one_to_one",
    )

    vendor_mask = as_bool_series(
        buyer_master["vendor_item"]
    )

    unsafe_vendor_buys = buyer_master[
        vendor_mask
    ]

    if not unsafe_vendor_buys.empty:
        raise MarketBuildError(
            "Buyer contains NPC vendor-associated "
            "items: "
            f"{unsafe_vendor_buys['itemid'].astype(int).tolist()[:20]}"
        )

    # Seed Buyer v1 intentionally excludes crystals.
    crystal_buys = buyer_master[
        buyer_master[
            "market_class"
        ].astype(str)
        == "STAPLE_CRYSTAL"
    ]

    if not crystal_buys.empty:
        raise MarketBuildError(
            "Buyer v1 contains STAPLE_CRYSTAL items."
        )

    # --------------------------------------------------------
    # Bid / ask safety
    # --------------------------------------------------------

    prices = buyer[
        [
            "itemid",
            "price_single",
            "price_stacks",
        ]
    ].merge(
        seller[
            [
                "itemid",
                "price_single",
                "price_stacks",
            ]
        ],
        on="itemid",
        suffixes=(
            "_buyer",
            "_seller",
        ),
        how="left",
        validate="one_to_one",
    )

    bad_single_spread = prices[
        prices[
            "price_single_buyer"
        ].astype(int)
        >=
        prices[
            "price_single_seller"
        ].astype(int)
    ]

    if not bad_single_spread.empty:
        raise MarketBuildError(
            "Buyer single bid >= seller ask for "
            "item IDs: "
            f"{bad_single_spread['itemid'].astype(int).tolist()[:20]}"
        )

    bad_stack_spread = prices[
        prices[
            "price_stacks_buyer"
        ].astype(int)
        >=
        prices[
            "price_stacks_seller"
        ].astype(int)
    ]

    if not bad_stack_spread.empty:
        raise MarketBuildError(
            "Buyer stack bid >= seller ask for "
            "item IDs: "
            f"{bad_stack_spread['itemid'].astype(int).tolist()[:20]}"
        )

    # --------------------------------------------------------
    # Rate safety
    # --------------------------------------------------------

    rate_columns = [
        "buy_rate_single",
        "buy_rate_stacks",
    ]

    for column in rate_columns:
        rates = buyer[
            column
        ].astype(float)

        if (
            (rates < 0.0)
            |
            (rates > 1.0)
        ).any():
            raise MarketBuildError(
                f"Buyer {column} contains values "
                "outside 0.0 -> 1.0."
            )

    seller_rate_columns = [
        "sell_rate_single",
        "sell_rate_stacks",
    ]

    for column in seller_rate_columns:
        rates = seller[
            column
        ].astype(float)

        if (
            (rates < 0.0)
            |
            (rates > 1.0)
        ).any():
            raise MarketBuildError(
                f"Seller {column} contains values "
                "outside 0.0 -> 1.0."
            )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    seller_classes = (
        seller[
            [
                "itemid",
            ]
        ]
        .merge(
            master[
                [
                    "itemid",
                    "market_class",
                ]
            ],
            on="itemid",
            validate="one_to_one",
        )["market_class"]
        .value_counts()
        .to_dict()
    )

    buyer_classes = (
        buyer_master[
            "market_class"
        ]
        .value_counts()
        .to_dict()
    )

    summary = {
        "status": "PASS",
        "master_items": int(
            len(master)
        ),
        "master_allowed_items": int(
            allowed_mask.sum()
        ),
        "seller_items": int(
            len(seller)
        ),
        "buyer_items": int(
            len(buyer)
        ),
        "seller_classes": {
            str(k): int(v)
            for k, v in seller_classes.items()
        },
        "buyer_classes": {
            str(k): int(v)
            for k, v in buyer_classes.items()
        },
        "seller_single_price_min": int(
            seller[
                "price_single"
            ].min()
        ),
        "seller_single_price_max": int(
            seller[
                "price_single"
            ].max()
        ),
        "buyer_single_bid_min": int(
            buyer[
                "price_single"
            ].min()
        ),
        "buyer_single_bid_max": int(
            buyer[
                "price_single"
            ].max()
        ),
        "seller_stack_price_min": int(
            seller[
                "price_stacks"
            ].min()
        ),
        "seller_stack_price_max": int(
            seller[
                "price_stacks"
            ].max()
        ),
        "buyer_stack_bid_min": int(
            buyer[
                "price_stacks"
            ].min()
        ),
        "buyer_stack_bid_max": int(
            buyer[
                "price_stacks"
            ].max()
        ),
    }

    return summary


# ============================================================
# Main build
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build and validate the LandSandBoat "
            "AHBot economy."
        )
    )

    parser.add_argument(
        "--validate-only",
        action="store_true",
        help=(
            "Validate existing generated market files "
            "without rebuilding them."
        ),
    )

    parser.add_argument(
        "--skip-vendor-index",
        action="store_true",
        help=(
            "Reuse the existing vendor-items.csv instead "
            "of rescanning the LSB Lua shop scripts."
        ),
    )

    args = parser.parse_args()

    REPORTS.mkdir(
        parents=True,
        exist_ok=True,
    )

    snapshots = snapshot_production_files()

    try:
        if not args.validate_only:
            if not args.skip_vendor_index:
                run_builder(
                    BUILD_VENDOR,
                    "Building Vendor Index",
                )

            run_builder(
                BUILD_SELLER,
                "Building Seed Seller Market",
            )

            run_builder(
                BUILD_BUYER,
                "Building Seed Buyer Market",
            )

        print()
        print(
            "======================================"
        )
        print(
            " Market Safety Audit"
        )
        print(
            "======================================"
        )

        (
            master,
            seller,
            buyer,
        ) = load_outputs()

        summary = validate_market(
            master,
            seller,
            buyer,
        )

        SUMMARY_FILE.write_text(
            json.dumps(
                summary,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        print()
        print(
            "MARKET BUILD: PASS"
        )
        print()

        print(
            f"Master items:          "
            f"{summary['master_items']}"
        )

        print(
            f"Master approved:       "
            f"{summary['master_allowed_items']}"
        )

        print(
            f"Seller catalog:        "
            f"{summary['seller_items']}"
        )

        print(
            f"Buyer catalog:         "
            f"{summary['buyer_items']}"
        )

        print()

        print(
            "Seller classes:"
        )

        for key, value in (
            summary[
                "seller_classes"
            ].items()
        ):
            print(
                f"  {key:<20} {value}"
            )

        print()

        print(
            "Buyer classes:"
        )

        for key, value in (
            summary[
                "buyer_classes"
            ].items()
        ):
            print(
                f"  {key:<20} {value}"
            )

        print()

        print(
            f"Summary report: {SUMMARY_FILE}"
        )

        return 0

    except Exception as exc:
        print()
        print(
            "======================================"
        )
        print(
            " MARKET BUILD FAILED"
        )
        print(
            "======================================"
        )
        print()
        print(
            str(exc)
        )

        if not args.validate_only:
            restore_production_files(
                snapshots
            )

        print()
        print(
            "Previous production market preserved."
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
