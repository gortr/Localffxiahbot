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
GENERATED = ROOT / "economy" / "generated"

OBS_ROOT = (
    ROOT
    / "economy"
    / "runtime-observations"
    / "player-market-response"
)

SUPPLY_ROOT = (
    OBS_ROOT
    / "supply"
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

BASELINE_CENSUS = (
    OBS_ROOT
    / "baseline-census.csv"
)

BASELINE_ACTIVE = (
    OBS_ROOT
    / "baseline-active-listings.csv"
)

BASELINE_HISTORY = (
    OBS_ROOT
    / "baseline-completed-history.csv"
)


class SupplyObservationError(RuntimeError):
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

    seller_is_bot = (
        int(row.seller) == 0
        and seller_name == bot_name
    )

    seller_is_player = (
        int(row.seller) > 0
    )

    buyer_is_bot = (
        buyer_name == bot_name
    )

    buyer_is_player = (
        bool(buyer_name)
        and not buyer_is_bot
    )

    if (
        seller_is_bot
        and buyer_is_bot
    ):
        return "SYNTHETIC_HISTORY"

    if (
        seller_is_player
        and buyer_is_bot
    ):
        return "PLAYER_SUPPLY_TO_BOT"

    if (
        seller_is_bot
        and buyer_is_player
    ):
        return "PLAYER_DEMAND_FROM_BOT"

    if (
        seller_is_player
        and buyer_is_player
    ):
        return "ORGANIC_PLAYER_TRADE"

    return "UNCLASSIFIED"


def main() -> int:
    required = [
        CONFIG_FILE,
        SELLER_RUNTIME,
        BUYER_RUNTIME,
        BASELINE_JSON,
        BASELINE_CENSUS,
        BASELINE_ACTIVE,
        BASELINE_HISTORY,
    ]

    for path in required:
        if not path.exists():
            raise SupplyObservationError(
                f"Missing required input: {path}"
            )

    baseline = json.loads(
        BASELINE_JSON.read_text(
            encoding="utf-8",
        )
    )

    if baseline.get("status") != "PASS":
        raise SupplyObservationError(
            "3D.1 baseline is not PASS."
        )

    if not baseline.get(
        "baseline_locked",
        False,
    ):
        raise SupplyObservationError(
            "3D.1 baseline is not locked."
        )

    baseline_epoch = as_int(
        baseline.get(
            "baseline_epoch"
        )
    )

    if baseline_epoch <= 0:
        raise SupplyObservationError(
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
        raise SupplyObservationError(
            "Seller runtime changed since "
            "the 3D baseline."
        )

    if (
        buyer_sha
        != baseline.get(
            "buyer_runtime_sha256"
        )
    ):
        raise SupplyObservationError(
            "Buyer runtime changed since "
            "the 3D baseline."
        )

    census0 = pd.read_csv(
        BASELINE_CENSUS,
        low_memory=False,
    )

    active0 = pd.read_csv(
        BASELINE_ACTIVE,
        low_memory=False,
    )

    history0 = pd.read_csv(
        BASELINE_HISTORY,
        low_memory=False,
    )

    seller = pd.read_csv(
        SELLER_RUNTIME,
        low_memory=False,
    )

    buyer = pd.read_csv(
        BUYER_RUNTIME,
        low_memory=False,
    )

    pilot_ids = set(
        census0["itemid"]
        .astype(int)
    )

    if len(pilot_ids) != 14:
        raise SupplyObservationError(
            "Expected 14 pilot items from "
            "the baseline."
        )

    lane_by_id = (
        census0[
            [
                "itemid",
                "pilot_lane",
                "name",
            ]
        ]
        .drop_duplicates(
            subset=["itemid"]
        )
        .set_index("itemid")
    )

    baseline_active_ids = set()

    if not active0.empty:
        baseline_active_ids = set(
            active0["id"]
            .astype(int)
        )

    baseline_history_ids = set()

    if not history0.empty:
        baseline_history_ids = set(
            history0["id"]
            .astype(int)
        )

    buyer_by_id = (
        buyer
        .set_index("itemid")
    )

    config = Config.from_yaml(
        CONFIG_FILE
    )

    bot_name = str(
        config.name
    ).strip()

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
        raise SupplyObservationError(
            "Cannot connect to auction database."
        )

    observed_epoch = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    observed_utc = utc_iso(
        observed_epoch
    )

    active_records = []
    event_records = []
    integrity_failures = []

    with manager.scoped_session() as session:
        rows = (
            session.query(AuctionHouse)
            .filter(
                AuctionHouse.itemid.in_(
                    sorted(pilot_ids)
                )
            )
            .all()
        )

        for row in rows:
            iid = int(
                row.itemid
            )

            stack = int(
                row.stack
            )

            sale = int(
                row.sale or 0
            )

            sell_date = int(
                row.sell_date or 0
            )

            buyer_name = str(
                row.buyer_name or ""
            ).strip()

            seller_name = str(
                row.seller_name or ""
            ).strip()

            is_active = (
                sale == 0
                and sell_date == 0
                and not buyer_name
            )

            is_completed = (
                sell_date != 0
            )

            if (
                sale != 0
                and sell_date == 0
            ):
                integrity_failures.append(
                    {
                        "id":
                            int(row.id),
                        "reason":
                            "SALE_WITHOUT_SELL_DATE",
                    }
                )

            if (
                sell_date != 0
                and sale == 0
            ):
                integrity_failures.append(
                    {
                        "id":
                            int(row.id),
                        "reason":
                            "SELL_DATE_WITH_ZERO_SALE",
                    }
                )

            if (
                not is_active
                and not is_completed
            ):
                integrity_failures.append(
                    {
                        "id":
                            int(row.id),
                        "reason":
                            "NEITHER_ACTIVE_NOR_COMPLETED",
                    }
                )

            lane = str(
                lane_by_id.loc[
                    iid,
                    "pilot_lane",
                ]
            )

            name = str(
                lane_by_id.loc[
                    iid,
                    "name",
                ]
            )

            buyer_enabled = 0
            configured_bid = None
            buy_rate = None

            if iid in buyer_by_id.index:
                br = buyer_by_id.loc[
                    iid
                ]

                flag_col = (
                    "buy_stacks"
                    if stack
                    else "buy_single"
                )

                price_col = (
                    "price_stacks"
                    if stack
                    else "price_single"
                )

                rate_col = (
                    "buy_rate_stacks"
                    if stack
                    else "buy_rate_single"
                )

                buyer_enabled = int(
                    as_int(
                        br[flag_col]
                    ) == 1
                )

                if buyer_enabled:
                    configured_bid = as_int(
                        br[price_col]
                    )

                    buy_rate = as_float(
                        br[rate_col]
                    )

            if is_active:
                role = classify_active(
                    row,
                    bot_name,
                )

                ask = int(
                    row.price or 0
                )

                eligible_for_bot = int(
                    role == "PLAYER"
                    and buyer_enabled
                    and configured_bid is not None
                    and ask <= configured_bid
                )

                listing_date = int(
                    row.date or 0
                )

                age_seconds = (
                    max(
                        0,
                        observed_epoch
                        - listing_date,
                    )
                    if listing_date
                    else None
                )

                active_records.append({
                    "id":
                        int(row.id),

                    "itemid":
                        iid,

                    "name":
                        name,

                    "pilot_lane":
                        lane,

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
                        seller_name,

                    "seller_role":
                        role,

                    "ask_price":
                        ask,

                    "listing_date":
                        listing_date,

                    "listing_date_utc":
                        utc_iso(
                            listing_date
                        ),

                    "listing_age_seconds":
                        age_seconds,

                    "listing_age_hours":
                        (
                            round(
                                age_seconds
                                / 3600,
                                4,
                            )
                            if age_seconds
                            is not None
                            else None
                        ),

                    "present_at_baseline":
                        int(
                            int(row.id)
                            in baseline_active_ids
                        ),

                    "new_since_baseline":
                        int(
                            int(row.id)
                            not in baseline_active_ids
                        ),

                    "buyer_enabled":
                        buyer_enabled,

                    "configured_bid":
                        configured_bid,

                    "buy_rate":
                        buy_rate,

                    "eligible_for_ahbot_buy":
                        eligible_for_bot,
                })

            if is_completed:
                tx_class = (
                    classify_completed(
                        row,
                        bot_name,
                    )
                )

                is_new_event = (
                    sell_date
                    >= baseline_epoch
                    and int(row.id)
                    not in baseline_history_ids
                )

                if is_new_event:
                    ask = int(
                        row.price or 0
                    )

                    paid = sale

                    event_records.append({
                        "id":
                            int(row.id),

                        "itemid":
                            iid,

                        "name":
                            name,

                        "pilot_lane":
                            lane,

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
                            seller_name,

                        "buyer_name":
                            buyer_name,

                        "ask_price":
                            ask,

                        "paid_price":
                            paid,

                        "sell_date":
                            sell_date,

                        "sell_date_utc":
                            utc_iso(
                                sell_date
                            ),

                        "classification":
                            tx_class,

                        "buyer_enabled":
                            buyer_enabled,

                        "configured_bid":
                            configured_bid,

                        "buy_rate":
                            buy_rate,

                        "paid_minus_ask":
                            (
                                paid - ask
                            ),

                        "paid_matches_configured_bid":
                            int(
                                buyer_enabled
                                and configured_bid
                                is not None
                                and paid
                                == configured_bid
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
            raise SupplyObservationError(
                "Read-only observation created "
                "unexpected DB session mutations."
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
                "pilot_lane",
                "stack",
                "form",
                "seller",
                "seller_name",
                "seller_role",
                "ask_price",
                "listing_date",
                "listing_date_utc",
                "listing_age_seconds",
                "listing_age_hours",
                "present_at_baseline",
                "new_since_baseline",
                "buyer_enabled",
                "configured_bid",
                "buy_rate",
                "eligible_for_ahbot_buy",
            ]
        )

    if events.empty:
        events = pd.DataFrame(
            columns=[
                "id",
                "itemid",
                "name",
                "pilot_lane",
                "stack",
                "form",
                "seller",
                "seller_name",
                "buyer_name",
                "ask_price",
                "paid_price",
                "sell_date",
                "sell_date_utc",
                "classification",
                "buyer_enabled",
                "configured_bid",
                "buy_rate",
                "paid_minus_ask",
                "paid_matches_configured_bid",
                "seconds_after_baseline",
            ]
        )

    census_records = []

    for iid in sorted(
        pilot_ids
    ):
        name = str(
            lane_by_id.loc[
                iid,
                "name",
            ]
        )

        lane = str(
            lane_by_id.loc[
                iid,
                "pilot_lane",
            ]
        )

        for stack in [0, 1]:
            form_active = active[
                (
                    active["itemid"]
                    .astype(int)
                    == iid
                )
                & (
                    active["stack"]
                    .astype(int)
                    == stack
                )
            ]

            form_events = events[
                (
                    events["itemid"]
                    .astype(int)
                    == iid
                )
                & (
                    events["stack"]
                    .astype(int)
                    == stack
                )
            ]

            players = form_active[
                form_active[
                    "seller_role"
                ]
                == "PLAYER"
            ]

            eligible = players[
                players[
                    "eligible_for_ahbot_buy"
                ]
                .astype(int)
                == 1
            ]

            supply_to_bot = form_events[
                form_events[
                    "classification"
                ]
                == "PLAYER_SUPPLY_TO_BOT"
            ]

            organic = form_events[
                form_events[
                    "classification"
                ]
                == "ORGANIC_PLAYER_TRADE"
            ]

            demand_from_bot = form_events[
                form_events[
                    "classification"
                ]
                == "PLAYER_DEMAND_FROM_BOT"
            ]

            unknown_completed = form_events[
                form_events[
                    "classification"
                ]
                == "UNCLASSIFIED"
            ]

            buyer_enabled = 0
            configured_bid = None
            buy_rate = None

            if iid in buyer_by_id.index:
                br = buyer_by_id.loc[
                    iid
                ]

                flag_col = (
                    "buy_stacks"
                    if stack
                    else "buy_single"
                )

                price_col = (
                    "price_stacks"
                    if stack
                    else "price_single"
                )

                rate_col = (
                    "buy_rate_stacks"
                    if stack
                    else "buy_rate_single"
                )

                buyer_enabled = int(
                    as_int(
                        br[flag_col]
                    ) == 1
                )

                if buyer_enabled:
                    configured_bid = as_int(
                        br[price_col]
                    )

                    buy_rate = as_float(
                        br[rate_col]
                    )

            min_player_ask = (
                int(
                    players[
                        "ask_price"
                    ].min()
                )
                if len(players)
                else None
            )

            max_player_ask = (
                int(
                    players[
                        "ask_price"
                    ].max()
                )
                if len(players)
                else None
            )

            min_eligible_ask = (
                int(
                    eligible[
                        "ask_price"
                    ].min()
                )
                if len(eligible)
                else None
            )

            supply_paid = int(
                supply_to_bot[
                    "paid_price"
                ].sum()
            ) if len(
                supply_to_bot
            ) else 0

            supply_ask = int(
                supply_to_bot[
                    "ask_price"
                ].sum()
            ) if len(
                supply_to_bot
            ) else 0

            census_records.append({
                "itemid":
                    iid,

                "name":
                    name,

                "pilot_lane":
                    lane,

                "stack":
                    stack,

                "form":
                    (
                        "STACK"
                        if stack
                        else "SINGLE"
                    ),

                "buyer_enabled":
                    buyer_enabled,

                "configured_bid":
                    configured_bid,

                "buy_rate":
                    buy_rate,

                "active_total":
                    len(
                        form_active
                    ),

                "active_ahbot":
                    int(
                        (
                            form_active[
                                "seller_role"
                            ]
                            == "AHBOT"
                        ).sum()
                    ),

                "active_player":
                    len(
                        players
                    ),

                "active_unknown":
                    int(
                        (
                            form_active[
                                "seller_role"
                            ]
                            == "UNKNOWN"
                        ).sum()
                    ),

                "new_active_player_since_baseline":
                    int(
                        players[
                            "new_since_baseline"
                        ].sum()
                    )
                    if len(players)
                    else 0,

                "player_min_ask":
                    min_player_ask,

                "player_max_ask":
                    max_player_ask,

                "eligible_player_listings":
                    len(
                        eligible
                    ),

                "minimum_eligible_ask":
                    min_eligible_ask,

                "new_player_supply_to_bot":
                    len(
                        supply_to_bot
                    ),

                "player_supply_ask_total":
                    supply_ask,

                "player_supply_gil_paid":
                    supply_paid,

                "player_supply_premium_paid":
                    supply_paid
                    - supply_ask,

                "new_organic_player_trades":
                    len(
                        organic
                    ),

                "new_player_demand_from_bot":
                    len(
                        demand_from_bot
                    ),

                "new_unclassified_completed":
                    len(
                        unknown_completed
                    ),
            })

    census = pd.DataFrame(
        census_records
    ).sort_values(
        [
            "pilot_lane",
            "itemid",
            "stack",
        ]
    )

    unknown_active = int(
        census[
            "active_unknown"
        ].sum()
    )

    unknown_completed = int(
        census[
            "new_unclassified_completed"
        ].sum()
    )

    active_player_total = int(
        census[
            "active_player"
        ].sum()
    )

    new_active_player_total = int(
        census[
            "new_active_player_since_baseline"
        ].sum()
    )

    eligible_total = int(
        census[
            "eligible_player_listings"
        ].sum()
    )

    player_supply_events = int(
        census[
            "new_player_supply_to_bot"
        ].sum()
    )

    player_supply_gil = int(
        census[
            "player_supply_gil_paid"
        ].sum()
    )

    player_supply_ask = int(
        census[
            "player_supply_ask_total"
        ].sum()
    )

    player_supply_premium = int(
        census[
            "player_supply_premium_paid"
        ].sum()
    )

    new_organic = int(
        census[
            "new_organic_player_trades"
        ].sum()
    )

    integrity_count = (
        len(integrity_failures)
        + unknown_active
        + unknown_completed
    )

    if player_supply_events:
        supply_state = (
            "PLAYER_SUPPLY_PURCHASED_BY_AHBOT"
        )

    elif eligible_total:
        supply_state = (
            "ELIGIBLE_PLAYER_SUPPLY_ACTIVE"
        )

    elif active_player_total:
        supply_state = (
            "PLAYER_SUPPLY_ACTIVE_NOT_ELIGIBLE"
        )

    else:
        supply_state = (
            "NO_PLAYER_SUPPLY_OBSERVED"
        )

    stamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    SUPPLY_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    active_file = (
        SUPPLY_ROOT
        / f"{stamp}-active.csv"
    )

    events_file = (
        SUPPLY_ROOT
        / f"{stamp}-events.csv"
    )

    census_file = (
        SUPPLY_ROOT
        / f"{stamp}-census.csv"
    )

    summary_file = (
        SUPPLY_ROOT
        / f"{stamp}-summary.json"
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

    events.sort_values(
        [
            "sell_date",
            "id",
        ]
    ).to_csv(
        events_file,
        index=False,
    )

    census.to_csv(
        census_file,
        index=False,
    )

    summary = {
        "status":
            "PASS"
            if integrity_count == 0
            else "FAIL",

        "phase":
            "3D.2",

        "observation_utc":
            observed_utc,

        "observation_epoch":
            observed_epoch,

        "baseline_utc":
            baseline.get(
                "baseline_utc"
            ),

        "baseline_epoch":
            baseline_epoch,

        "transaction_rule":
            (
                "sell_date >= baseline_epoch "
                "AND id not in baseline history"
            ),

        "seller_runtime_sha256":
            seller_sha,

        "buyer_runtime_sha256":
            buyer_sha,

        "pilot_items":
            len(
                pilot_ids
            ),

        "pilot_forms":
            len(
                census
            ),

        "supply_state":
            supply_state,

        "active_player_listings":
            active_player_total,

        "new_active_player_listings_since_baseline":
            new_active_player_total,

        "eligible_player_listings":
            eligible_total,

        "player_supply_to_bot_events":
            player_supply_events,

        "player_supply_ask_total":
            player_supply_ask,

        "player_supply_gil_paid":
            player_supply_gil,

        "player_supply_premium_paid":
            player_supply_premium,

        "organic_player_trades":
            new_organic,

        "active_unknown_rows":
            unknown_active,

        "new_unclassified_completed":
            unknown_completed,

        "integrity_failures":
            integrity_count,

        "database_mutation_performed":
            0,

        "runtime_mutation_performed":
            0,

        "price_influence_authorized":
            0,

        "stock_influence_authorized":
            0,

        "catalog_mutation_authorized":
            0,

        "auto_live_promotion":
            0,

        "files": {
            "active":
                str(
                    active_file
                ),

            "events":
                str(
                    events_file
                ),

            "census":
                str(
                    census_file
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

    print("=" * 108)
    print(
        " Phase 3D.2 Player Supply Response"
    )
    print("=" * 108)
    print()

    print(
        census.to_string(
            index=False
        )
    )

    print()
    print(
        "Observation UTC:                     ",
        observed_utc,
    )

    print(
        "Baseline UTC:                        ",
        baseline.get(
            "baseline_utc"
        ),
    )

    print()
    print(
        "Active player listings:              ",
        active_player_total,
    )

    print(
        "New player listings since baseline:  ",
        new_active_player_total,
    )

    print(
        "Eligible player listings:            ",
        eligible_total,
    )

    print()
    print(
        "Player -> AHBot transactions:         ",
        player_supply_events,
    )

    print(
        "Player asks represented:             ",
        player_supply_ask,
    )

    print(
        "AHBot gil paid:                      ",
        player_supply_gil,
    )

    print(
        "AHBot premium over player asks:      ",
        player_supply_premium,
    )

    print(
        "Organic player trades:               ",
        new_organic,
    )

    print()
    print(
        "Unknown active rows:                 ",
        unknown_active,
    )

    print(
        "Unclassified new completions:        ",
        unknown_completed,
    )

    print(
        "Integrity failures:                  ",
        integrity_count,
    )

    print()
    print(
        "Supply state:",
        supply_state,
    )

    print()
    print(
        "Database mutation performed:         0"
    )

    print(
        "Runtime mutation performed:          0"
    )

    print(
        "Price influence authorized:          0"
    )

    print(
        "Stock influence authorized:          0"
    )

    print(
        "Catalog mutation authorized:         0"
    )

    print(
        "Auto live promotion:                 0"
    )

    print()
    print(
        "Active snapshot:",
        active_file,
    )

    print(
        "Event snapshot: ",
        events_file,
    )

    print(
        "Census:         ",
        census_file,
    )

    print(
        "Summary:        ",
        summary_file,
    )

    print()
    print(summary["status"])

    if integrity_count:
        raise SystemExit(1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
