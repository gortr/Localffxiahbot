from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ffxiahbot.auction.manager import Manager
from ffxiahbot.config import Config
from ffxiahbot.tables.auctionhouse import AuctionHouse


ROOT = Path.home() / "ffxiahbot"

GENERATED = (
    ROOT
    / "economy"
    / "generated"
)

OBS_ROOT = (
    ROOT
    / "economy"
    / "runtime-observations"
    / "gil-flow"
)

SINK_ROOT = (
    OBS_ROOT
    / "sink"
)

CONFIG_FILE = (
    ROOT
    / "bin"
    / "config.yaml"
)

SELLER_RUNTIME = (
    GENERATED
    / "market-sell.csv"
)

BUYER_RUNTIME = (
    GENERATED
    / "market-buy.csv"
)

BASELINE_JSON = (
    OBS_ROOT
    / "baseline.json"
)

BASELINE_TRANSACTIONS = (
    OBS_ROOT
    / "baseline-completed-transactions.csv"
)


class SinkLedgerError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(
                1024 * 1024
            ),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


def utc_iso(epoch: int) -> str:
    if not epoch:
        return ""

    return datetime.fromtimestamp(
        epoch,
        tz=timezone.utc,
    ).isoformat()


def as_int(value: Any) -> int:
    if value is None:
        return 0

    try:
        if pd.isna(value):
            return 0
    except TypeError:
        pass

    return int(float(value))


def as_float(value: Any) -> float:
    if value is None:
        return 0.0

    try:
        if pd.isna(value):
            return 0.0
    except TypeError:
        pass

    return float(value)


def classify_active(
    row: AuctionHouse,
    bot_name: str,
) -> str:
    seller_name = str(
        row.seller_name or ""
    ).strip()

    if (
        int(row.seller) == 0
        and seller_name == bot_name
    ):
        return "AHBOT"

    if int(row.seller) > 0:
        return "PLAYER"

    return "UNKNOWN"


def classify_completed(
    row: AuctionHouse,
    bot_name: str,
) -> str:
    seller_name = str(
        row.seller_name or ""
    ).strip()

    buyer_name = str(
        row.buyer_name or ""
    ).strip()

    seller_bot = (
        int(row.seller) == 0
        and seller_name == bot_name
    )

    seller_player = (
        int(row.seller) > 0
    )

    buyer_bot = (
        buyer_name == bot_name
    )

    buyer_player = (
        bool(buyer_name)
        and not buyer_bot
    )

    if seller_bot and buyer_bot:
        return "SYNTHETIC_HISTORY"

    if seller_player and buyer_bot:
        return "PLAYER_SUPPLY_TO_BOT"

    if seller_bot and buyer_player:
        return "PLAYER_DEMAND_FROM_BOT"

    if seller_player and buyer_player:
        return "ORGANIC_PLAYER_TRADE"

    return "UNCLASSIFIED"


def main() -> int:
    required = [
        CONFIG_FILE,
        SELLER_RUNTIME,
        BUYER_RUNTIME,
        BASELINE_JSON,
        BASELINE_TRANSACTIONS,
    ]

    for path in required:
        if not path.exists():
            raise SinkLedgerError(
                f"Missing required input: {path}"
            )

    baseline = json.loads(
        BASELINE_JSON.read_text(
            encoding="utf-8",
        )
    )

    if baseline.get("status") != "PASS":
        raise SinkLedgerError(
            "3E.1 baseline is not PASS."
        )

    if not baseline.get(
        "baseline_locked",
        False,
    ):
        raise SinkLedgerError(
            "3E.1 baseline is not locked."
        )

    baseline_epoch = as_int(
        baseline.get(
            "baseline_epoch"
        )
    )

    if baseline_epoch <= 0:
        raise SinkLedgerError(
            "Invalid baseline epoch."
        )

    seller_sha = sha256(
        SELLER_RUNTIME
    )

    buyer_sha = sha256(
        BUYER_RUNTIME
    )

    if (
        seller_sha
        != baseline.get(
            "seller_runtime_sha256"
        )
    ):
        raise SinkLedgerError(
            "Seller runtime changed since "
            "the 3E baseline."
        )

    if (
        buyer_sha
        != baseline.get(
            "buyer_runtime_sha256"
        )
    ):
        raise SinkLedgerError(
            "Buyer runtime changed since "
            "the 3E baseline."
        )

    seller = pd.read_csv(
        SELLER_RUNTIME,
        low_memory=False,
    )

    baseline_transactions = pd.read_csv(
        BASELINE_TRANSACTIONS,
        low_memory=False,
    )

    baseline_ids = set()

    if not baseline_transactions.empty:
        baseline_ids = set(
            baseline_transactions[
                "id"
            ].astype(int)
        )

    seller_ids = set(
        seller[
            "itemid"
        ].astype(int)
    )

    seller_by_id = (
        seller.set_index(
            "itemid"
        )
    )

    config = Config.from_yaml(
        CONFIG_FILE
    )

    bot_name = str(
        config.name
    ).strip()

    restock_seconds = as_int(
        config.restock
    )

    if restock_seconds <= 0:
        raise SinkLedgerError(
            "Invalid restock interval."
        )

    restocks_per_day = (
        86400.0
        / restock_seconds
    )

    use_selling_rates = bool(
        config.use_selling_rates
    )

    manager = (
        Manager.create_database_and_manager(
            hostname=config.hostname,
            database=config.database,
            username=config.username,
            password=config.password,
            port=config.port,
            name=config.name,
            fail=True,
        )
    )

    if not manager.can_connect():
        raise SinkLedgerError(
            "Cannot connect to auction database."
        )

    observation_epoch = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    observation_utc = utc_iso(
        observation_epoch
    )

    # --------------------------------------------------
    # Seller policy
    #
    # stock_* is a TOTAL MARKET supply target.
    # It must not be interpreted as required AHBot stock.
    # --------------------------------------------------

    policy_records = []

    for _, row in seller.iterrows():
        iid = as_int(
            row["itemid"]
        )

        name = str(
            row["name"]
        )

        for stack in [0, 1]:
            enabled = as_int(
                row[
                    "sell_stacks"
                    if stack
                    else "sell_single"
                ]
            )

            price = as_int(
                row[
                    "price_stacks"
                    if stack
                    else "price_single"
                ]
            )

            stock_target = as_int(
                row[
                    "stock_stacks"
                    if stack
                    else "stock_single"
                ]
            )

            sell_rate = as_float(
                row[
                    "sell_rate_stacks"
                    if stack
                    else "sell_rate_single"
                ]
            )

            policy_records.append({
                "itemid":
                    iid,

                "name":
                    name,

                "stack":
                    stack,

                "form":
                    (
                        "STACK"
                        if stack
                        else "SINGLE"
                    ),

                "sell_enabled":
                    enabled,

                "configured_price":
                    price,

                "total_market_stock_target":
                    stock_target,

                "sell_rate":
                    sell_rate,

                "configured_total_market_target_ask_value":
                    (
                        price
                        * stock_target
                        if enabled
                        else 0
                    ),
            })

    policy = pd.DataFrame(
        policy_records
    )

    enabled_policy = policy[
        policy[
            "sell_enabled"
        ].astype(int)
        == 1
    ].copy()

    active_records = []
    event_records = []

    integrity_failures = []
    review_flags = []

    with manager.scoped_session() as session:
        # Read all active rows so orphaned AHBot
        # seller listings cannot hide outside the
        # current seller CSV.
        active_rows = (
            session.query(
                AuctionHouse
            )
            .filter(
                AuctionHouse.sale == 0,
                AuctionHouse.sell_date == 0,
            )
            .all()
        )

        for row in active_rows:
            buyer_name = str(
                row.buyer_name or ""
            ).strip()

            if buyer_name:
                integrity_failures.append({
                    "id":
                        int(row.id),

                    "reason":
                        "ACTIVE_ROW_HAS_BUYER_NAME",
                })

                continue

            iid = int(
                row.itemid
            )

            stack = int(
                row.stack
            )

            role = classify_active(
                row,
                bot_name,
            )

            # Keep:
            #   all current seller-catalog supply
            #   all AHBot supply even if orphaned
            if (
                iid not in seller_ids
                and role != "AHBOT"
            ):
                continue

            in_runtime = int(
                iid in seller_ids
            )

            sell_enabled = 0
            configured_price = None
            stock_target = None
            sell_rate = None
            name = ""

            if in_runtime:
                sr = seller_by_id.loc[
                    iid
                ]

                name = str(
                    sr["name"]
                )

                sell_enabled = as_int(
                    sr[
                        "sell_stacks"
                        if stack
                        else "sell_single"
                    ]
                )

                configured_price = as_int(
                    sr[
                        "price_stacks"
                        if stack
                        else "price_single"
                    ]
                )

                stock_target = as_int(
                    sr[
                        "stock_stacks"
                        if stack
                        else "stock_single"
                    ]
                )

                sell_rate = as_float(
                    sr[
                        "sell_rate_stacks"
                        if stack
                        else "sell_rate_single"
                    ]
                )

            if (
                role == "AHBOT"
                and not in_runtime
            ):
                integrity_failures.append({
                    "id":
                        int(row.id),

                    "itemid":
                        iid,

                    "reason":
                        "ACTIVE_AHBOT_LISTING_"
                        "OUTSIDE_SELLER_RUNTIME",
                })

            if (
                role == "AHBOT"
                and in_runtime
                and not sell_enabled
            ):
                integrity_failures.append({
                    "id":
                        int(row.id),

                    "itemid":
                        iid,

                    "reason":
                        "ACTIVE_AHBOT_LISTING_"
                        "ON_DISABLED_FORM",
                })

            ask = int(
                row.price or 0
            )

            active_records.append({
                "id":
                    int(row.id),

                "itemid":
                    iid,

                "name":
                    name,

                "stack":
                    stack,

                "form":
                    (
                        "STACK"
                        if stack
                        else "SINGLE"
                    ),

                "seller":
                    int(row.seller),

                "seller_name":
                    str(
                        row.seller_name or ""
                    ).strip(),

                "seller_role":
                    role,

                "ask_price":
                    ask,

                "in_current_seller_runtime":
                    in_runtime,

                "sell_enabled":
                    sell_enabled,

                "configured_price":
                    configured_price,

                "ask_matches_runtime":
                    int(
                        in_runtime
                        and configured_price
                        is not None
                        and ask
                        == configured_price
                    ),

                "total_market_stock_target":
                    stock_target,

                "sell_rate":
                    sell_rate,

                "listing_date":
                    int(
                        row.date or 0
                    ),

                "listing_date_utc":
                    utc_iso(
                        int(
                            row.date or 0
                        )
                    ),
            })

        completed_rows = (
            session.query(
                AuctionHouse
            )
            .filter(
                AuctionHouse.sell_date
                >= baseline_epoch
            )
            .all()
        )

        for row in completed_rows:
            row_id = int(
                row.id
            )

            if row_id in baseline_ids:
                continue

            classification = (
                classify_completed(
                    row,
                    bot_name,
                )
            )

            if (
                classification
                != "PLAYER_DEMAND_FROM_BOT"
            ):
                continue

            iid = int(
                row.itemid
            )

            stack = int(
                row.stack
            )

            ask = int(
                row.price or 0
            )

            paid = int(
                row.sale or 0
            )

            in_runtime = int(
                iid in seller_ids
            )

            sell_enabled = 0
            configured_price = None
            stock_target = None
            sell_rate = None
            name = ""

            if in_runtime:
                sr = seller_by_id.loc[
                    iid
                ]

                name = str(
                    sr["name"]
                )

                sell_enabled = as_int(
                    sr[
                        "sell_stacks"
                        if stack
                        else "sell_single"
                    ]
                )

                configured_price = as_int(
                    sr[
                        "price_stacks"
                        if stack
                        else "price_single"
                    ]
                )

                stock_target = as_int(
                    sr[
                        "stock_stacks"
                        if stack
                        else "stock_single"
                    ]
                )

                sell_rate = as_float(
                    sr[
                        "sell_rate_stacks"
                        if stack
                        else "sell_rate_single"
                    ]
                )

            if not in_runtime:
                integrity_failures.append({
                    "id":
                        row_id,

                    "itemid":
                        iid,

                    "reason":
                        "POSTBASELINE_AHBOT_SALE_"
                        "OUTSIDE_SELLER_RUNTIME",
                })

            elif not sell_enabled:
                integrity_failures.append({
                    "id":
                        row_id,

                    "itemid":
                        iid,

                    "reason":
                        "POSTBASELINE_AHBOT_SALE_"
                        "ON_DISABLED_FORM",
                })

            if paid <= 0:
                integrity_failures.append({
                    "id":
                        row_id,

                    "reason":
                        "POSTBASELINE_AHBOT_SALE_"
                        "NONPOSITIVE_PAYMENT",
                })

            if (
                ask > 0
                and paid < ask
            ):
                integrity_failures.append({
                    "id":
                        row_id,

                    "reason":
                        "POSTBASELINE_AHBOT_SALE_"
                        "BELOW_LISTING_ASK",
                })

            if (
                in_runtime
                and configured_price is not None
                and ask != configured_price
            ):
                review_flags.append({
                    "id":
                        row_id,

                    "itemid":
                        iid,

                    "reason":
                        "SOLD_LISTING_ASK_DIFFERS_"
                        "FROM_CURRENT_RUNTIME",
                })

            listing_date = int(
                row.date or 0
            )

            sell_date = int(
                row.sell_date or 0
            )

            duration_seconds = None

            if (
                listing_date
                and sell_date >= listing_date
            ):
                duration_seconds = (
                    sell_date
                    - listing_date
                )

            event_records.append({
                "id":
                    row_id,

                "itemid":
                    iid,

                "name":
                    name,

                "stack":
                    stack,

                "form":
                    (
                        "STACK"
                        if stack
                        else "SINGLE"
                    ),

                "buyer_name":
                    str(
                        row.buyer_name or ""
                    ).strip(),

                "ask_price":
                    ask,

                "paid_price":
                    paid,

                "player_overbid_above_ask":
                    paid - ask,

                "in_current_seller_runtime":
                    in_runtime,

                "sell_enabled":
                    sell_enabled,

                "configured_price":
                    configured_price,

                "ask_matches_runtime":
                    int(
                        in_runtime
                        and configured_price
                        is not None
                        and ask
                        == configured_price
                    ),

                "total_market_stock_target":
                    stock_target,

                "sell_rate":
                    sell_rate,

                "listing_date":
                    listing_date,

                "listing_date_utc":
                    utc_iso(
                        listing_date
                    ),

                "sell_date":
                    sell_date,

                "sell_date_utc":
                    utc_iso(
                        sell_date
                    ),

                "sale_duration_seconds":
                    duration_seconds,

                "sale_duration_hours":
                    (
                        round(
                            duration_seconds
                            / 3600,
                            6,
                        )
                        if duration_seconds
                        is not None
                        else None
                    ),

                "seconds_after_baseline":
                    sell_date
                    - baseline_epoch,
            })

        if (
            session.new
            or session.dirty
            or session.deleted
        ):
            raise SinkLedgerError(
                "Read-only sink ledger created "
                "unexpected DB mutations."
            )

    active = pd.DataFrame(
        active_records
    )

    events = pd.DataFrame(
        event_records
    )

    if active.empty:
        active = pd.DataFrame(
            columns=[
                "id",
                "itemid",
                "name",
                "stack",
                "form",
                "seller",
                "seller_name",
                "seller_role",
                "ask_price",
                "in_current_seller_runtime",
                "sell_enabled",
                "configured_price",
                "ask_matches_runtime",
                "total_market_stock_target",
                "sell_rate",
                "listing_date",
                "listing_date_utc",
            ]
        )

    if events.empty:
        events = pd.DataFrame(
            columns=[
                "id",
                "itemid",
                "name",
                "stack",
                "form",
                "buyer_name",
                "ask_price",
                "paid_price",
                "player_overbid_above_ask",
                "in_current_seller_runtime",
                "sell_enabled",
                "configured_price",
                "ask_matches_runtime",
                "total_market_stock_target",
                "sell_rate",
                "listing_date",
                "listing_date_utc",
                "sell_date",
                "sell_date_utc",
                "sale_duration_seconds",
                "sale_duration_hours",
                "seconds_after_baseline",
            ]
        )

    ledger_records = []

    for _, p in enabled_policy.iterrows():
        iid = as_int(
            p["itemid"]
        )

        stack = as_int(
            p["stack"]
        )

        active_form = active[
            (
                active[
                    "itemid"
                ].astype(int)
                == iid
            )
            & (
                active[
                    "stack"
                ].astype(int)
                == stack
            )
        ]

        ahbot_active = active_form[
            active_form[
                "seller_role"
            ]
            == "AHBOT"
        ]

        player_active = active_form[
            active_form[
                "seller_role"
            ]
            == "PLAYER"
        ]

        unknown_active = active_form[
            active_form[
                "seller_role"
            ]
            == "UNKNOWN"
        ]

        events_form = events[
            (
                events[
                    "itemid"
                ].astype(int)
                == iid
            )
            & (
                events[
                    "stack"
                ].astype(int)
                == stack
            )
        ]

        configured_price = as_int(
            p[
                "configured_price"
            ]
        )

        stock_target = as_int(
            p[
                "total_market_stock_target"
            ]
        )

        active_total = (
            len(ahbot_active)
            + len(player_active)
            + len(unknown_active)
        )

        target_gap = max(
            stock_target
            - active_total,
            0,
        )

        current_ahbot_ask_value = (
            int(
                ahbot_active[
                    "ask_price"
                ].sum()
            )
            if len(
                ahbot_active
            )
            else 0
        )

        sink_gil = (
            int(
                events_form[
                    "paid_price"
                ].sum()
            )
            if len(
                events_form
            )
            else 0
        )

        ask_gil = (
            int(
                events_form[
                    "ask_price"
                ].sum()
            )
            if len(
                events_form
            )
            else 0
        )

        ledger_records.append({
            "itemid":
                iid,

            "name":
                str(
                    p["name"]
                ),

            "stack":
                stack,

            "form":
                str(
                    p["form"]
                ),

            "configured_price":
                configured_price,

            "total_market_stock_target":
                stock_target,

            "sell_rate":
                as_float(
                    p[
                        "sell_rate"
                    ]
                ),

            "active_ahbot_listings":
                len(
                    ahbot_active
                ),

            "active_player_listings":
                len(
                    player_active
                ),

            "active_unknown_listings":
                len(
                    unknown_active
                ),

            "active_total_market_listings":
                active_total,

            "target_gap_before_next_restock":
                target_gap,

            "current_ahbot_inventory_ask_value":
                current_ahbot_ask_value,

            "configured_total_market_target_ask_value":
                configured_price
                * stock_target,

            "postbaseline_sales":
                len(
                    events_form
                ),

            "postbaseline_listing_ask_gil":
                ask_gil,

            "postbaseline_player_gil_sink":
                sink_gil,

            "postbaseline_player_overbid_above_ask":
                sink_gil
                - ask_gil,
        })

    ledger = pd.DataFrame(
        ledger_records
    ).sort_values(
        [
            "itemid",
            "stack",
        ]
    )

    active_ahbot_listings = int(
        ledger[
            "active_ahbot_listings"
        ].sum()
    )

    active_player_listings = int(
        ledger[
            "active_player_listings"
        ].sum()
    )

    active_unknown_listings = int(
        ledger[
            "active_unknown_listings"
        ].sum()
    )

    total_target_gap = int(
        ledger[
            "target_gap_before_next_restock"
        ].sum()
    )

    current_ahbot_ask_value = int(
        ledger[
            "current_ahbot_inventory_ask_value"
        ].sum()
    )

    configured_market_target_value = int(
        ledger[
            "configured_total_market_target_ask_value"
        ].sum()
    )

    postbaseline_sales = int(
        ledger[
            "postbaseline_sales"
        ].sum()
    )

    postbaseline_ask_gil = int(
        ledger[
            "postbaseline_listing_ask_gil"
        ].sum()
    )

    postbaseline_sink = int(
        ledger[
            "postbaseline_player_gil_sink"
        ].sum()
    )

    postbaseline_overbid = int(
        ledger[
            "postbaseline_player_overbid_above_ask"
        ].sum()
    )

    active_orphan_bot = int(
        (
            (
                active[
                    "seller_role"
                ]
                == "AHBOT"
            )
            & (
                active[
                    "in_current_seller_runtime"
                ]
                .astype(int)
                == 0
            )
        ).sum()
    ) if len(active) else 0

    active_price_mismatch = int(
        (
            (
                active[
                    "seller_role"
                ]
                == "AHBOT"
            )
            & (
                active[
                    "in_current_seller_runtime"
                ]
                .astype(int)
                == 1
            )
            & (
                active[
                    "ask_matches_runtime"
                ]
                .astype(int)
                == 0
            )
        ).sum()
    ) if len(active) else 0

    if current_ahbot_ask_value:
        inventory_by_item = (
            ledger.groupby(
                "itemid"
            )[
                "current_ahbot_inventory_ask_value"
            ]
            .sum()
            .sort_values(
                ascending=False
            )
        )

        top_inventory_itemid = int(
            inventory_by_item.index[0]
        )

        top_inventory_value = int(
            inventory_by_item.iloc[0]
        )

        top_inventory_share = (
            top_inventory_value
            / current_ahbot_ask_value
        )
    else:
        top_inventory_itemid = None
        top_inventory_value = 0
        top_inventory_share = 0.0

    if configured_market_target_value:
        target_by_item = (
            ledger.groupby(
                "itemid"
            )[
                "configured_total_market_target_ask_value"
            ]
            .sum()
            .sort_values(
                ascending=False
            )
        )

        top_target_itemid = int(
            target_by_item.index[0]
        )

        top_target_value = int(
            target_by_item.iloc[0]
        )

        top_target_share = (
            top_target_value
            / configured_market_target_value
        )
    else:
        top_target_itemid = None
        top_target_value = 0
        top_target_share = 0.0

    integrity_count = (
        len(integrity_failures)
        + active_unknown_listings
    )

    if postbaseline_sales:
        sink_state = (
            "POSTBASELINE_SINK_ACTIVITY"
        )

    elif active_ahbot_listings:
        sink_state = (
            "AHBOT_INVENTORY_AVAILABLE_NO_"
            "POSTBASELINE_SALES"
        )

    else:
        sink_state = (
            "NO_CURRENT_AHBOT_SELLER_INVENTORY"
        )

    stamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    SINK_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    ledger_file = (
        SINK_ROOT
        / f"{stamp}-ledger.csv"
    )

    events_file = (
        SINK_ROOT
        / f"{stamp}-events.csv"
    )

    active_file = (
        SINK_ROOT
        / f"{stamp}-active-market.csv"
    )

    policy_file = (
        SINK_ROOT
        / f"{stamp}-seller-policy.csv"
    )

    summary_file = (
        SINK_ROOT
        / f"{stamp}-summary.json"
    )

    ledger.to_csv(
        ledger_file,
        index=False,
    )

    events.sort_values(
        [
            "sell_date",
            "id",
        ]
    ).to_csv(
        events_file,
        index=False,
    )

    active.sort_values(
        [
            "itemid",
            "stack",
            "id",
        ]
    ).to_csv(
        active_file,
        index=False,
    )

    enabled_policy.sort_values(
        [
            "itemid",
            "stack",
        ]
    ).to_csv(
        policy_file,
        index=False,
    )

    summary = {
        "status":
            (
                "PASS"
                if integrity_count == 0
                else "FAIL"
            ),

        "phase":
            "3E.3",

        "observation_utc":
            observation_utc,

        "baseline_utc":
            baseline.get(
                "baseline_utc"
            ),

        "baseline_epoch":
            baseline_epoch,

        "transaction_rule":
            (
                "sell_date >= baseline_epoch "
                "AND id not in baseline "
                "transaction IDs"
            ),

        "sink_state":
            sink_state,

        "seller_runtime_rows":
            len(seller),

        "enabled_seller_forms":
            len(
                enabled_policy
            ),

        "restock_seconds":
            restock_seconds,

        "restocks_per_day":
            restocks_per_day,

        "use_selling_rates":
            use_selling_rates,

        "stock_semantics":
            (
                "stock_single/stock_stacks are "
                "total-market supply targets; "
                "player listings suppress AHBot "
                "restocking"
            ),

        "baseline_historical_sink_gil":
            as_int(
                baseline.get(
                    "gross_sink_gil"
                )
            ),

        "postbaseline_sales":
            postbaseline_sales,

        "postbaseline_listing_ask_gil":
            postbaseline_ask_gil,

        "postbaseline_sink_gil":
            postbaseline_sink,

        "postbaseline_player_overbid_above_ask":
            postbaseline_overbid,

        "active_ahbot_listings":
            active_ahbot_listings,

        "active_player_listings_in_seller_catalog":
            active_player_listings,

        "active_unknown_listings":
            active_unknown_listings,

        "current_ahbot_inventory_ask_value":
            current_ahbot_ask_value,

        "configured_total_market_target_ask_value":
            configured_market_target_value,

        "target_gap_before_next_restock":
            total_target_gap,

        "active_ahbot_outside_runtime":
            active_orphan_bot,

        "active_ahbot_price_mismatches":
            active_price_mismatch,

        "top_current_inventory_itemid":
            top_inventory_itemid,

        "top_current_inventory_ask_value":
            top_inventory_value,

        "top_current_inventory_share":
            round(
                top_inventory_share,
                6,
            ),

        "top_configured_target_itemid":
            top_target_itemid,

        "top_configured_target_ask_value":
            top_target_value,

        "top_configured_target_share":
            round(
                top_target_share,
                6,
            ),

        "review_flags":
            len(
                review_flags
            ),

        "integrity_failures":
            integrity_count,

        "runtime_mutation_performed":
            0,

        "database_mutation_performed":
            0,

        "price_change_authorized":
            0,

        "rate_change_authorized":
            0,

        "stock_change_authorized":
            0,

        "catalog_change_authorized":
            0,

        "auto_live_promotion":
            0,

        "files": {
            "ledger":
                str(
                    ledger_file
                ),

            "events":
                str(
                    events_file
                ),

            "active_market":
                str(
                    active_file
                ),

            "seller_policy":
                str(
                    policy_file
                ),
        },

        "integrity_details":
            integrity_failures,

        "review_details":
            review_flags,
    }

    summary_file.write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print("=" * 110)
    print(
        " Phase 3E.3 AHBot Sink Ledger"
    )
    print("=" * 110)
    print()

    print(
        "Observation UTC:                         ",
        observation_utc,
    )

    print(
        "Baseline UTC:                            ",
        baseline.get(
            "baseline_utc"
        ),
    )

    print()
    print(
        "Seller runtime rows:                     ",
        len(seller),
    )

    print(
        "Enabled seller forms:                   ",
        len(
            enabled_policy
        ),
    )

    print(
        "Restock seconds:                        ",
        restock_seconds,
    )

    print(
        "Restocks per day:                       ",
        restocks_per_day,
    )

    print(
        "Selling rates enabled:                  ",
        int(
            use_selling_rates
        ),
    )

    print()
    print(
        "Historical sink at T0:                  ",
        as_int(
            baseline.get(
                "gross_sink_gil"
            )
        ),
    )

    print(
        "Post-baseline sales:                    ",
        postbaseline_sales,
    )

    print(
        "Post-baseline listing asks:             ",
        postbaseline_ask_gil,
    )

    print(
        "Post-baseline player gil sink:          ",
        postbaseline_sink,
    )

    print(
        "Player overbid above asks:              ",
        postbaseline_overbid,
    )

    print()
    print(
        "Active AHBot listings:                  ",
        active_ahbot_listings,
    )

    print(
        "Player listings in seller catalog:      ",
        active_player_listings,
    )

    print(
        "Unknown active listings:                ",
        active_unknown_listings,
    )

    print()
    print(
        "Current AHBot inventory ask value:      ",
        current_ahbot_ask_value,
    )

    print(
        "Configured total-market target value:   ",
        configured_market_target_value,
    )

    print(
        "Current total target gap:               ",
        total_target_gap,
    )

    print()
    print(
        "Active AHBot rows outside runtime:      ",
        active_orphan_bot,
    )

    print(
        "Active AHBot price mismatches:          ",
        active_price_mismatch,
    )

    print(
        "Review flags:                           ",
        len(
            review_flags
        ),
    )

    print()
    print(
        "Top current inventory item ID:          ",
        top_inventory_itemid,
    )

    print(
        "Top current inventory ask value:        ",
        top_inventory_value,
    )

    print(
        "Top current inventory share:            ",
        round(
            top_inventory_share,
            6,
        ),
    )

    print()
    print(
        "Integrity failures:                     ",
        integrity_count,
    )

    print()
    print(
        "Sink state:",
        sink_state,
    )

    print()
    print(
        "Runtime mutation performed:             0"
    )

    print(
        "Database mutation performed:            0"
    )

    print(
        "Price change authorized:                0"
    )

    print(
        "Rate change authorized:                 0"
    )

    print(
        "Stock change authorized:                0"
    )

    print(
        "Catalog change authorized:              0"
    )

    print(
        "Auto live promotion:                    0"
    )

    print()
    print(
        "Ledger:",
        ledger_file,
    )

    print(
        "Events:",
        events_file,
    )

    print(
        "Active market:",
        active_file,
    )

    print(
        "Seller policy:",
        policy_file,
    )

    print(
        "Summary:",
        summary_file,
    )

    print()
    print(summary["status"])

    if integrity_count:
        print()
        print("Integrity failures:")

        for failure in integrity_failures:
            print(
                " -",
                failure,
            )

        raise SystemExit(1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
