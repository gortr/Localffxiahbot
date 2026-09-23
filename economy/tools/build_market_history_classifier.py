from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from ffxiahbot.config import Config
from ffxiahbot.database import Database


ROOT = Path.home() / "ffxiahbot"

REPORTS = (
    ROOT
    / "economy"
    / "reports"
)

DEFAULT_CONFIG = (
    ROOT
    / "bin"
    / "config.yaml"
)

OUTPUT_FILE = (
    REPORTS
    / "market-history-transactions.csv"
)

SUMMARY_FILE = (
    REPORTS
    / "market-history-classifier-summary.json"
)


CLASS_SYNTHETIC = (
    "SYNTHETIC_HISTORY"
)

CLASS_PLAYER_SUPPLY = (
    "PLAYER_SUPPLY_TO_BOT"
)

CLASS_PLAYER_DEMAND = (
    "PLAYER_DEMAND_FROM_BOT"
)

CLASS_ORGANIC = (
    "ORGANIC_PLAYER_TRADE"
)

CLASS_UNCLASSIFIED = (
    "UNCLASSIFIED"
)


class MarketHistoryClassifierError(
    RuntimeError
):
    pass


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Build read-only auction-history "
            "transaction classification."
        )
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=(
            "AHBot config file. "
            "Default: bin/config.yaml"
        ),
    )

    return parser.parse_args()


def clean_text(
    value: Any,
) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass

    text_value = str(value).strip()

    if text_value.lower() in {
        "",
        "none",
        "nan",
        "null",
    }:
        return ""

    return text_value


def as_int(
    value: Any,
) -> int:
    if value is None:
        return 0

    try:
        if pd.isna(value):
            return 0
    except TypeError:
        pass

    try:
        return int(value)
    except (
        TypeError,
        ValueError,
    ):
        return 0


def normalize_name(
    value: Any,
) -> str:
    return (
        clean_text(value)
        .casefold()
    )


def iso_timestamp(
    value: int,
) -> str:
    if value <= 0:
        return ""

    return (
        datetime.fromtimestamp(
            value,
            tz=timezone.utc,
        )
        .isoformat()
    )


def classify_actor(
    actor_id: int,
    actor_name: str,
    bot_name: str,
) -> str:
    normalized = (
        normalize_name(
            actor_name
        )
    )

    if (
        actor_id == 0
        and normalized == bot_name
    ):
        return "AHBOT"

    if (
        actor_id > 0
        and normalized
        and normalized != bot_name
    ):
        return "PLAYER"

    return "UNKNOWN"


def classify_transaction(
    seller_role: str,
    buyer_role: str,
) -> str:
    if (
        seller_role == "AHBOT"
        and buyer_role == "AHBOT"
    ):
        return CLASS_SYNTHETIC

    if (
        seller_role == "PLAYER"
        and buyer_role == "AHBOT"
    ):
        return CLASS_PLAYER_SUPPLY

    if (
        seller_role == "AHBOT"
        and buyer_role == "PLAYER"
    ):
        return CLASS_PLAYER_DEMAND

    if (
        seller_role == "PLAYER"
        and buyer_role == "PLAYER"
    ):
        return CLASS_ORGANIC

    return CLASS_UNCLASSIFIED


def main() -> int:
    args = parse_args()

    config_path = (
        args.config
        .expanduser()
        .resolve()
    )

    if not config_path.exists():
        raise MarketHistoryClassifierError(
            "Missing AHBot configuration: "
            f"{config_path}"
        )

    config = Config.from_yaml(
        config_path
    )

    bot_name_display = clean_text(
        config.name
    )

    bot_name = (
        bot_name_display
        .casefold()
    )

    if not bot_name:
        raise MarketHistoryClassifierError(
            "AHBot name is empty in config."
        )

    password = config.password

    if hasattr(
        password,
        "get_secret_value",
    ):
        password = (
            password
            .get_secret_value()
        )

    db = Database.pymysql(
        hostname=config.hostname,
        database=config.database,
        username=config.username,
        password=str(password),
        port=config.port,
    )

    query = text(
        """
        SELECT
            id,
            itemid,
            stack,
            seller,
            seller_name,
            date,
            price,
            buyer,
            buyer_name,
            sale,
            sell_date
        FROM auction_house
        WHERE sell_date <> 0
        ORDER BY sell_date ASC, id ASC
        """
    )

    # This tool is deliberately read-only.
    # It executes only the SELECT above and never
    # mutates auction_house or any other DB table.
    with db.engine.connect() as connection:
        history = pd.read_sql_query(
            query,
            connection,
        )

    rows: list[dict[str, Any]] = []

    for _, row in history.iterrows():
        row_id = as_int(
            row.get("id")
        )

        itemid = as_int(
            row.get("itemid")
        )

        stack = as_int(
            row.get("stack")
        )

        seller = as_int(
            row.get("seller")
        )

        seller_name = clean_text(
            row.get("seller_name")
        )

        buyer = as_int(
            row.get("buyer")
        )

        buyer_name = clean_text(
            row.get("buyer_name")
        )

        listed_timestamp = as_int(
            row.get("date")
        )

        sold_timestamp = as_int(
            row.get("sell_date")
        )

        ask_price = as_int(
            row.get("price")
        )

        bid_price = as_int(
            row.get("sale")
        )

        seller_role = classify_actor(
            seller,
            seller_name,
            bot_name,
        )

        buyer_role = classify_actor(
            buyer,
            buyer_name,
            bot_name,
        )

        transaction_class = (
            classify_transaction(
                seller_role,
                buyer_role,
            )
        )

        seller_is_player = int(
            seller_role == "PLAYER"
        )

        buyer_is_player = int(
            buyer_role == "PLAYER"
        )

        seller_is_bot = int(
            seller_role == "AHBOT"
        )

        buyer_is_bot = int(
            buyer_role == "AHBOT"
        )

        synthetic_history = int(
            transaction_class
            == CLASS_SYNTHETIC
        )

        organic_trade = int(
            transaction_class
            == CLASS_ORGANIC
        )

        # Player-originated ask evidence exists
        # whenever the seller is a real player.
        ask_evidence_valid = int(
            seller_role == "PLAYER"
        )

        # Player-originated bid evidence exists
        # whenever the buyer is a real player.
        bid_evidence_valid = int(
            buyer_role == "PLAYER"
        )

        supply_evidence = int(
            seller_role == "PLAYER"
        )

        demand_evidence = int(
            buyer_role == "PLAYER"
        )

        # Listing duration is only meaningful for
        # player sellers. AHBot intentionally uses
        # future-dated active listings.
        listing_duration_valid = int(
            seller_role == "PLAYER"
            and listed_timestamp > 0
            and sold_timestamp >= listed_timestamp
        )

        listing_duration_seconds = None

        if listing_duration_valid:
            listing_duration_seconds = (
                sold_timestamp
                - listed_timestamp
            )

        completed_row_valid = int(
            sold_timestamp > 0
            and bid_price > 0
        )

        ask_price_valid = int(
            ask_price > 0
        )

        bid_price_valid = int(
            bid_price > 0
        )

        bid_at_or_above_ask = int(
            ask_price > 0
            and bid_price >= ask_price
        )

        price_gap = None
        bid_over_ask_ratio = None

        if (
            ask_price > 0
            and bid_price > 0
        ):
            price_gap = (
                bid_price
                - ask_price
            )

            bid_over_ask_ratio = (
                bid_price
                / ask_price
            )

        actor_roles_valid = int(
            seller_role != "UNKNOWN"
            and buyer_role != "UNKNOWN"
        )

        evidence_classified = int(
            transaction_class
            != CLASS_UNCLASSIFIED
        )

        rows.append({
            "id":
                row_id,

            "itemid":
                itemid,

            "stack":
                stack,

            "seller":
                seller,

            "seller_name":
                seller_name,

            "seller_role":
                seller_role,

            "buyer":
                buyer,

            "buyer_name":
                buyer_name,

            "buyer_role":
                buyer_role,

            "listed_timestamp":
                listed_timestamp,

            "listed_at_utc":
                iso_timestamp(
                    listed_timestamp
                ),

            "sold_timestamp":
                sold_timestamp,

            "sold_at_utc":
                iso_timestamp(
                    sold_timestamp
                ),

            "ask_price":
                ask_price,

            "bid_price":
                bid_price,

            "price_gap":
                price_gap,

            "bid_over_ask_ratio":
                bid_over_ask_ratio,

            "transaction_class":
                transaction_class,

            "seller_is_player":
                seller_is_player,

            "buyer_is_player":
                buyer_is_player,

            "seller_is_bot":
                seller_is_bot,

            "buyer_is_bot":
                buyer_is_bot,

            "synthetic_history":
                synthetic_history,

            "organic_trade":
                organic_trade,

            "ask_evidence_valid":
                ask_evidence_valid,

            "bid_evidence_valid":
                bid_evidence_valid,

            "supply_evidence":
                supply_evidence,

            "demand_evidence":
                demand_evidence,

            "listing_duration_valid":
                listing_duration_valid,

            "listing_duration_seconds":
                listing_duration_seconds,

            "completed_row_valid":
                completed_row_valid,

            "ask_price_valid":
                ask_price_valid,

            "bid_price_valid":
                bid_price_valid,

            "bid_at_or_above_ask":
                bid_at_or_above_ask,

            "actor_roles_valid":
                actor_roles_valid,

            "evidence_classified":
                evidence_classified,

            # Explicit safety firewall.
            "price_influence_ready":
                0,

            "stock_influence_ready":
                0,

            "activation_ready":
                0,

            "auto_live_promotion":
                0,
        })

    output = pd.DataFrame(
        rows
    )

    if output.empty:
        raise MarketHistoryClassifierError(
            "No completed auction-house "
            "transactions were found."
        )

    class_order = [
        CLASS_SYNTHETIC,
        CLASS_PLAYER_SUPPLY,
        CLASS_PLAYER_DEMAND,
        CLASS_ORGANIC,
        CLASS_UNCLASSIFIED,
    ]

    class_counts = {
        key: int(
            (
                output[
                    "transaction_class"
                ]
                == key
            ).sum()
        )
        for key in class_order
    }

    unclassified_rows = int(
        class_counts[
            CLASS_UNCLASSIFIED
        ]
    )

    invalid_completion_rows = int(
        (
            output[
                "completed_row_valid"
            ]
            != 1
        ).sum()
    )

    invalid_actor_rows = int(
        (
            output[
                "actor_roles_valid"
            ]
            != 1
        ).sum()
    )

    bid_below_ask_rows = int(
        (
            output[
                "bid_at_or_above_ask"
            ]
            != 1
        ).sum()
    )

    negative_player_duration_rows = int(
        (
            (
                output[
                    "seller_is_player"
                ]
                == 1
            )
            & (
                output[
                    "listing_duration_valid"
                ]
                != 1
            )
        ).sum()
    )

    player_involved = output[
        (
            output[
                "seller_is_player"
            ]
            == 1
        )
        | (
            output[
                "buyer_is_player"
            ]
            == 1
        )
    ]

    player_ask_evidence = output[
        output[
            "ask_evidence_valid"
        ]
        == 1
    ]

    player_bid_evidence = output[
        output[
            "bid_evidence_valid"
        ]
        == 1
    ]

    organic = output[
        output[
            "organic_trade"
        ]
        == 1
    ]

    integrity_failures = (
        unclassified_rows
        + invalid_completion_rows
        + invalid_actor_rows
        + bid_below_ask_rows
        + negative_player_duration_rows
    )

    status = (
        "PASS"
        if integrity_failures == 0
        else "FAIL"
    )

    summary = {
        "status":
            status,

        "stage":
            "2A.2_MARKET_HISTORY_CLASSIFIER",

        "bot_name":
            bot_name_display,

        "completed_transactions":
            int(len(output)),

        "transaction_class_counts":
            class_counts,

        "synthetic_history_rows":
            int(
                class_counts[
                    CLASS_SYNTHETIC
                ]
            ),

        "player_supply_to_bot_rows":
            int(
                class_counts[
                    CLASS_PLAYER_SUPPLY
                ]
            ),

        "player_demand_from_bot_rows":
            int(
                class_counts[
                    CLASS_PLAYER_DEMAND
                ]
            ),

        "organic_player_trade_rows":
            int(
                class_counts[
                    CLASS_ORGANIC
                ]
            ),

        "unclassified_rows":
            unclassified_rows,

        "player_involved_transactions":
            int(
                len(
                    player_involved
                )
            ),

        "usable_player_ask_evidence_rows":
            int(
                len(
                    player_ask_evidence
                )
            ),

        "usable_player_bid_evidence_rows":
            int(
                len(
                    player_bid_evidence
                )
            ),

        "organic_price_observations":
            int(
                len(
                    organic
                )
            ),

        "valid_player_listing_durations":
            int(
                output[
                    "listing_duration_valid"
                ].sum()
            ),

        "invalid_completion_rows":
            invalid_completion_rows,

        "invalid_actor_rows":
            invalid_actor_rows,

        "bid_below_ask_rows":
            bid_below_ask_rows,

        "invalid_player_listing_duration_rows":
            negative_player_duration_rows,

        "integrity_failures":
            int(
                integrity_failures
            ),

        "history_price_influence_ready":
            0,

        "history_stock_influence_ready":
            0,

        "activation_ready":
            0,

        "auto_live_promotions":
            0,

        "generated_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),
    }

    REPORTS.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
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
    print("=" * 84)
    print(
        " Phase 2A.2 Market History "
        "Transaction Classifier"
    )
    print("=" * 84)

    print()
    print(
        f"Completed transactions:          "
        f"{len(output):>6}"
    )

    print()
    print("Transaction classes:")

    for key in class_order:
        print(
            f"  {key:<30} "
            f"{class_counts[key]:>6}"
        )

    print()
    print(
        f"Player-involved transactions:    "
        f"{len(player_involved):>6}"
    )

    print(
        f"Usable player ask evidence:      "
        f"{len(player_ask_evidence):>6}"
    )

    print(
        f"Usable player bid evidence:      "
        f"{len(player_bid_evidence):>6}"
    )

    print(
        f"Organic price observations:      "
        f"{len(organic):>6}"
    )

    print(
        f"Valid player listing durations:  "
        f"{int(output['listing_duration_valid'].sum()):>6}"
    )

    print()
    print("Integrity:")

    print(
        f"  Unclassified rows:             "
        f"{unclassified_rows:>6}"
    )

    print(
        f"  Invalid completion rows:       "
        f"{invalid_completion_rows:>6}"
    )

    print(
        f"  Invalid actor rows:            "
        f"{invalid_actor_rows:>6}"
    )

    print(
        f"  Bid below ask rows:            "
        f"{bid_below_ask_rows:>6}"
    )

    print(
        f"  Invalid player durations:      "
        f"{negative_player_duration_rows:>6}"
    )

    print(
        f"  Total integrity failures:      "
        f"{integrity_failures:>6}"
    )

    print()
    print(
        "History price influence ready:       0"
    )

    print(
        "History stock influence ready:       0"
    )

    print(
        "Activation ready:                    0"
    )

    print(
        "Auto live promotions:                0"
    )

    print()
    print(
        f"Transactions: {OUTPUT_FILE}"
    )

    print(
        f"Summary:      {SUMMARY_FILE}"
    )

    print()
    print(status)

    if integrity_failures:
        raise MarketHistoryClassifierError(
            "Market-history classifier "
            f"found {integrity_failures} "
            "integrity failures."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
