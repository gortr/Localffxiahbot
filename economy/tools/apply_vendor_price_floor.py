from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd


# ============================================================
# Paths
# ============================================================

ROOT = Path.home() / "ffxiahbot"

GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

SELLER_FILE = GENERATED / "market-sell-phase0.csv"
MASTER_FILE = GENERATED / "master-market.csv"
VENDOR_FILE = GENERATED / "vendor-prices.csv"

REPORT_FILE = REPORTS / "vendor-price-adjustments.csv"


# ============================================================
# Policy
# ============================================================

# Players using the AH are paying a modest premium for
# convenience versus traveling to the NPC vendor.
SINGLE_VENDOR_PREMIUM = 0.10
STACK_VENDOR_PREMIUM = 0.05

# Preserve our existing bulk pricing rule:
# a stack should never be dramatically cheaper per unit than
# AHBot's own single-item price.
STACK_SINGLE_RATIO_FLOOR = 0.90


# ============================================================
# Helpers
# ============================================================

class VendorFloorError(RuntimeError):
    pass


def as_bool(value) -> bool:
    if isinstance(value, bool):
        return value

    if pd.isna(value):
        return False

    return (
        str(value)
        .strip()
        .lower()
        in {
            "true",
            "1",
            "yes",
            "y",
        }
    )


def require_columns(
    frame: pd.DataFrame,
    required: set[str],
    label: str,
) -> None:
    missing = sorted(
        required - set(frame.columns)
    )

    if missing:
        raise VendorFloorError(
            f"{label} missing required columns: "
            + ", ".join(missing)
        )


# ============================================================
# Main
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply NPC vendor convenience-price floors "
            "to the AHBot Seed seller catalog."
        )
    )

    parser.add_argument(
        "--check-only",
        action="store_true",
        help=(
            "Calculate and report adjustments without "
            "changing the seller CSV."
        ),
    )

    args = parser.parse_args()

    REPORTS.mkdir(
        parents=True,
        exist_ok=True,
    )

    seller = pd.read_csv(
        SELLER_FILE
    )

    master = pd.read_csv(
        MASTER_FILE
    )

    vendor = pd.read_csv(
        VENDOR_FILE
    )

    require_columns(
        seller,
        {
            "itemid",
            "name",
            "sell_single",
            "price_single",
            "sell_stacks",
            "price_stacks",
        },
        "market-sell-phase0.csv",
    )

    require_columns(
        master,
        {
            "itemid",
            "stack_size",
        },
        "master-market.csv",
    )

    require_columns(
        vendor,
        {
            "itemid",
            "vendor_price_min",
            "vendor_price_max",
            "has_hard_floor",
            "source_types",
            "source_files",
        },
        "vendor-prices.csv",
    )

    if seller["itemid"].duplicated().any():
        raise VendorFloorError(
            "Seller catalog contains duplicate item IDs."
        )

    if master["itemid"].duplicated().any():
        raise VendorFloorError(
            "Master market contains duplicate item IDs."
        )

    if vendor["itemid"].duplicated().any():
        raise VendorFloorError(
            "Vendor price index contains duplicate item IDs."
        )

    master_lookup = master.set_index(
        "itemid"
    )

    vendor_lookup = vendor.set_index(
        "itemid"
    )

    adjusted = seller.copy()

    report_rows: list[dict] = []

    overlap_count = 0
    hard_floor_count = 0
    adjusted_item_count = 0
    adjusted_single_count = 0
    adjusted_stack_count = 0

    for idx, row in adjusted.iterrows():
        itemid = int(
            row["itemid"]
        )

        if itemid not in vendor_lookup.index:
            continue

        overlap_count += 1

        vendor_row = vendor_lookup.loc[
            itemid
        ]

        if not as_bool(
            vendor_row["has_hard_floor"]
        ):
            continue

        hard_floor_count += 1

        if pd.isna(
            vendor_row["vendor_price_min"]
        ):
            raise VendorFloorError(
                f"Item {itemid} has has_hard_floor=1 "
                "but no vendor_price_min."
            )

        vendor_price_min = int(
            vendor_row["vendor_price_min"]
        )

        if vendor_price_min <= 0:
            raise VendorFloorError(
                f"Item {itemid} has invalid "
                f"vendor_price_min={vendor_price_min}"
            )

        if itemid not in master_lookup.index:
            raise VendorFloorError(
                f"Seller item {itemid} missing from "
                "master market."
            )

        master_row = master_lookup.loc[
            itemid
        ]

        stack_size = int(
            master_row["stack_size"]
        )

        old_single = int(
            row["price_single"]
        )

        old_stack = int(
            row["price_stacks"]
        )

        new_single = old_single
        new_stack = old_stack

        npc_single_floor = None
        npc_stack_floor = None
        stack_relationship_floor = None

        # ----------------------------------------------------
        # Single
        # ----------------------------------------------------

        if int(
            row["sell_single"]
        ) == 1:
            npc_single_floor = math.ceil(
                vendor_price_min
                * (1.0 + SINGLE_VENDOR_PREMIUM)
            )

            new_single = max(
                old_single,
                npc_single_floor,
            )

        # ----------------------------------------------------
        # Stack
        # ----------------------------------------------------

        if int(
            row["sell_stacks"]
        ) == 1:
            if stack_size <= 1:
                raise VendorFloorError(
                    f"Stack-enabled item {itemid} has "
                    f"invalid stack size {stack_size}."
                )

            npc_stack_floor = math.ceil(
                vendor_price_min
                * stack_size
                * (1.0 + STACK_VENDOR_PREMIUM)
            )

            stack_relationship_floor = math.ceil(
                new_single
                * stack_size
                * STACK_SINGLE_RATIO_FLOOR
            )

            new_stack = max(
                old_stack,
                npc_stack_floor,
                stack_relationship_floor,
            )

        single_changed = (
            new_single != old_single
        )

        stack_changed = (
            new_stack != old_stack
        )

        if single_changed:
            adjusted_single_count += 1

        if stack_changed:
            adjusted_stack_count += 1

        if single_changed or stack_changed:
            adjusted_item_count += 1

        adjusted.at[
            idx,
            "price_single",
        ] = new_single

        adjusted.at[
            idx,
            "price_stacks",
        ] = new_stack

        report_rows.append(
            {
                "itemid": itemid,
                "name": row["name"],
                "stack_size": stack_size,
                "vendor_price_min":
                    vendor_price_min,
                "vendor_price_max":
                    vendor_row[
                        "vendor_price_max"
                    ],
                "old_single": old_single,
                "npc_single_floor":
                    npc_single_floor,
                "new_single": new_single,
                "single_adjusted":
                    single_changed,
                "old_stack": old_stack,
                "npc_stack_floor":
                    npc_stack_floor,
                "stack_relationship_floor":
                    stack_relationship_floor,
                "new_stack": new_stack,
                "stack_adjusted":
                    stack_changed,
                "source_types":
                    vendor_row[
                        "source_types"
                    ],
                "source_files":
                    vendor_row[
                        "source_files"
                    ],
            }
        )

    # Always emit a valid CSV, even when there are no trusted
    # vendor-price overlaps in the current seller catalog.
    report_columns = [
        "itemid",
        "name",
        "stack_size",
        "vendor_price_min",
        "vendor_price_max",
        "old_single",
        "npc_single_floor",
        "new_single",
        "single_adjusted",
        "old_stack",
        "npc_stack_floor",
        "stack_relationship_floor",
        "new_stack",
        "stack_adjusted",
        "source_types",
        "source_files",
    ]

    report = pd.DataFrame(
        report_rows,
        columns=report_columns,
    )

    report.to_csv(
        REPORT_FILE,
        index=False,
    )

    if not args.check_only:
        adjusted.to_csv(
            SELLER_FILE,
            index=False,
        )

    print()
    print(
        "======================================"
    )
    print(
        " NPC Vendor Convenience Price Floor"
    )
    print(
        "======================================"
    )

    print(
        f"Seller items:                  "
        f"{len(seller):>6}"
    )

    print(
        f"Vendor overlaps:               "
        f"{overlap_count:>6}"
    )

    print(
        f"Trusted vendor-price overlaps: "
        f"{hard_floor_count:>6}"
    )

    print(
        f"Items adjusted:                "
        f"{adjusted_item_count:>6}"
    )

    print(
        f"Single prices adjusted:        "
        f"{adjusted_single_count:>6}"
    )

    print(
        f"Stack prices adjusted:         "
        f"{adjusted_stack_count:>6}"
    )

    print()

    print(
        f"Single convenience premium: "
        f"{SINGLE_VENDOR_PREMIUM:.0%}"
    )

    print(
        f"Stack convenience premium:  "
        f"{STACK_VENDOR_PREMIUM:.0%}"
    )

    print()

    if args.check_only:
        print(
            "CHECK ONLY: seller CSV was not modified."
        )
    else:
        print(
            f"Updated seller: {SELLER_FILE}"
        )

    print(
        f"Report:         {REPORT_FILE}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
