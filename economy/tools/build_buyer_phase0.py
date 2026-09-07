from __future__ import annotations

from pathlib import Path

import pandas as pd


# ============================================================
# Paths
# ============================================================

ROOT = Path.home() / "ffxiahbot"

GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

MASTER_FILE = GENERATED / "master-market.csv"
SELLER_FILE = GENERATED / "market-sell-phase0.csv"

BUYER_FILE = GENERATED / "market-buy-phase0.csv"
REPORT_FILE = REPORTS / "phase0-buyer-excluded.csv"

REPORTS.mkdir(parents=True, exist_ok=True)


# ============================================================
# Buyer policy
# ============================================================

# Buyer v1 deliberately excludes every item our vendor scanner
# sees, INCLUDING elemental crystals.
#
# Crystals remain available from AHBot Seller, but AHBot Buyer
# will not purchase them until we understand precisely why
# LSB's shop scripts expose them to the vendor scanner.

CLASS_POLICY = {
    "STAPLE": {
        "bid_multiplier": 0.78,

        # With tick=120 seconds this gives a median sale time
        # of roughly 1.5 hours.
        "buy_rate_single": 0.015,
        "buy_rate_stacks": 0.015,
    },

    "NORMAL": {
        "bid_multiplier": 0.70,

        # With tick=120 seconds this gives a median sale time
        # of roughly 4.6 hours.
        "buy_rate_single": 0.005,
        "buy_rate_stacks": 0.005,
    },

    # Defined for future use, but currently vendor filtering
    # prevents our eight crystals from entering Buyer v1.
    "STAPLE_CRYSTAL": {
        "bid_multiplier": 0.85,
        "buy_rate_single": 0.030,
        "buy_rate_stacks": 0.030,
    },
}


# ============================================================
# Helpers
# ============================================================

def as_bool(value) -> bool:
    if isinstance(value, bool):
        return value

    if pd.isna(value):
        return False

    return str(value).strip().lower() in {
        "true",
        "1",
        "yes",
        "y",
    }


def safe_bid(
    ask: int,
    multiplier: float,
) -> int | None:
    """
    Produce a bid strictly below AHBot's own seller ask.

    Returning None means no safely separated buyer price could
    be created.
    """

    ask = int(ask)

    if ask <= 1:
        return None

    bid = int(
        ask * multiplier
    )

    bid = max(
        1,
        bid,
    )

    # Hard guarantee against buying from our seller and
    # relisting directly back to the buyer for a profit.
    if bid >= ask:
        bid = ask - 1

    if bid <= 0:
        return None

    return bid


# ============================================================
# Load production economy
# ============================================================

master = pd.read_csv(
    MASTER_FILE
)

seller = pd.read_csv(
    SELLER_FILE
)


# ============================================================
# Merge authoritative classification with seller prices
# ============================================================

master_columns = [
    "itemid",
    "name",
    "market_class",
    "allowed",
    "vendor_item",
    "base_sell",
    "stack_size",
    "provenance",
    "rejection_reason",
]

market = seller.merge(
    master[master_columns],
    on=[
        "itemid",
        "name",
    ],
    how="left",
    validate="one_to_one",
)


# ============================================================
# Build buyer catalog
# ============================================================

buyer_rows = []
excluded_rows = []


for _, row in market.iterrows():
    itemid = int(
        row["itemid"]
    )

    name = str(
        row["name"]
    )

    market_class = str(
        row["market_class"]
    )

    allowed = as_bool(
        row["allowed"]
    )

    vendor_item = as_bool(
        row["vendor_item"]
    )

    reasons: list[str] = []

    # --------------------------------------------------------
    # Safety checks
    # --------------------------------------------------------

    if not allowed:
        reasons.append(
            "NOT_SELLER_APPROVED"
        )

    # Unlike the seller, Buyer v1 has NO vendor exemptions.
    if vendor_item:
        reasons.append(
            "NPC_VENDOR"
        )

    policy = CLASS_POLICY.get(
        market_class
    )

    if policy is None:
        reasons.append(
            "NO_BUYER_POLICY"
        )

    # --------------------------------------------------------
    # Calculate bid
    # --------------------------------------------------------

    bid_single = None
    bid_stack = None

    if policy is not None:
        multiplier = float(
            policy[
                "bid_multiplier"
            ]
        )

        bid_single = safe_bid(
            int(row["price_single"]),
            multiplier,
        )

        bid_stack = safe_bid(
            int(row["price_stacks"]),
            multiplier,
        )

        if bid_single is None:
            reasons.append(
                "NO_SAFE_SINGLE_BID"
            )

        if bid_stack is None:
            reasons.append(
                "NO_SAFE_STACK_BID"
            )

    reasons = list(
        dict.fromkeys(reasons)
    )

    # --------------------------------------------------------
    # Exclusion report
    # --------------------------------------------------------

    if reasons:
        excluded_rows.append(
            {
                "itemid": itemid,
                "name": name,
                "market_class": market_class,
                "seller_price_single": int(
                    row["price_single"]
                ),
                "seller_price_stacks": int(
                    row["price_stacks"]
                ),
                "vendor_item": vendor_item,
                "reason": "|".join(
                    reasons
                ),
            }
        )

        continue

    # --------------------------------------------------------
    # Production buyer row
    # --------------------------------------------------------

    buyer_rows.append(
        {
            "itemid": itemid,
            "name": name,

            # Buyer service only.
            "sell_single": 0,
            "buy_single": 1,

            "price_single": bid_single,

            # Buyer doesn't restock anything.
            "stock_single": 0,

            "buy_rate_single": float(
                policy[
                    "buy_rate_single"
                ]
            ),

            "sell_rate_single": 0.0,

            "sell_stacks": 0,
            "buy_stacks": 1,

            "price_stacks": bid_stack,

            "stock_stacks": 0,

            "buy_rate_stacks": float(
                policy[
                    "buy_rate_stacks"
                ]
            ),

            "sell_rate_stacks": 0.0,
        }
    )


# ============================================================
# Write outputs
# ============================================================

buyer = pd.DataFrame(
    buyer_rows
)

excluded = pd.DataFrame(
    excluded_rows
)

buyer.to_csv(
    BUYER_FILE,
    index=False,
)

excluded.to_csv(
    REPORT_FILE,
    index=False,
)


# ============================================================
# Validation
# ============================================================

if not buyer.empty:
    # Buyer must NEVER be able to sell.
    assert (
        buyer["sell_single"] == 0
    ).all()

    assert (
        buyer["sell_stacks"] == 0
    ).all()

    # Every buyer bid must remain below AHBot's corresponding
    # seller ask.
    comparison = buyer.merge(
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
    )

    assert (
        comparison[
            "price_single_buyer"
        ]
        <
        comparison[
            "price_single_seller"
        ]
    ).all()

    assert (
        comparison[
            "price_stacks_buyer"
        ]
        <
        comparison[
            "price_stacks_seller"
        ]
    ).all()


# ============================================================
# Summary
# ============================================================

print()
print(
    "======================================"
)
print(
    " Phase 0 / Seed Buyer v1"
)
print(
    "======================================"
)

print(
    f"Seed seller items:       "
    f"{len(seller):>6}"
)

print(
    f"Buyer-approved items:    "
    f"{len(buyer):>6}"
)

print(
    f"Buyer-excluded items:    "
    f"{len(excluded):>6}"
)

print()

if not excluded.empty:
    print(
        "Exclusion reasons:"
    )

    print(
        excluded["reason"]
        .str.split("|")
        .explode()
        .value_counts()
        .to_string()
    )

    print()

if not buyer.empty:
    joined = buyer.merge(
        master[
            [
                "itemid",
                "market_class",
            ]
        ],
        on="itemid",
    )

    print(
        "Buyer classes:"
    )

    print(
        joined[
            "market_class"
        ]
        .value_counts()
        .to_string()
    )

    print()

    print(
        "Bid ranges:"
    )

    print(
        "Singles:"
        f" {buyer['price_single'].min()}"
        f" -> {buyer['price_single'].max()}"
    )

    print(
        "Stacks:"
        f" {buyer['price_stacks'].min()}"
        f" -> {buyer['price_stacks'].max()}"
    )

    print()

print(
    "Generated:"
)

print(
    BUYER_FILE
)

print(
    REPORT_FILE
)
