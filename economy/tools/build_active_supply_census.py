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
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

DEFAULT_CONFIG = ROOT / "bin" / "config.yaml"
SELLER_RUNTIME = GENERATED / "market-sell.csv"

OUTPUT_CSV = REPORTS / "active-supply-census.csv"
SUMMARY_JSON = REPORTS / "active-supply-census-summary.json"


class ActiveSupplyCensusError(RuntimeError):
    pass


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Build a read-only census comparing "
            "configured seller stock targets against "
            "live auction-house supply."
        )
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="AHBot config file.",
    )

    parser.add_argument(
        "--seller-csv",
        type=Path,
        default=SELLER_RUNTIME,
        help="Runtime seller CSV.",
    )

    return parser.parse_args()


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass

    value = str(value).strip()

    if value.lower() in {
        "",
        "none",
        "nan",
        "null",
    }:
        return ""

    return value


def as_int(value: Any) -> int:
    if value is None:
        return 0

    try:
        if pd.isna(value):
            return 0
    except TypeError:
        pass

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if value is None:
        return False

    try:
        if pd.isna(value):
            return False
    except TypeError:
        pass

    if isinstance(value, (int, float)):
        return bool(value)

    normalized = str(value).strip().casefold()

    if normalized in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }:
        return True

    if normalized in {
        "0",
        "false",
        "no",
        "n",
        "off",
        "",
    }:
        return False

    raise ActiveSupplyCensusError(
        f"Cannot interpret boolean value: {value!r}"
    )


def classify_actor(
    seller_id: int,
    seller_name: str,
    bot_name: str,
) -> str:
    normalized_name = (
        clean_text(seller_name)
        .casefold()
    )

    if (
        seller_id == 0
        and normalized_name == bot_name
    ):
        return "AHBOT"

    if (
        seller_id > 0
        and normalized_name
        and normalized_name != bot_name
    ):
        return "PLAYER"

    return "UNKNOWN"


def main() -> int:
    args = parse_args()

    config_path = (
        args.config
        .expanduser()
        .resolve()
    )

    seller_csv = (
        args.seller_csv
        .expanduser()
        .resolve()
    )

    if not config_path.exists():
        raise ActiveSupplyCensusError(
            f"Missing config: {config_path}"
        )

    if not seller_csv.exists():
        raise ActiveSupplyCensusError(
            f"Missing seller runtime: {seller_csv}"
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
        raise ActiveSupplyCensusError(
            "Configured AHBot name is empty."
        )

    seller = pd.read_csv(
        seller_csv
    )

    required_columns = {
        "itemid",
        "name",
        "sell_single",
        "sell_stacks",
        "price_single",
        "price_stacks",
        "stock_single",
        "stock_stacks",
    }

    missing = (
        required_columns
        - set(seller.columns)
    )

    if missing:
        raise ActiveSupplyCensusError(
            "Seller runtime missing columns: "
            + ", ".join(sorted(missing))
        )

    configured_forms: list[
        dict[str, Any]
    ] = []

    seen_keys: set[
        tuple[int, int]
    ] = set()

    for _, row in seller.iterrows():
        itemid = as_int(
            row["itemid"]
        )

        name = clean_text(
            row["name"]
        )

        forms = (
            (
                0,
                as_bool(
                    row["sell_single"]
                ),
                as_int(
                    row["stock_single"]
                ),
                as_int(
                    row["price_single"]
                ),
            ),
            (
                1,
                as_bool(
                    row["sell_stacks"]
                ),
                as_int(
                    row["stock_stacks"]
                ),
                as_int(
                    row["price_stacks"]
                ),
            ),
        )

        for (
            stack,
            enabled,
            target,
            price,
        ) in forms:
            if not enabled:
                continue

            if target < 0:
                raise ActiveSupplyCensusError(
                    "Negative stock target: "
                    f"itemid={itemid} "
                    f"stack={stack}"
                )

            if price <= 0:
                raise ActiveSupplyCensusError(
                    "Enabled seller form has "
                    "non-positive price: "
                    f"itemid={itemid} "
                    f"stack={stack}"
                )

            key = (
                itemid,
                stack,
            )

            if key in seen_keys:
                raise ActiveSupplyCensusError(
                    "Duplicate configured seller form: "
                    f"{key}"
                )

            seen_keys.add(key)

            configured_forms.append({
                "itemid":
                    itemid,

                "name":
                    name,

                "stack":
                    stack,

                "configured_price":
                    price,

                "configured_market_target":
                    target,
            })

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
        WHERE sale = 0
          AND sell_date = 0
          AND buyer_name IS NULL
        ORDER BY
            itemid,
            stack,
            price,
            id
        """
    )

    with db.engine.connect() as connection:
        active = pd.read_sql_query(
            query,
            connection,
        )

    if active.empty:
        active = pd.DataFrame(
            columns=[
                "id",
                "itemid",
                "stack",
                "seller",
                "seller_name",
                "date",
                "price",
                "buyer",
                "buyer_name",
                "sale",
                "sell_date",
            ]
        )

    active["seller_role"] = active.apply(
        lambda row: classify_actor(
            as_int(
                row["seller"]
            ),
            clean_text(
                row["seller_name"]
            ),
            bot_name,
        ),
        axis=1,
    )

    global_ahbot_rows = int(
        (
            active[
                "seller_role"
            ]
            == "AHBOT"
        ).sum()
    )

    global_player_rows = int(
        (
            active[
                "seller_role"
            ]
            == "PLAYER"
        ).sum()
    )

    global_unknown_rows = int(
        (
            active[
                "seller_role"
            ]
            == "UNKNOWN"
        ).sum()
    )

    census_rows: list[
        dict[str, Any]
    ] = []

    configured_active_ids: set[int] = set()

    for form in configured_forms:
        itemid = form["itemid"]
        stack = form["stack"]
        target = form[
            "configured_market_target"
        ]

        rows = active[
            (
                active["itemid"]
                == itemid
            )
            & (
                active["stack"]
                == stack
            )
        ].copy()

        for value in rows["id"]:
            configured_active_ids.add(
                as_int(value)
            )

        bot_rows = rows[
            rows[
                "seller_role"
            ]
            == "AHBOT"
        ]

        player_rows = rows[
            rows[
                "seller_role"
            ]
            == "PLAYER"
        ]

        unknown_rows = rows[
            rows[
                "seller_role"
            ]
            == "UNKNOWN"
        ]

        bot_count = int(
            len(bot_rows)
        )

        player_count = int(
            len(player_rows)
        )

        unknown_count = int(
            len(unknown_rows)
        )

        total_count = int(
            len(rows)
        )

        known_count = (
            bot_count
            + player_count
        )

        target_delta = (
            total_count
            - target
        )

        target_gap = max(
            target - total_count,
            0,
        )

        target_excess = max(
            total_count - target,
            0,
        )

        bot_needed_given_players = max(
            target - player_count,
            0,
        )

        bot_above_needed = max(
            bot_count
            - bot_needed_given_players,
            0,
        )

        bot_below_needed = max(
            bot_needed_given_players
            - bot_count,
            0,
        )

        if total_count == 0:
            source_status = "NO_SUPPLY"
        elif unknown_count:
            source_status = "UNKNOWN_PRESENT"
        elif bot_count and player_count:
            source_status = "MIXED"
        elif bot_count:
            source_status = "AHBOT_ONLY"
        elif player_count:
            source_status = "PLAYER_ONLY"
        else:
            source_status = "UNKNOWN_PRESENT"

        if total_count < target:
            target_status = "BELOW_TARGET"
        elif total_count > target:
            target_status = "ABOVE_TARGET"
        else:
            target_status = "AT_TARGET"

        if known_count > 0:
            bot_share = (
                bot_count
                / known_count
            )

            player_share = (
                player_count
                / known_count
            )
        else:
            bot_share = None
            player_share = None

        census_rows.append({
            "itemid":
                itemid,

            "name":
                form["name"],

            "stack":
                stack,

            "configured_price":
                form[
                    "configured_price"
                ],

            "configured_market_target":
                target,

            "ahbot_active_listings":
                bot_count,

            "player_active_listings":
                player_count,

            "unknown_active_listings":
                unknown_count,

            "total_active_listings":
                total_count,

            "target_delta":
                target_delta,

            "target_gap":
                target_gap,

            "target_excess":
                target_excess,

            "bot_needed_given_player_supply":
                bot_needed_given_players,

            "bot_above_needed":
                bot_above_needed,

            "bot_below_needed":
                bot_below_needed,

            "bot_share_known_supply":
                bot_share,

            "player_share_known_supply":
                player_share,

            "supply_source_status":
                source_status,

            "target_status":
                target_status,

            "no_active_supply":
                int(
                    total_count == 0
                ),

            "player_supply_meets_target":
                int(
                    target > 0
                    and player_count >= target
                ),

            "bot_majority_known_supply":
                int(
                    known_count > 0
                    and bot_count > player_count
                ),

            "bot_share_ge_75pct":
                int(
                    bot_share is not None
                    and bot_share >= 0.75
                ),

            "bot_above_player_complement":
                int(
                    bot_above_needed > 0
                ),

            # Observation only.
            "stock_change_authorized":
                0,

            "history_stock_influence_ready":
                0,

            "activation_ready":
                0,

            "auto_live_promotion":
                0,
        })

    census = pd.DataFrame(
        census_rows
    )

    if census.empty:
        raise ActiveSupplyCensusError(
            "No enabled seller forms found "
            "in runtime seller CSV."
        )

    configured_unknown_rows = int(
        census[
            "unknown_active_listings"
        ].sum()
    )

    configured_active_rows = int(
        census[
            "total_active_listings"
        ].sum()
    )

    unconfigured_active_rows = int(
        len(active)
        - len(configured_active_ids)
    )

    forms_below_target = int(
        (
            census[
                "target_status"
            ]
            == "BELOW_TARGET"
        ).sum()
    )

    forms_at_target = int(
        (
            census[
                "target_status"
            ]
            == "AT_TARGET"
        ).sum()
    )

    forms_above_target = int(
        (
            census[
                "target_status"
            ]
            == "ABOVE_TARGET"
        ).sum()
    )

    no_supply_forms = int(
        census[
            "no_active_supply"
        ].sum()
    )

    player_only_forms = int(
        (
            census[
                "supply_source_status"
            ]
            == "PLAYER_ONLY"
        ).sum()
    )

    bot_only_forms = int(
        (
            census[
                "supply_source_status"
            ]
            == "AHBOT_ONLY"
        ).sum()
    )

    mixed_forms = int(
        (
            census[
                "supply_source_status"
            ]
            == "MIXED"
        ).sum()
    )

    player_meets_target_forms = int(
        census[
            "player_supply_meets_target"
        ].sum()
    )

    bot_majority_forms = int(
        census[
            "bot_majority_known_supply"
        ].sum()
    )

    bot_75_forms = int(
        census[
            "bot_share_ge_75pct"
        ].sum()
    )

    bot_above_needed_forms = int(
        census[
            "bot_above_player_complement"
        ].sum()
    )

    integrity_failures = (
        configured_unknown_rows
    )

    safety_columns = (
        "stock_change_authorized",
        "history_stock_influence_ready",
        "activation_ready",
        "auto_live_promotion",
    )

    for column in safety_columns:
        if (
            census[column]
            != 0
        ).any():
            integrity_failures += 1

    summary = {
        "status":
            (
                "PASS"
                if integrity_failures == 0
                else "FAIL"
            ),

        "stage":
            "2B.1_ACTIVE_SUPPLY_CENSUS",

        "bot_name":
            bot_name_display,

        "configured_seller_rows":
            int(len(seller)),

        "configured_item_forms":
            int(len(census)),

        "global_active_auction_rows":
            int(len(active)),

        "global_ahbot_active_rows":
            global_ahbot_rows,

        "global_player_active_rows":
            global_player_rows,

        "global_unknown_active_rows":
            global_unknown_rows,

        "configured_active_rows":
            configured_active_rows,

        "unconfigured_active_rows":
            unconfigured_active_rows,

        "configured_unknown_active_rows":
            configured_unknown_rows,

        "forms_below_target":
            forms_below_target,

        "forms_at_target":
            forms_at_target,

        "forms_above_target":
            forms_above_target,

        "forms_with_no_supply":
            no_supply_forms,

        "player_only_forms":
            player_only_forms,

        "ahbot_only_forms":
            bot_only_forms,

        "mixed_supply_forms":
            mixed_forms,

        "player_supply_meets_target_forms":
            player_meets_target_forms,

        "bot_majority_forms":
            bot_majority_forms,

        "bot_share_ge_75pct_forms":
            bot_75_forms,

        "bot_above_player_complement_forms":
            bot_above_needed_forms,

        "integrity_failures":
            int(
                integrity_failures
            ),

        "stock_change_authorized":
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

    census.to_csv(
        OUTPUT_CSV,
        index=False,
    )

    SUMMARY_JSON.write_text(
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
        " Phase 2B.1 Active Supply Census"
    )
    print("=" * 84)

    print()
    print(
        f"Configured seller rows:          "
        f"{len(seller):>6}"
    )

    print(
        f"Configured item forms:           "
        f"{len(census):>6}"
    )

    print()
    print("Live auction supply:")

    print(
        f"  Total active rows:             "
        f"{len(active):>6}"
    )

    print(
        f"  AHBot active rows:             "
        f"{global_ahbot_rows:>6}"
    )

    print(
        f"  Player active rows:            "
        f"{global_player_rows:>6}"
    )

    print(
        f"  Unknown active rows:           "
        f"{global_unknown_rows:>6}"
    )

    print()
    print("Configured-market coverage:")

    print(
        f"  Active rows in census:         "
        f"{configured_active_rows:>6}"
    )

    print(
        f"  Active rows outside census:    "
        f"{unconfigured_active_rows:>6}"
    )

    print()
    print("Target state:")

    print(
        f"  Below target:                  "
        f"{forms_below_target:>6}"
    )

    print(
        f"  At target:                     "
        f"{forms_at_target:>6}"
    )

    print(
        f"  Above target:                  "
        f"{forms_above_target:>6}"
    )

    print(
        f"  No active supply:              "
        f"{no_supply_forms:>6}"
    )

    print()
    print("Supply source:")

    print(
        f"  AHBot only:                    "
        f"{bot_only_forms:>6}"
    )

    print(
        f"  Player only:                   "
        f"{player_only_forms:>6}"
    )

    print(
        f"  Mixed:                         "
        f"{mixed_forms:>6}"
    )

    print()
    print("Player / bot relationship:")

    print(
        f"  Player supply meets target:    "
        f"{player_meets_target_forms:>6}"
    )

    print(
        f"  AHBot majority supply:         "
        f"{bot_majority_forms:>6}"
    )

    print(
        f"  AHBot share >= 75%:            "
        f"{bot_75_forms:>6}"
    )

    print(
        f"  AHBot above player complement: "
        f"{bot_above_needed_forms:>6}"
    )

    print()
    print(
        f"Integrity failures:              "
        f"{integrity_failures:>6}"
    )

    print()
    print(
        "Stock change authorized:             0"
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
        f"Census:  {OUTPUT_CSV}"
    )

    print(
        f"Summary: {SUMMARY_JSON}"
    )

    print()

    if integrity_failures:
        print("FAIL")

        raise ActiveSupplyCensusError(
            "2B.1 active supply census "
            "found integrity failures."
        )

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
