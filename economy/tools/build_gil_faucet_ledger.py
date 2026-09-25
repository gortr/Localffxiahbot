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

FAUCET_ROOT = (
    OBS_ROOT
    / "faucet"
)

CONFIG_FILE = (
    ROOT
    / "bin"
    / "config.yaml"
)

BUYER_RUNTIME = (
    GENERATED
    / "market-buy.csv"
)

SELLER_RUNTIME = (
    GENERATED
    / "market-sell.csv"
)

MANAGER_FILE = (
    ROOT
    / "ffxiahbot"
    / "auction"
    / "manager.py"
)

BASELINE_JSON = (
    OBS_ROOT
    / "baseline.json"
)

BASELINE_TRANSACTIONS = (
    OBS_ROOT
    / "baseline-completed-transactions.csv"
)


class FaucetLedgerError(RuntimeError):
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
        BUYER_RUNTIME,
        SELLER_RUNTIME,
        MANAGER_FILE,
        BASELINE_JSON,
        BASELINE_TRANSACTIONS,
    ]

    for path in required:
        if not path.exists():
            raise FaucetLedgerError(
                f"Missing required input: {path}"
            )

    baseline = json.loads(
        BASELINE_JSON.read_text(
            encoding="utf-8",
        )
    )

    if baseline.get("status") != "PASS":
        raise FaucetLedgerError(
            "3E.1 baseline is not PASS."
        )

    if not baseline.get(
        "baseline_locked",
        False,
    ):
        raise FaucetLedgerError(
            "3E.1 baseline is not locked."
        )

    baseline_epoch = as_int(
        baseline.get(
            "baseline_epoch"
        )
    )

    if baseline_epoch <= 0:
        raise FaucetLedgerError(
            "Invalid baseline epoch."
        )

    buyer_sha = sha256(
        BUYER_RUNTIME
    )

    seller_sha = sha256(
        SELLER_RUNTIME
    )

    if (
        buyer_sha
        != baseline.get(
            "buyer_runtime_sha256"
        )
    ):
        raise FaucetLedgerError(
            "Buyer runtime changed since "
            "the 3E baseline."
        )

    if (
        seller_sha
        != baseline.get(
            "seller_runtime_sha256"
        )
    ):
        raise FaucetLedgerError(
            "Seller runtime changed since "
            "the 3E baseline."
        )

    buyer = pd.read_csv(
        BUYER_RUNTIME,
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

    buyer_ids = set(
        buyer[
            "itemid"
        ].astype(int)
    )

    buyer_by_id = (
        buyer.set_index(
            "itemid"
        )
    )

    config = Config.from_yaml(
        CONFIG_FILE
    )

    bot_name = str(
        config.name
    ).strip()

    tick_seconds = as_int(
        config.tick
    )

    if tick_seconds <= 0:
        raise FaucetLedgerError(
            "Invalid buyer tick."
        )

    cycles_per_day = (
        86400.0
        / tick_seconds
    )

    manager_text = (
        MANAGER_FILE.read_text(
            encoding="utf-8",
        )
    )

    rate_hardening_present = int(
        "rate_allowed_this_cycle"
        in manager_text
        and "purchase_key"
        in manager_text
    )

    if not rate_hardening_present:
        raise FaucetLedgerError(
            "One-roll-per-item/form buyer "
            "hardening is not present."
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
        raise FaucetLedgerError(
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

    policy_records = []

    for _, row in buyer.iterrows():
        iid = as_int(
            row["itemid"]
        )

        name = str(
            row["name"]
        )

        for stack in [0, 1]:
            enabled = as_int(
                row[
                    "buy_stacks"
                    if stack
                    else "buy_single"
                ]
            )

            bid = as_int(
                row[
                    "price_stacks"
                    if stack
                    else "price_single"
                ]
            )

            rate = as_float(
                row[
                    "buy_rate_stacks"
                    if stack
                    else "buy_rate_single"
                ]
            )

            expected_successes = (
                cycles_per_day
                * rate
                if enabled
                else 0.0
            )

            expected_gil = (
                expected_successes
                * bid
            )

            absolute_cycle_ceiling = (
                cycles_per_day
                * bid
                if enabled
                else 0.0
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

                "buy_enabled":
                    enabled,

                "configured_bid":
                    bid,

                "buy_rate":
                    rate,

                "tick_seconds":
                    tick_seconds,

                "cycles_per_day":
                    cycles_per_day,

                "expected_successes_per_day_continuous_supply":
                    round(
                        expected_successes,
                        6,
                    ),

                "expected_gil_per_day_continuous_supply":
                    round(
                        expected_gil,
                        6,
                    ),

                "absolute_rng_success_ceiling_gil_per_day":
                    round(
                        absolute_cycle_ceiling,
                        6,
                    ),
            })

    policy = pd.DataFrame(
        policy_records
    )

    enabled_policy = policy[
        policy[
            "buy_enabled"
        ].astype(int)
        == 1
    ].copy()

    active_records = []
    event_records = []
    integrity_failures = []

    with manager.scoped_session() as session:
        active_rows = (
            session.query(
                AuctionHouse
            )
            .filter(
                AuctionHouse.itemid.in_(
                    sorted(
                        buyer_ids
                    )
                ),
                AuctionHouse.sale == 0,
                AuctionHouse.sell_date == 0,
            )
            .all()
        )

        for row in active_rows:
            if int(
                row.seller
            ) <= 0:
                continue

            iid = int(
                row.itemid
            )

            stack = int(
                row.stack
            )

            br = buyer_by_id.loc[
                iid
            ]

            enabled = as_int(
                br[
                    "buy_stacks"
                    if stack
                    else "buy_single"
                ]
            )

            bid = as_int(
                br[
                    "price_stacks"
                    if stack
                    else "price_single"
                ]
            )

            rate = as_float(
                br[
                    "buy_rate_stacks"
                    if stack
                    else "buy_rate_single"
                ]
            )

            ask = int(
                row.price or 0
            )

            active_records.append({
                "id":
                    int(row.id),

                "itemid":
                    iid,

                "name":
                    str(
                        br["name"]
                    ),

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

                "ask_price":
                    ask,

                "buy_enabled":
                    enabled,

                "configured_bid":
                    bid,

                "buy_rate":
                    rate,

                "eligible_for_ahbot":
                    int(
                        enabled
                        and ask <= bid
                    ),

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
                != "PLAYER_SUPPLY_TO_BOT"
            ):
                continue

            iid = int(
                row.itemid
            )

            stack = int(
                row.stack
            )

            in_runtime = int(
                iid in buyer_ids
            )

            configured_bid = None
            buy_rate = None
            enabled = 0
            name = ""

            if in_runtime:
                br = buyer_by_id.loc[
                    iid
                ]

                name = str(
                    br["name"]
                )

                enabled = as_int(
                    br[
                        "buy_stacks"
                        if stack
                        else "buy_single"
                    ]
                )

                configured_bid = as_int(
                    br[
                        "price_stacks"
                        if stack
                        else "price_single"
                    ]
                )

                buy_rate = as_float(
                    br[
                        "buy_rate_stacks"
                        if stack
                        else "buy_rate_single"
                    ]
                )

            ask = int(
                row.price or 0
            )

            paid = int(
                row.sale or 0
            )

            if not in_runtime:
                integrity_failures.append({
                    "id":
                        row_id,

                    "reason":
                        "POST_BASELINE_AHBOT_BUY_"
                        "OUTSIDE_BUYER_RUNTIME",
                })

            elif not enabled:
                integrity_failures.append({
                    "id":
                        row_id,

                    "reason":
                        "POST_BASELINE_AHBOT_BUY_"
                        "DISABLED_FORM",
                })

            elif ask > configured_bid:
                integrity_failures.append({
                    "id":
                        row_id,

                    "reason":
                        "POST_BASELINE_AHBOT_BUY_"
                        "ABOVE_CONFIGURED_BID",
                })

            elif paid != configured_bid:
                integrity_failures.append({
                    "id":
                        row_id,

                    "reason":
                        "POST_BASELINE_AHBOT_PAYOUT_"
                        "DOES_NOT_MATCH_BID",
                })

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

                "seller":
                    int(row.seller),

                "seller_name":
                    str(
                        row.seller_name or ""
                    ).strip(),

                "ask_price":
                    ask,

                "paid_price":
                    paid,

                "payout_premium":
                    paid - ask,

                "sell_date":
                    int(
                        row.sell_date
                    ),

                "sell_date_utc":
                    utc_iso(
                        int(
                            row.sell_date
                        )
                    ),

                "seconds_after_baseline":
                    int(
                        row.sell_date
                    )
                    - baseline_epoch,

                "in_current_buyer_runtime":
                    in_runtime,

                "buy_enabled":
                    enabled,

                "configured_bid":
                    configured_bid,

                "buy_rate":
                    buy_rate,

                "ask_at_or_below_bid":
                    int(
                        in_runtime
                        and configured_bid
                        is not None
                        and ask
                        <= configured_bid
                    ),

                "payout_matches_bid":
                    int(
                        in_runtime
                        and configured_bid
                        is not None
                        and paid
                        == configured_bid
                    ),
            })

        if (
            session.new
            or session.dirty
            or session.deleted
        ):
            raise FaucetLedgerError(
                "Read-only faucet ledger created "
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
                "ask_price",
                "buy_enabled",
                "configured_bid",
                "buy_rate",
                "eligible_for_ahbot",
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
                "seller",
                "seller_name",
                "ask_price",
                "paid_price",
                "payout_premium",
                "sell_date",
                "sell_date_utc",
                "seconds_after_baseline",
                "in_current_buyer_runtime",
                "buy_enabled",
                "configured_bid",
                "buy_rate",
                "ask_at_or_below_bid",
                "payout_matches_bid",
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

        eligible = active_form[
            active_form[
                "eligible_for_ahbot"
            ].astype(int)
            == 1
        ]

        faucet_gil = (
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

        player_ask_gil = (
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

            "configured_bid":
                as_int(
                    p[
                        "configured_bid"
                    ]
                ),

            "buy_rate":
                as_float(
                    p[
                        "buy_rate"
                    ]
                ),

            "active_player_listings":
                len(
                    active_form
                ),

            "eligible_player_listings":
                len(
                    eligible
                ),

            "minimum_active_ask":
                (
                    int(
                        active_form[
                            "ask_price"
                        ].min()
                    )
                    if len(
                        active_form
                    )
                    else None
                ),

            "minimum_eligible_ask":
                (
                    int(
                        eligible[
                            "ask_price"
                        ].min()
                    )
                    if len(
                        eligible
                    )
                    else None
                ),

            "postbaseline_purchases":
                len(
                    events_form
                ),

            "player_ask_gil":
                player_ask_gil,

            "ahbot_faucet_gil":
                faucet_gil,

            "ahbot_premium_over_ask":
                faucet_gil
                - player_ask_gil,

            "expected_successes_per_day_continuous_supply":
                as_float(
                    p[
                        "expected_successes_per_day_continuous_supply"
                    ]
                ),

            "expected_gil_per_day_continuous_supply":
                as_float(
                    p[
                        "expected_gil_per_day_continuous_supply"
                    ]
                ),

            "absolute_rng_success_ceiling_gil_per_day":
                as_float(
                    p[
                        "absolute_rng_success_ceiling_gil_per_day"
                    ]
                ),
        })

    ledger = pd.DataFrame(
        ledger_records
    ).sort_values(
        [
            "itemid",
            "stack",
        ]
    )

    total_actual_faucet = int(
        ledger[
            "ahbot_faucet_gil"
        ].sum()
    )

    total_player_asks = int(
        ledger[
            "player_ask_gil"
        ].sum()
    )

    total_premium = int(
        ledger[
            "ahbot_premium_over_ask"
        ].sum()
    )

    total_purchases = int(
        ledger[
            "postbaseline_purchases"
        ].sum()
    )

    active_player_listings = int(
        ledger[
            "active_player_listings"
        ].sum()
    )

    eligible_player_listings = int(
        ledger[
            "eligible_player_listings"
        ].sum()
    )

    expected_daily_exposure = float(
        ledger[
            "expected_gil_per_day_continuous_supply"
        ].sum()
    )

    absolute_daily_ceiling = float(
        ledger[
            "absolute_rng_success_ceiling_gil_per_day"
        ].sum()
    )

    if total_actual_faucet:
        item_actual = (
            ledger.groupby(
                "itemid"
            )[
                "ahbot_faucet_gil"
            ]
            .sum()
            .sort_values(
                ascending=False
            )
        )

        top_item_id = int(
            item_actual.index[0]
        )

        top_item_gil = int(
            item_actual.iloc[0]
        )

        top_item_share = (
            top_item_gil
            / total_actual_faucet
        )
    else:
        top_item_id = None
        top_item_gil = 0
        top_item_share = 0.0

    if expected_daily_exposure:
        expected_by_item = (
            ledger.groupby(
                "itemid"
            )[
                "expected_gil_per_day_continuous_supply"
            ]
            .sum()
            .sort_values(
                ascending=False
            )
        )

        highest_exposure_item_id = int(
            expected_by_item.index[0]
        )

        highest_exposure_gil = float(
            expected_by_item.iloc[0]
        )

        highest_exposure_share = (
            highest_exposure_gil
            / expected_daily_exposure
        )
    else:
        highest_exposure_item_id = None
        highest_exposure_gil = 0.0
        highest_exposure_share = 0.0

    integrity_count = len(
        integrity_failures
    )

    if total_purchases:
        faucet_state = (
            "POSTBASELINE_FAUCET_ACTIVITY"
        )

    elif eligible_player_listings:
        faucet_state = (
            "ELIGIBLE_SUPPLY_WAITING_FOR_BUYER"
        )

    elif active_player_listings:
        faucet_state = (
            "PLAYER_SUPPLY_PRESENT_NOT_ELIGIBLE"
        )

    else:
        faucet_state = (
            "NO_POSTBASELINE_FAUCET_ACTIVITY"
        )

    stamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    FAUCET_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    ledger_file = (
        FAUCET_ROOT
        / f"{stamp}-ledger.csv"
    )

    events_file = (
        FAUCET_ROOT
        / f"{stamp}-events.csv"
    )

    active_file = (
        FAUCET_ROOT
        / f"{stamp}-active-supply.csv"
    )

    exposure_file = (
        FAUCET_ROOT
        / f"{stamp}-buyer-exposure.csv"
    )

    summary_file = (
        FAUCET_ROOT
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
        exposure_file,
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
            "3E.2",

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

        "faucet_state":
            faucet_state,

        "buyer_runtime_rows":
            len(buyer),

        "enabled_buyer_forms":
            len(
                enabled_policy
            ),

        "tick_seconds":
            tick_seconds,

        "cycles_per_day":
            cycles_per_day,

        "one_roll_per_item_form_per_cycle":
            rate_hardening_present,

        "baseline_historical_faucet_gil":
            as_int(
                baseline.get(
                    "gross_faucet_gil"
                )
            ),

        "postbaseline_purchases":
            total_purchases,

        "postbaseline_player_ask_gil":
            total_player_asks,

        "postbaseline_faucet_gil":
            total_actual_faucet,

        "postbaseline_premium_over_ask":
            total_premium,

        "active_player_listings":
            active_player_listings,

        "eligible_player_listings":
            eligible_player_listings,

        "expected_daily_gil_continuous_supply":
            round(
                expected_daily_exposure,
                6,
            ),

        "absolute_rng_success_ceiling_gil_per_day":
            round(
                absolute_daily_ceiling,
                6,
            ),

        "top_actual_faucet_itemid":
            top_item_id,

        "top_actual_faucet_gil":
            top_item_gil,

        "top_actual_faucet_share":
            round(
                top_item_share,
                6,
            ),

        "highest_modeled_exposure_itemid":
            highest_exposure_item_id,

        "highest_modeled_exposure_gil_per_day":
            round(
                highest_exposure_gil,
                6,
            ),

        "highest_modeled_exposure_share":
            round(
                highest_exposure_share,
                6,
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

            "active_supply":
                str(
                    active_file
                ),

            "buyer_exposure":
                str(
                    exposure_file
                ),
        },

        "integrity_details":
            integrity_failures,
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
        " Phase 3E.2 AHBot Faucet Ledger"
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
        "Buyer runtime rows:                      ",
        len(buyer),
    )

    print(
        "Enabled buyer forms:                    ",
        len(
            enabled_policy
        ),
    )

    print(
        "Tick seconds:                            ",
        tick_seconds,
    )

    print(
        "Cycles per day:                          ",
        cycles_per_day,
    )

    print(
        "One roll per item/form/cycle:            ",
        rate_hardening_present,
    )

    print()
    print(
        "Historical faucet at T0:                 ",
        as_int(
            baseline.get(
                "gross_faucet_gil"
            )
        ),
    )

    print(
        "Post-baseline purchases:                 ",
        total_purchases,
    )

    print(
        "Post-baseline player asks:               ",
        total_player_asks,
    )

    print(
        "Post-baseline AHBot faucet:              ",
        total_actual_faucet,
    )

    print(
        "Post-baseline premium over asks:         ",
        total_premium,
    )

    print()
    print(
        "Active player listings in buyer catalog: ",
        active_player_listings,
    )

    print(
        "Currently eligible player listings:      ",
        eligible_player_listings,
    )

    print()
    print(
        "Expected daily gil under continuous "
        "eligible supply:                        ",
        round(
            expected_daily_exposure,
            6,
        ),
    )

    print(
        "Absolute RNG-success ceiling gil/day:     ",
        round(
            absolute_daily_ceiling,
            6,
        ),
    )

    print()
    print(
        "Highest modeled exposure item ID:         ",
        highest_exposure_item_id,
    )

    print(
        "Highest modeled exposure gil/day:         ",
        round(
            highest_exposure_gil,
            6,
        ),
    )

    print(
        "Highest modeled exposure share:           ",
        round(
            highest_exposure_share,
            6,
        ),
    )

    print()
    print(
        "Integrity failures:                       ",
        integrity_count,
    )

    print()
    print(
        "Faucet state:",
        faucet_state,
    )

    print()
    print(
        "Runtime mutation performed:               0"
    )

    print(
        "Database mutation performed:              0"
    )

    print(
        "Price change authorized:                  0"
    )

    print(
        "Rate change authorized:                   0"
    )

    print(
        "Stock change authorized:                  0"
    )

    print(
        "Catalog change authorized:                0"
    )

    print(
        "Auto live promotion:                      0"
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
        "Active supply:",
        active_file,
    )

    print(
        "Exposure:",
        exposure_file,
    )

    print(
        "Summary:",
        summary_file,
    )

    print()
    print(summary["status"])

    if integrity_count:
        raise SystemExit(1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
