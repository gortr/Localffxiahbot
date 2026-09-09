from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

VENDOR_ITEMS_FILE = ROOT / "economy" / "generated" / "vendor-items.csv"
VENDOR_PRICES_FILE = ROOT / "economy" / "generated" / "vendor-prices.csv"

OUTPUT_FILE = ROOT / "economy" / "generated" / "vendor-policy.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "vendor-policy-summary.json"


class VendorPolicyError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip()

    if text.lower() == "nan":
        return ""

    return text


def as_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def main() -> int:
    for path in [
        VENDOR_ITEMS_FILE,
        VENDOR_PRICES_FILE,
    ]:
        if not path.exists():
            raise VendorPolicyError(
                f"Missing required input: {path}"
            )

    items = pd.read_csv(
        VENDOR_ITEMS_FILE,
        low_memory=False,
    )

    prices = pd.read_csv(
        VENDOR_PRICES_FILE,
        low_memory=False,
    )

    if "itemid" not in items.columns:
        raise VendorPolicyError(
            "vendor-items.csv has no itemid column."
        )

    if "itemid" not in prices.columns:
        raise VendorPolicyError(
            "vendor-prices.csv has no itemid column."
        )

    if items["itemid"].duplicated().any():
        raise VendorPolicyError(
            "vendor-items.csv contains duplicate itemids."
        )

    if prices["itemid"].duplicated().any():
        raise VendorPolicyError(
            "vendor-prices.csv contains duplicate itemids."
        )

    items = items.copy()
    prices = prices.copy()

    items["itemid"] = items["itemid"].map(as_int)
    prices["itemid"] = prices["itemid"].map(as_int)

    item_by_id = {
        as_int(row.get("itemid")): row.to_dict()
        for _, row in items.iterrows()
    }

    price_by_id = {
        as_int(row.get("itemid")): row.to_dict()
        for _, row in prices.iterrows()
    }

    all_ids = sorted(
        set(item_by_id)
        | set(price_by_id)
    )

    rows: list[dict[str, Any]] = []

    for itemid in all_ids:
        item_row = item_by_id.get(itemid, {})
        price_row = price_by_id.get(itemid, {})

        reference_source_count = as_int(
            item_row.get("source_count")
        )

        priced_source_count = as_int(
            price_row.get("priced_source_count")
        )

        vendor_source_count = as_int(
            price_row.get("vendor_source_count")
        )

        gated_source_count = as_int(
            price_row.get("gated_source_count")
        )

        has_hard_floor = as_int(
            price_row.get("has_hard_floor")
        )

        source_types = clean_text(
            price_row.get("source_types")
        )

        # A raw file-level reference is not enough to call something
        # a vendor item. Actual vendor semantics require one of the
        # recognized vendor-source counters from vendor-prices.csv.
        actual_vendor_source = int(
            bool(
                vendor_source_count > 0
                or priced_source_count > 0
                or gated_source_count > 0
            )
        )

        vendor_reference_only = int(
            bool(
                reference_source_count > 0
                and not actual_vendor_source
            )
        )

        buyer_vendor_block = actual_vendor_source

        seller_vendor_floor_required = actual_vendor_source

        seller_vendor_ready = int(
            bool(
                actual_vendor_source
                and has_hard_floor
            )
        )

        rows.append(
            {
                "itemid": itemid,
                "name": (
                    clean_text(
                        price_row.get("name")
                    )
                    or clean_text(
                        item_row.get("constants")
                    )
                ),
                "vendor_reference": int(
                    reference_source_count > 0
                ),
                "vendor_reference_source_count":
                    reference_source_count,
                "actual_vendor_source":
                    actual_vendor_source,
                "vendor_reference_only":
                    vendor_reference_only,
                "priced_source_count":
                    priced_source_count,
                "vendor_source_count":
                    vendor_source_count,
                "gated_source_count":
                    gated_source_count,
                "has_hard_floor":
                    has_hard_floor,
                "vendor_price_min":
                    price_row.get(
                        "vendor_price_min"
                    ),
                "vendor_price_max":
                    price_row.get(
                        "vendor_price_max"
                    ),
                "source_types":
                    source_types,
                "source_files":
                    clean_text(
                        price_row.get("source_files")
                    )
                    or clean_text(
                        item_row.get("sources")
                    ),
                "buyer_vendor_block":
                    buyer_vendor_block,
                "seller_vendor_floor_required":
                    seller_vendor_floor_required,
                "seller_vendor_ready":
                    seller_vendor_ready,
                "activation_ready":
                    0,
                "auto_live_promotion":
                    0,
            }
        )

    output = pd.DataFrame(
        rows
    ).sort_values(
        by=[
            "itemid"
        ],
        kind="stable",
    )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    summary = {
        "status": "PASS",
        "total_reference_items": int(
            (
                output[
                    "vendor_reference"
                ]
                == 1
            ).sum()
        ),
        "actual_vendor_sources": int(
            output[
                "actual_vendor_source"
            ].sum()
        ),
        "reference_only_items": int(
            output[
                "vendor_reference_only"
            ].sum()
        ),
        "hard_floor_items": int(
            output[
                "has_hard_floor"
            ].sum()
        ),
        "buyer_vendor_block": int(
            output[
                "buyer_vendor_block"
            ].sum()
        ),
        "seller_vendor_floor_required": int(
            output[
                "seller_vendor_floor_required"
            ].sum()
        ),
        "seller_vendor_ready": int(
            output[
                "seller_vendor_ready"
            ].sum()
        ),
        "activation_ready": 0,
        "auto_live_promotions": 0,
    }

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
    print("=" * 52)
    print(" Canonical Vendor Semantics")
    print("=" * 52)
    print(
        f"Broad reference items:               "
        f"{summary['total_reference_items']:>6}"
    )
    print(
        f"Actual vendor-source items:           "
        f"{summary['actual_vendor_sources']:>6}"
    )
    print(
        f"Reference-only items:                 "
        f"{summary['reference_only_items']:>6}"
    )
    print(
        f"Trusted hard-floor items:             "
        f"{summary['hard_floor_items']:>6}"
    )
    print(
        f"Buyer vendor blocks:                  "
        f"{summary['buyer_vendor_block']:>6}"
    )
    print(
        f"Seller vendor-floor required:         "
        f"{summary['seller_vendor_floor_required']:>6}"
    )
    print(
        f"Seller vendor-ready:                  "
        f"{summary['seller_vendor_ready']:>6}"
    )
    print()
    print("Activation ready: 0")
    print("Auto live promotions: 0")
    print()
    print(f"Generated: {OUTPUT_FILE}")
    print(f"Summary:   {SUMMARY_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
