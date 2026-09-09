from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

SOURCE_AUDIT_FILE = (
    ROOT
    / "economy"
    / "reports"
    / "scroll-puppet-source-audit.csv"
)

OUTPUT_FILE = (
    ROOT
    / "economy"
    / "generated"
    / "scroll-shop-price-evidence.csv"
)

SUMMARY_FILE = (
    ROOT
    / "economy"
    / "reports"
    / "scroll-shop-price-evidence-summary.json"
)


class ScrollShopEvidenceError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip()

    if text.lower() == "nan":
        return ""

    return text


def parse_prices(value: Any) -> list[int]:
    text = clean_text(
        value
    )

    if not text:
        return []

    prices: list[int] = []

    for token in text.split(
        "|"
    ):
        token = token.strip()

        if not token:
            continue

        try:
            price = int(
                token
            )

        except ValueError as exc:
            raise ScrollShopEvidenceError(
                f"Invalid literal shop price token: {token!r}"
            ) from exc

        if price > 0:
            prices.append(
                price
            )

    return sorted(
        set(
            prices
        )
    )


def main() -> int:
    if not SOURCE_AUDIT_FILE.exists():
        raise ScrollShopEvidenceError(
            f"Missing source audit: {SOURCE_AUDIT_FILE}"
        )

    audit = pd.read_csv(
        SOURCE_AUDIT_FILE,
        low_memory=False,
    )

    required = {
        "audit_bucket",
        "itemid",
        "name",
        "shop_reference_count",
        "literal_shop_prices",
        "shop_files",
        "auto_live_promotion",
    }

    missing = sorted(
        required
        - set(
            audit.columns
        )
    )

    if missing:
        raise ScrollShopEvidenceError(
            "scroll-puppet-source-audit.csv missing: "
            + ", ".join(
                missing
            )
        )

    if int(
        audit[
            "auto_live_promotion"
        ].sum()
    ) != 0:
        raise ScrollShopEvidenceError(
            "Source audit contains live promotions."
        )

    scrolls = audit[
        audit[
            "audit_bucket"
        ]
        == "SCROLL_POLICY"
    ].copy()

    rows: list[dict[str, Any]] = []

    for _, row in scrolls.iterrows():
        prices = parse_prices(
            row.get(
                "literal_shop_prices"
            )
        )

        if not prices:
            continue

        if int(
            row.get(
                "shop_reference_count",
                0,
            )
        ) <= 0:
            raise ScrollShopEvidenceError(
                "Literal price found without script-shop reference "
                f"for item {int(row['itemid'])}"
            )

        rows.append(
            {
                "itemid":
                    int(
                        row[
                            "itemid"
                        ]
                    ),

                "name":
                    clean_text(
                        row.get(
                            "name"
                        )
                    ),

                "vendor_price_min":
                    min(
                        prices
                    ),

                "vendor_price_max":
                    max(
                        prices
                    ),

                "has_hard_floor":
                    1,

                "priced_source_count":
                    len(
                        prices
                    ),

                "source_type":
                    "LSB_SCRIPT_LITERAL_SHOP",

                "source_files":
                    clean_text(
                        row.get(
                            "shop_files"
                        )
                    ),

                "buyer_allowed":
                    0,

                "seller_candidate":
                    1,

                "recommended_class":
                    "SCARCE",

                "manual_review_required":
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

    if output.empty:
        raise ScrollShopEvidenceError(
            "No trusted scroll shop prices were produced."
        )

    if output[
        "itemid"
    ].duplicated().any():
        raise ScrollShopEvidenceError(
            "Duplicate item IDs in scroll shop price evidence."
        )

    if int(
        output[
            "auto_live_promotion"
        ].sum()
    ) != 0:
        raise ScrollShopEvidenceError(
            "Scroll shop evidence attempted live promotion."
        )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    summary = {
        "status":
            "PASS",

        "trusted_scroll_vendor_items":
            int(
                len(
                    output
                )
            ),

        "buyer_allowed":
            int(
                output[
                    "buyer_allowed"
                ].sum()
            ),

        "seller_candidates":
            int(
                output[
                    "seller_candidate"
                ].sum()
            ),

        "manual_review_required":
            int(
                output[
                    "manual_review_required"
                ].sum()
            ),

        "auto_live_promotions":
            0,
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
    print(
        "======================================"
    )
    print(
        " Scroll Shop Price Evidence"
    )
    print(
        "======================================"
    )

    print(
        f"Trusted scroll vendor items: "
        f"{summary['trusted_scroll_vendor_items']:>6}"
    )

    print(
        f"Seller candidates:           "
        f"{summary['seller_candidates']:>6}"
    )

    print(
        f"Buyer allowed:               "
        f"{summary['buyer_allowed']:>6}"
    )

    print(
        f"Manual review required:      "
        f"{summary['manual_review_required']:>6}"
    )

    print(
        "Auto live promotions: 0"
    )

    print()
    print(
        f"Generated: {OUTPUT_FILE}"
    )

    print(
        f"Summary:   {SUMMARY_FILE}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
