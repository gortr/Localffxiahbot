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

ANALYSIS_ROOT = (
    OBS_ROOT
    / "analysis"
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


class AnalysisError(RuntimeError):
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


def as_float(value: Any):
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
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


def safe_ratio(
    numerator: int,
    denominator: int,
):
    if denominator <= 0:
        return None

    return round(
        numerator / denominator,
        6,
    )


def mean_or_none(
    series: pd.Series,
):
    values = (
        series
        .dropna()
        .astype(float)
    )

    if not len(values):
        return None

    return round(
        float(values.mean()),
        6,
    )


def median_or_none(
    series: pd.Series,
):
    values = (
        series
        .dropna()
        .astype(float)
    )

    if not len(values):
        return None

    return round(
        float(values.median()),
        6,
    )


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
            raise AnalysisError(
                f"Missing required input: {path}"
            )

    baseline = json.loads(
        BASELINE_JSON.read_text(
            encoding="utf-8",
        )
    )

    if baseline.get("status") != "PASS":
        raise AnalysisError(
            "3D.1 baseline is not PASS."
        )

    if not baseline.get(
        "baseline_locked",
        False,
    ):
        raise AnalysisError(
            "3D.1 baseline is not locked."
        )

    baseline_epoch = as_int(
        baseline.get(
            "baseline_epoch"
        )
    )

    if baseline_epoch <= 0:
        raise AnalysisError(
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
        raise AnalysisError(
            "Seller runtime changed since T0."
        )

    if (
        buyer_sha
        != baseline.get(
            "buyer_runtime_sha256"
        )
    ):
        raise AnalysisError(
            "Buyer runtime changed since T0."
        )

    baseline_census = pd.read_csv(
        BASELINE_CENSUS,
        low_memory=False,
    )

    baseline_active = pd.read_csv(
        BASELINE_ACTIVE,
        low_memory=False,
    )

    baseline_history = pd.read_csv(
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

    pilot_meta = (
        baseline_census[
            [
                "itemid",
                "name",
                "pilot_lane",
            ]
        ]
        .drop_duplicates(
            subset=["itemid"]
        )
        .copy()
    )

    pilot_ids = set(
        pilot_meta[
            "itemid"
        ].astype(int)
    )

    if len(pilot_ids) != 14:
        raise AnalysisError(
            "Expected 14 pilot items."
        )

    meta_by_id = (
        pilot_meta
        .set_index("itemid")
    )

    baseline_active_ids = set()

    if not baseline_active.empty:
        baseline_active_ids = set(
            baseline_active[
                "id"
            ].astype(int)
        )

    baseline_history_ids = set()

    if not baseline_history.empty:
        baseline_history_ids = set(
            baseline_history[
                "id"
            ].astype(int)
        )

    seller_by_id = (
        seller.set_index(
            "itemid"
        )
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
        raise AnalysisError(
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

    listing_records = []
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

            row_id = int(
                row.id
            )

            sale = int(
                row.sale or 0
            )

            sell_date = int(
                row.sell_date or 0
            )

            listing_date = int(
                row.date or 0
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
                integrity_failures.append({
                    "id":
                        row_id,

                    "reason":
                        "SALE_WITHOUT_SELL_DATE",
                })

            if (
                sell_date != 0
                and sale == 0
            ):
                integrity_failures.append({
                    "id":
                        row_id,

                    "reason":
                        "SELL_DATE_WITH_ZERO_SALE",
                })

            if (
                not is_active
                and not is_completed
            ):
                integrity_failures.append({
                    "id":
                        row_id,

                    "reason":
                        "NEITHER_ACTIVE_NOR_COMPLETED",
                })

            lane = str(
                meta_by_id.loc[
                    iid,
                    "pilot_lane",
                ]
            )

            name = str(
                meta_by_id.loc[
                    iid,
                    "name",
                ]
            )

            if is_active:
                seller_role = (
                    classify_active(
                        row,
                        bot_name,
                    )
                )

                status = "ACTIVE"
                classification = ""

            else:
                seller_role = (
                    "AHBOT"
                    if (
                        int(row.seller) == 0
                        and seller_name
                        == bot_name
                    )
                    else (
                        "PLAYER"
                        if int(row.seller) > 0
                        else "UNKNOWN"
                    )
                )

                status = "COMPLETED"

                classification = (
                    classify_completed(
                        row,
                        bot_name,
                    )
                )

            present_at_t0 = int(
                row_id
                in baseline_active_ids
            )

            post_t0_listing = int(
                (
                    listing_date
                    >= baseline_epoch
                )
                and not present_at_t0
            )

            in_observation_window = int(
                present_at_t0
                or post_t0_listing
            )

            if in_observation_window:
                listing_records.append({
                    "id":
                        row_id,

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
                        seller_role,

                    "ask_price":
                        int(
                            row.price or 0
                        ),

                    "listing_date":
                        listing_date,

                    "listing_date_utc":
                        utc_iso(
                            listing_date
                        ),

                    "present_at_t0":
                        present_at_t0,

                    "post_t0_listing":
                        post_t0_listing,

                    "status":
                        status,

                    "buyer_name":
                        buyer_name,

                    "paid_price":
                        sale,

                    "sell_date":
                        sell_date,

                    "sell_date_utc":
                        utc_iso(
                            sell_date
                        ),

                    "classification":
                        classification,
                })

            is_new_event = (
                is_completed
                and sell_date
                >= baseline_epoch
                and row_id
                not in baseline_history_ids
            )

            if is_new_event:
                duration_seconds = None

                if (
                    listing_date
                    and sell_date
                    >= listing_date
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
                        int(
                            row.price or 0
                        ),

                    "paid_price":
                        sale,

                    "classification":
                        classify_completed(
                            row,
                            bot_name,
                        ),

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

                    "present_at_t0":
                        int(
                            row_id
                            in baseline_active_ids
                        ),

                    "seconds_after_t0":
                        sell_date
                        - baseline_epoch,
                })

        if (
            session.new
            or session.dirty
            or session.deleted
        ):
            raise AnalysisError(
                "Read-only analysis created "
                "unexpected DB mutations."
            )

    listings = pd.DataFrame(
        listing_records
    )

    events = pd.DataFrame(
        event_records
    )

    if listings.empty:
        listings = pd.DataFrame(
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
                "present_at_t0",
                "post_t0_listing",
                "status",
                "buyer_name",
                "paid_price",
                "sell_date",
                "sell_date_utc",
                "classification",
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
                "classification",
                "listing_date",
                "listing_date_utc",
                "sell_date",
                "sell_date_utc",
                "sale_duration_seconds",
                "sale_duration_hours",
                "present_at_t0",
                "seconds_after_t0",
            ]
        )

    metrics = []

    for iid in sorted(
        pilot_ids
    ):
        lane = str(
            meta_by_id.loc[
                iid,
                "pilot_lane",
            ]
        )

        name = str(
            meta_by_id.loc[
                iid,
                "name",
            ]
        )

        for stack in [0, 1]:
            base_row = baseline_census[
                (
                    baseline_census[
                        "itemid"
                    ].astype(int)
                    == iid
                )
                & (
                    baseline_census[
                        "stack"
                    ].astype(int)
                    == stack
                )
            ]

            if len(base_row) != 1:
                raise AnalysisError(
                    f"{iid}/{stack}: invalid "
                    "baseline census cardinality."
                )

            base = base_row.iloc[0]

            form_listings = listings[
                (
                    listings["itemid"]
                    .astype(int)
                    == iid
                )
                & (
                    listings["stack"]
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

            active = form_listings[
                form_listings[
                    "status"
                ]
                == "ACTIVE"
            ]

            active_bot = active[
                active[
                    "seller_role"
                ]
                == "AHBOT"
            ]

            active_player = active[
                active[
                    "seller_role"
                ]
                == "PLAYER"
            ]

            active_unknown = active[
                active[
                    "seller_role"
                ]
                == "UNKNOWN"
            ]

            bot_opportunities = (
                form_listings[
                    form_listings[
                        "seller_role"
                    ]
                    == "AHBOT"
                ]
            )

            player_opportunities = (
                form_listings[
                    form_listings[
                        "seller_role"
                    ]
                    == "PLAYER"
                ]
            )

            demand = form_events[
                form_events[
                    "classification"
                ]
                == "PLAYER_DEMAND_FROM_BOT"
            ]

            supply = form_events[
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

            unknown_events = form_events[
                form_events[
                    "classification"
                ]
                == "UNCLASSIFIED"
            ]

            seller_enabled = 0
            seller_price = None
            stock_target = None
            sell_rate = None

            if iid in seller_by_id.index:
                sr = seller_by_id.loc[
                    iid
                ]

                seller_enabled = as_int(
                    sr[
                        "sell_stacks"
                        if stack
                        else "sell_single"
                    ]
                )

                if seller_enabled:
                    seller_price = as_int(
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

            buyer_enabled = 0
            buyer_bid = None
            buy_rate = None

            if iid in buyer_by_id.index:
                br = buyer_by_id.loc[
                    iid
                ]

                buyer_enabled = as_int(
                    br[
                        "buy_stacks"
                        if stack
                        else "buy_single"
                    ]
                )

                if buyer_enabled:
                    buyer_bid = as_int(
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

            eligible_player_opportunities = (
                player_opportunities.iloc[
                    0:0
                ]
            )

            if (
                buyer_enabled
                and buyer_bid is not None
                and len(
                    player_opportunities
                )
            ):
                eligible_player_opportunities = (
                    player_opportunities[
                        player_opportunities[
                            "ask_price"
                        ].astype(int)
                        <= buyer_bid
                    ]
                )

            current_eligible = (
                active_player.iloc[
                    0:0
                ]
            )

            if (
                buyer_enabled
                and buyer_bid is not None
                and len(active_player)
            ):
                current_eligible = (
                    active_player[
                        active_player[
                            "ask_price"
                        ].astype(int)
                        <= buyer_bid
                    ]
                )

            sink_gil = (
                int(
                    demand[
                        "paid_price"
                    ].sum()
                )
                if len(demand)
                else 0
            )

            faucet_gil = (
                int(
                    supply[
                        "paid_price"
                    ].sum()
                )
                if len(supply)
                else 0
            )

            player_ask_total = (
                int(
                    supply[
                        "ask_price"
                    ].sum()
                )
                if len(supply)
                else 0
            )

            premium_paid = (
                faucet_gil
                - player_ask_total
            )

            sell_through = safe_ratio(
                len(demand),
                len(bot_opportunities),
            )

            capture_rate = safe_ratio(
                len(supply),
                len(
                    eligible_player_opportunities
                ),
            )

            demand_duration_mean = (
                mean_or_none(
                    demand[
                        "sale_duration_hours"
                    ]
                )
                if len(demand)
                else None
            )

            organic_price_median = (
                median_or_none(
                    organic[
                        "paid_price"
                    ]
                )
                if len(organic)
                else None
            )

            player_ask_median = (
                median_or_none(
                    player_opportunities[
                        "ask_price"
                    ]
                )
                if len(
                    player_opportunities
                )
                else None
            )

            if len(demand) and len(supply):
                signal_state = (
                    "TWO_SIDED_PLAYER_SIGNAL"
                )

            elif len(demand):
                signal_state = (
                    "PLAYER_DEMAND_SIGNAL"
                )

            elif len(supply):
                signal_state = (
                    "PLAYER_SUPPLY_SIGNAL"
                )

            elif len(organic):
                signal_state = (
                    "ORGANIC_PLAYER_SIGNAL"
                )

            elif len(active_player):
                signal_state = (
                    "ACTIVE_PLAYER_SUPPLY_ONLY"
                )

            else:
                signal_state = (
                    "NO_PLAYER_RESPONSE"
                )

            metrics.append({
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

                "seller_enabled":
                    seller_enabled,

                "seller_price":
                    seller_price,

                "stock_target":
                    stock_target,

                "sell_rate":
                    sell_rate,

                "buyer_enabled":
                    buyer_enabled,

                "buyer_bid":
                    buyer_bid,

                "buy_rate":
                    buy_rate,

                "baseline_active_ahbot":
                    as_int(
                        base[
                            "active_ahbot"
                        ]
                    ),

                "baseline_active_player":
                    as_int(
                        base[
                            "active_player"
                        ]
                    ),

                "current_active_ahbot":
                    len(active_bot),

                "current_active_player":
                    len(active_player),

                "current_active_unknown":
                    len(active_unknown),

                "ahbot_depth_delta":
                    len(active_bot)
                    - as_int(
                        base[
                            "active_ahbot"
                        ]
                    ),

                "player_depth_delta":
                    len(active_player)
                    - as_int(
                        base[
                            "active_player"
                        ]
                    ),

                "current_player_min_ask":
                    (
                        int(
                            active_player[
                                "ask_price"
                            ].min()
                        )
                        if len(
                            active_player
                        )
                        else None
                    ),

                "current_player_median_ask":
                    (
                        median_or_none(
                            active_player[
                                "ask_price"
                            ]
                        )
                        if len(
                            active_player
                        )
                        else None
                    ),

                "current_eligible_player_depth":
                    len(
                        current_eligible
                    ),

                "ahbot_listing_opportunities":
                    len(
                        bot_opportunities
                    ),

                "player_listing_opportunities":
                    len(
                        player_opportunities
                    ),

                "eligible_player_opportunities":
                    len(
                        eligible_player_opportunities
                    ),

                "ahbot_to_player_sales":
                    len(demand),

                "seller_sell_through_rate":
                    sell_through,

                "player_to_ahbot_sales":
                    len(supply),

                "buyer_capture_rate":
                    capture_rate,

                "organic_player_trades":
                    len(organic),

                "player_gil_sink":
                    sink_gil,

                "ahbot_gil_faucet":
                    faucet_gil,

                "net_player_gil_sink":
                    sink_gil
                    - faucet_gil,

                "player_supply_ask_total":
                    player_ask_total,

                "ahbot_premium_over_ask":
                    premium_paid,

                "player_listing_median_ask":
                    player_ask_median,

                "organic_median_paid_price":
                    organic_price_median,

                "average_demand_sale_duration_hours":
                    demand_duration_mean,

                "unclassified_events":
                    len(
                        unknown_events
                    ),

                "signal_state":
                    signal_state,
            })

    metrics_df = pd.DataFrame(
        metrics
    ).sort_values(
        [
            "pilot_lane",
            "itemid",
            "stack",
        ]
    )

    unexpected_active = int(
        metrics_df[
            "current_active_unknown"
        ].sum()
    )

    unclassified = int(
        metrics_df[
            "unclassified_events"
        ].sum()
    )

    # Pilot B must never become an AHBot seller lane.
    pilot_b_bot_depth = int(
        metrics_df[
            metrics_df[
                "pilot_lane"
            ]
            == "PILOT_B_BUYER"
        ][
            "current_active_ahbot"
        ].sum()
    )

    if pilot_b_bot_depth:
        integrity_failures.append({
            "reason":
                "PILOT_B_ACTIVE_AHBOT_SELLER_ROWS",

            "count":
                pilot_b_bot_depth,
        })

    integrity_count = (
        len(integrity_failures)
        + unexpected_active
        + unclassified
    )

    total_demand_sales = int(
        metrics_df[
            "ahbot_to_player_sales"
        ].sum()
    )

    total_supply_sales = int(
        metrics_df[
            "player_to_ahbot_sales"
        ].sum()
    )

    total_organic = int(
        metrics_df[
            "organic_player_trades"
        ].sum()
    )

    total_sink = int(
        metrics_df[
            "player_gil_sink"
        ].sum()
    )

    total_faucet = int(
        metrics_df[
            "ahbot_gil_faucet"
        ].sum()
    )

    active_player_depth = int(
        metrics_df[
            "current_active_player"
        ].sum()
    )

    active_eligible_depth = int(
        metrics_df[
            "current_eligible_player_depth"
        ].sum()
    )

    forms_with_signal = int(
        (
            metrics_df[
                "signal_state"
            ]
            != "NO_PLAYER_RESPONSE"
        ).sum()
    )

    if (
        total_demand_sales
        or total_supply_sales
        or total_organic
        or active_player_depth
    ):
        market_state = (
            "PLAYER_RESPONSE_OBSERVED"
        )

    else:
        market_state = (
            "NO_PLAYER_RESPONSE_YET"
        )

    stamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    ANALYSIS_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    metrics_file = (
        ANALYSIS_ROOT
        / f"{stamp}-price-depth-sellthrough.csv"
    )

    listings_file = (
        ANALYSIS_ROOT
        / f"{stamp}-listing-opportunities.csv"
    )

    events_file = (
        ANALYSIS_ROOT
        / f"{stamp}-events.csv"
    )

    summary_file = (
        ANALYSIS_ROOT
        / f"{stamp}-summary.json"
    )

    metrics_df.to_csv(
        metrics_file,
        index=False,
    )

    listings.sort_values(
        [
            "itemid",
            "stack",
            "id",
        ]
    ).to_csv(
        listings_file,
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

    summary = {
        "status":
            "PASS"
            if integrity_count == 0
            else "FAIL",

        "phase":
            "3D.4",

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

        "seller_runtime_sha256":
            seller_sha,

        "buyer_runtime_sha256":
            buyer_sha,

        "pilot_items":
            len(pilot_ids),

        "pilot_forms":
            len(metrics_df),

        "market_state":
            market_state,

        "forms_with_player_signal":
            forms_with_signal,

        "active_player_depth":
            active_player_depth,

        "active_eligible_player_depth":
            active_eligible_depth,

        "ahbot_to_player_sales":
            total_demand_sales,

        "player_to_ahbot_sales":
            total_supply_sales,

        "organic_player_trades":
            total_organic,

        "player_gil_sink":
            total_sink,

        "ahbot_gil_faucet":
            total_faucet,

        "net_player_gil_sink":
            total_sink
            - total_faucet,

        "unexpected_active_unknown_rows":
            unexpected_active,

        "unclassified_events":
            unclassified,

        "pilot_b_active_ahbot_seller_rows":
            pilot_b_bot_depth,

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
            "metrics":
                str(metrics_file),

            "listing_opportunities":
                str(listings_file),

            "events":
                str(events_file),
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

    print("=" * 112)
    print(
        " Phase 3D.4 Price / Depth / "
        "Sell-Through Analysis"
    )
    print("=" * 112)
    print()

    display_columns = [
        "itemid",
        "name",
        "pilot_lane",
        "form",
        "seller_price",
        "buyer_bid",
        "baseline_active_ahbot",
        "current_active_ahbot",
        "current_active_player",
        "current_eligible_player_depth",
        "ahbot_listing_opportunities",
        "ahbot_to_player_sales",
        "seller_sell_through_rate",
        "player_listing_opportunities",
        "eligible_player_opportunities",
        "player_to_ahbot_sales",
        "buyer_capture_rate",
        "organic_player_trades",
        "player_gil_sink",
        "ahbot_gil_faucet",
        "signal_state",
    ]

    print(
        metrics_df[
            display_columns
        ].to_string(
            index=False
        )
    )

    print()
    print(
        "Observation UTC:                  ",
        observed_utc,
    )

    print(
        "Baseline UTC:                     ",
        baseline.get(
            "baseline_utc"
        ),
    )

    print()
    print(
        "Forms with player signal:         ",
        forms_with_signal,
    )

    print(
        "Current player depth:             ",
        active_player_depth,
    )

    print(
        "Current eligible player depth:    ",
        active_eligible_depth,
    )

    print()
    print(
        "AHBot -> Player sales:             ",
        total_demand_sales,
    )

    print(
        "Player -> AHBot sales:             ",
        total_supply_sales,
    )

    print(
        "Organic player trades:            ",
        total_organic,
    )

    print()
    print(
        "Player gil sink:                  ",
        total_sink,
    )

    print(
        "AHBot gil faucet:                 ",
        total_faucet,
    )

    print(
        "Net player gil sink:              ",
        total_sink - total_faucet,
    )

    print()
    print(
        "Unknown active rows:              ",
        unexpected_active,
    )

    print(
        "Unclassified events:              ",
        unclassified,
    )

    print(
        "Pilot B AHBot seller rows:        ",
        pilot_b_bot_depth,
    )

    print(
        "Integrity failures:               ",
        integrity_count,
    )

    print()
    print(
        "Market state:",
        market_state,
    )

    print()
    print(
        "Database mutation performed:       0"
    )

    print(
        "Runtime mutation performed:        0"
    )

    print(
        "Price influence authorized:        0"
    )

    print(
        "Stock influence authorized:        0"
    )

    print(
        "Catalog mutation authorized:       0"
    )

    print(
        "Auto live promotion:               0"
    )

    print()
    print(
        "Metrics:",
        metrics_file,
    )

    print(
        "Listings:",
        listings_file,
    )

    print(
        "Events:",
        events_file,
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
