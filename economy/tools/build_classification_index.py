from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from market_policy import (
    MarketClass,
    as_bool,
    classify_row,
)


# ============================================================
# Paths
# ============================================================

ROOT = Path.home() / "ffxiahbot"

GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

MASTER_FILE = GENERATED / "master-market.csv"
CLASSIFICATION_FILE = GENERATED / "market-classification.csv"
SUMMARY_FILE = REPORTS / "market-classification-summary.json"


# ============================================================
# Helpers
# ============================================================

class ClassificationBuildError(RuntimeError):
    pass


def require_columns(
    frame: pd.DataFrame,
    required: set[str],
    label: str,
) -> None:
    missing = sorted(
        required - set(frame.columns)
    )

    if missing:
        raise ClassificationBuildError(
            f"{label} missing required columns: "
            + ", ".join(missing)
        )


# ============================================================
# Main
# ============================================================

def main() -> int:
    REPORTS.mkdir(
        parents=True,
        exist_ok=True,
    )

    master = pd.read_csv(
        MASTER_FILE
    )

    require_columns(
        master,
        {
            "itemid",
            "name",
            "allowed",
            "market_class",
            "vendor_item",
            "provenance",
            "rejection_reason",
        },
        "master-market.csv",
    )

    if master["itemid"].duplicated().any():
        raise ClassificationBuildError(
            "master-market.csv contains duplicate item IDs."
        )

    rows: list[dict] = []

    for _, source in master.iterrows():
        source_dict = source.to_dict()

        decision = classify_row(
            source_dict
        )

        current_seed_sell_allowed = as_bool(
            source_dict.get(
                "allowed"
            )
        )

        legacy_market_class = str(
            source_dict.get(
                "market_class",
                "",
            )
        ).strip()

        if (
            legacy_market_class.lower()
            == "nan"
        ):
            legacy_market_class = ""

        vendor_item = as_bool(
            source_dict.get(
                "vendor_item"
            )
        )

        # Buyer v1 remains intentionally stricter than base class capability:
        # vendor-associated items and the legacy crystal exception are blocked.
        current_seed_buy_allowed = (
            current_seed_sell_allowed
            and not vendor_item
            and legacy_market_class
                != "STAPLE_CRYSTAL"
        )

        provenance = str(
            source_dict.get(
                "provenance",
                "",
            )
        ).strip()

        if provenance.lower() == "nan":
            provenance = ""

        provenance_known = (
            bool(provenance)
            and provenance.lower()
            not in {
                "unknown",
                "none",
            }
        )

        rows.append(
            {
                "itemid":
                    int(
                        source_dict[
                            "itemid"
                        ]
                    ),

                "name":
                    source_dict[
                        "name"
                    ],

                "market_class":
                    decision.market_class.value,

                "classification_reason":
                    decision.classification_reason,

                "base_sell_capable":
                    int(
                        decision.base_sell_capable
                    ),

                "base_buy_capable":
                    int(
                        decision.base_buy_capable
                    ),

                "review_required":
                    int(
                        decision.review_required
                    ),

                "current_seed_sell_allowed":
                    int(
                        current_seed_sell_allowed
                    ),

                "current_seed_buy_allowed":
                    int(
                        current_seed_buy_allowed
                    ),

                "legacy_market_class":
                    legacy_market_class,

                "vendor_item":
                    int(
                        vendor_item
                    ),

                "provenance":
                    provenance,

                "provenance_known":
                    int(
                        provenance_known
                    ),

                "rejection_reason":
                    source_dict.get(
                        "rejection_reason",
                        "",
                    ),
            }
        )

    classification = pd.DataFrame(
        rows
    )

    if classification.empty:
        raise ClassificationBuildError(
            "Classification index is empty."
        )

    if classification["itemid"].duplicated().any():
        raise ClassificationBuildError(
            "Classification output contains duplicate item IDs."
        )

    # --------------------------------------------------------
    # Preserve our proven Seed behavior exactly.
    # --------------------------------------------------------

    seed_seller_count = int(
        classification[
            "current_seed_sell_allowed"
        ].sum()
    )

    seed_buyer_count = int(
        classification[
            "current_seed_buy_allowed"
        ].sum()
    )

    if seed_seller_count != 167:
        raise ClassificationBuildError(
            "Expected 167 current Seed seller items, "
            f"found {seed_seller_count}."
        )

    if seed_buyer_count != 159:
        raise ClassificationBuildError(
            "Expected 159 current Seed buyer items, "
            f"found {seed_buyer_count}."
        )

    # No currently approved Seed seller item may resolve to a
    # non-synthetic class.
    current_seed = classification[
        classification[
            "current_seed_sell_allowed"
        ] == 1
    ]

    invalid_current = current_seed[
        ~current_seed[
            "market_class"
        ].isin(
            {
                MarketClass.STAPLE.value,
                MarketClass.NORMAL.value,
                MarketClass.SCARCE.value,
            }
        )
    ]

    if not invalid_current.empty:
        raise ClassificationBuildError(
            "Current Seed-approved items classified as "
            "non-synthetic classes: "
            f"{invalid_current['itemid'].astype(int).tolist()[:20]}"
        )

    # Hard-blocked items can never be marked currently sellable/buyable.
    blocked = classification[
        classification[
            "market_class"
        ] == MarketClass.BLOCKED.value
    ]

    if (
        blocked[
            "current_seed_sell_allowed"
        ].sum()
        or blocked[
            "current_seed_buy_allowed"
        ].sum()
    ):
        raise ClassificationBuildError(
            "BLOCKED items are present in current Seed market."
        )

    classification.to_csv(
        CLASSIFICATION_FILE,
        index=False,
    )

    class_counts = (
        classification[
            "market_class"
        ]
        .value_counts()
        .to_dict()
    )

    summary = {
        "status":
            "PASS",

        "total_items":
            int(
                len(
                    classification
                )
            ),

        "classes":
            {
                str(key):
                    int(value)
                for key, value
                in class_counts.items()
            },

        "review_required":
            int(
                classification[
                    "review_required"
                ].sum()
            ),

        "provenance_known":
            int(
                classification[
                    "provenance_known"
                ].sum()
            ),

        "provenance_unknown":
            int(
                len(
                    classification
                )
                - classification[
                    "provenance_known"
                ].sum()
            ),

        "current_seed_seller":
            seed_seller_count,

        "current_seed_buyer":
            seed_buyer_count,
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
        " Canonical Market Classification"
    )
    print(
        "======================================"
    )

    print(
        f"Total items:           "
        f"{summary['total_items']:>6}"
    )

    print(
        f"Current Seed seller:   "
        f"{summary['current_seed_seller']:>6}"
    )

    print(
        f"Current Seed buyer:    "
        f"{summary['current_seed_buyer']:>6}"
    )

    print(
        f"Review required:       "
        f"{summary['review_required']:>6}"
    )

    print(
        f"Known provenance:      "
        f"{summary['provenance_known']:>6}"
    )

    print(
        f"Unknown provenance:    "
        f"{summary['provenance_unknown']:>6}"
    )

    print()
    print(
        "Classes:"
    )

    for market_class in MarketClass:
        print(
            f"  {market_class.value:<14} "
            f"{summary['classes'].get(market_class.value, 0):>6}"
        )

    print()
    print(
        f"Generated: {CLASSIFICATION_FILE}"
    )
    print(
        f"Summary:   {SUMMARY_FILE}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
