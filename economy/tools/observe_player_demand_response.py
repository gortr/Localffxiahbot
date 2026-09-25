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

DEMAND_ROOT = (
    OBS_ROOT
    / "demand"
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


class DemandObservationError(RuntimeError):
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
            raise DemandObservationError(
                f"Missing required input: {path}"
            )

    baseline = json.loads(
        BASELINE_JSON.read_text(
            encoding="utf-8",
        )
    )

    if baseline.get("status") != "PASS":
        raise DemandObservationError(
            "3D.1 baseline is not PASS."
        )

    if not baseline.get(
        "baseline_locked",
        False,
    ):
        raise DemandObservationError(
            "3D.1 baseline is not locked."
        )

    baseline_epoch = as_int(
        baseline.get(
            "baseline_epoch"
        )
    )

    if baseline_epoch <= 0:
        raise DemandObservationError(
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
        raise DemandObservationError(
            "Seller runtime changed since "
            "the 3D baseline."
        )

    if (
        buyer_sha
        != baseline.get(
            "buyer_runtime_sha256"
        )
    ):
        raise DemandObservationError(
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

    pilot_a = (
        census0[
            census0["pilot_lane"]
            == "PILOT_A_SELLER"
        ][
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

    pilot_a_ids = set(
        pilot_a["itemid"]
        .astype(int)
    )

    if len(pilot_a_ids) != 11:
        raise DemandObservationError(
            "Expected 11 Pilot A items."
        )

    pilot_a_by_id = (
        pilot_a.set_index(
            "itemid"
        )
    )

    seller_by_id = (
        seller.set_index(
            "itemid"
        )
    )

    missing_seller = (
        pilot_a_ids
        - set(
            seller_by_id.index
            .astype(int)
        )
    )

    if missing_seller:
        raise DemandObservationError(
            "Pilot A items missing from "
            f"seller runtime: {sorted(missing_seller)}"
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
        raise DemandObservationError(
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
                    sorted(
                        pilot_a_ids
                    )
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

            seller_name = str(
                row.seller_name or ""
            ).strip()

            buyer_name = str(
                row.buyer_name or ""
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
                        int(row.id),

                    "reason":
                        "SALE_WITHOUT_SELL_DATE",
                })

            if (
                sell_date != 0
                and sale == 0
            ):
                integrity_failures.append({
                    "id":
                        int(row.id),

                    "reason":
                        "SELL_DATE_WITH_ZERO_SALE",
                })

            if (
                not is_active
                and not is_completed
            ):
                integrity_failures.append({
                    "id":
                        int(row.id),

                    "reason":
                        "NEITHER_ACTIVE_NOR_COMPLETED",
                })

            sr = seller_by_id.loc[
                iid
            ]

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

            if is_active:
                role = classify_active(
                    row,
                    bot_name,
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
                        str(
                            pilot_a_by_id.loc[
                                iid,
                                "name",
                            ]
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
                        seller_name,

                    "seller_role":
                        role,

                    "ask_price":
                        int(
                            row.price or 0
                        ),

                    "configured_price":
                        configured_price,

                    "price_matches_runtime":
                        int(
                            int(
                                row.price or 0
                            )
                            == configured_price
                        ),

                    "stock_target":
                        stock_target,

                    "sell_rate":
                        sell_rate,

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

                    "new_listing_since_baseline":
                        int(
                            int(row.id)
                            not in baseline_active_ids
                        ),
                })

            if is_completed:
                classification = (
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

                if not is_new_event:
                    continue

                listing_date = int(
                    row.date or 0
                )

                duration_seconds = (
                    sell_date
                    - listing_date
                    if (
                        listing_date
                        and sell_date
                        >= listing_date
                    )
                    else None
                )

                event_records.append({
                    "id":
                        int(row.id),

                    "itemid":
                        iid,

                    "name":
                        str(
                            pilot_a_by_id.loc[
                                iid,
                                "name",
                            ]
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
                        seller_name,

                    "buyer_name":
                        buyer_name,

                    "ask_price":
                        int(
                            row.price or 0
                        ),

                    "paid_price":
                        sale,

                    "configured_price":
                        configured_price,

                    "paid_matches_runtime_price":
                        int(
                            sale
                            == configured_price
                        ),

                    "sell_rate":
                        sell_rate,

                    "stock_target":
                        stock_target,

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
                                4,
                            )
                            if duration_seconds
                            is not None
                            else None
                        ),

                    "listing_present_at_baseline":
                        int(
                            int(row.id)
                            in baseline_active_ids
                        ),

                    "classification":
                        classification,

                    "seconds_after_baseline":
                        sell_date
                        - baseline_epoch,
                })

        if (
            session.new
            or session.dirty
            or session.deleted
        ):
            raise DemandObservationError(
                "Read-only observation created "
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
                "configured_price",
                "price_matches_runtime",
                "stock_target",
                "sell_rate",
                "listing_date",
                "listing_date_utc",
                "listing_age_seconds",
                "listing_age_hours",
                "present_at_baseline",
                "new_listing_since_baseline",
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
                "buyer_name",
                "ask_price",
                "paid_price",
                "configured_price",
                "paid_matches_runtime_price",
                "sell_rate",
                "stock_target",
                "listing_date",
                "listing_date_utc",
                "sell_date",
                "sell_date_utc",
                "sale_duration_seconds",
                "sale_duration_hours",
                "listing_present_at_baseline",
                "classification",
                "seconds_after_baseline",
            ]
        )

    census_records = []

    for iid in sorted(
        pilot_a_ids
    ):
        sr = seller_by_id.loc[
            iid
        ]

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

            bot_active = form_active[
                form_active[
                    "seller_role"
                ]
                == "AHBOT"
            ]

            player_active = form_active[
                form_active[
                    "seller_role"
                ]
                == "PLAYER"
            ]

            unknown_active = form_active[
                form_active[
                    "seller_role"
                ]
                == "UNKNOWN"
            ]

            demand = form_events[
                form_events[
                    "classification"
                ]
                == "PLAYER_DEMAND_FROM_BOT"
            ]

            organic = form_events[
                form_events[
                    "classification"
                ]
                == "ORGANIC_PLAYER_TRADE"
            ]

            supply_to_bot = form_events[
                form_events[
                    "classification"
                ]
                == "PLAYER_SUPPLY_TO_BOT"
            ]

            unclassified = form_events[
                form_events[
                    "classification"
                ]
                == "UNCLASSIFIED"
            ]

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

            if len(demand):
                durations = (
                    demand[
                        "sale_duration_hours"
                    ]
                    .dropna()
                    .astype(float)
                )

                avg_duration = (
                    float(
                        durations.mean()
                    )
                    if len(durations)
                    else None
                )

                min_duration = (
                    float(
                        durations.min()
                    )
                    if len(durations)
                    else None
                )

                max_duration = (
                    float(
                        durations.max()
                    )
                    if len(durations)
                    else None
                )
            else:
                avg_duration = None
                min_duration = None
                max_duration = None

            census_records.append({
                "itemid":
                    iid,

                "name":
                    str(
                        pilot_a_by_id.loc[
                            iid,
                            "name",
                        ]
                    ),

                "stack":
                    stack,

                "form":
                    (
                        "STACK"
                        if stack
                        else "SINGLE"
                    ),

                "configured_price":
                    configured_price,

                "stock_target":
                    stock_target,

                "sell_rate":
                    sell_rate,

                "active_ahbot":
                    len(
                        bot_active
                    ),

                "active_player":
                    len(
                        player_active
                    ),

                "active_unknown":
                    len(
                        unknown_active
                    ),

                "baseline_active_ahbot":
                    as_int(
                        census0[
                            (
                                census0[
                                    "itemid"
                                ].astype(int)
                                == iid
                            )
                            & (
                                census0[
                                    "stack"
                                ].astype(int)
                                == stack
                            )
                        ][
                            "active_ahbot"
                        ].iloc[0]
                    ),

                "new_ahbot_listings_since_baseline":
                    int(
                        bot_active[
                            "new_listing_since_baseline"
                        ].sum()
                    )
                    if len(bot_active)
                    else 0,

                "new_player_demand_from_bot":
                    len(
                        demand
                    ),

                "player_gil_spent":
                    int(
                        demand[
                            "paid_price"
                        ].sum()
                    )
                    if len(demand)
                    else 0,

                "demand_from_baseline_listings":
                    int(
                        demand[
                            "listing_present_at_baseline"
                        ].sum()
                    )
                    if len(demand)
                    else 0,

                "demand_from_postbaseline_listings":
                    int(
                        (
                            demand[
                                "listing_present_at_baseline"
                            ]
                            == 0
                        ).sum()
                    )
                    if len(demand)
                    else 0,

                "average_sale_duration_hours":
                    avg_duration,

                "minimum_sale_duration_hours":
                    min_duration,

                "maximum_sale_duration_hours":
                    max_duration,

                "new_organic_player_trades":
                    len(
                        organic
                    ),

                "new_player_supply_to_bot":
                    len(
                        supply_to_bot
                    ),

                "new_unclassified_completed":
                    len(
                        unclassified
                    ),
            })

    census = pd.DataFrame(
        census_records
    ).sort_values(
        [
            "itemid",
            "stack",
        ]
    )

    player_demand_events = int(
        census[
            "new_player_demand_from_bot"
        ].sum()
    )

    player_gil_spent = int(
        census[
            "player_gil_spent"
        ].sum()
    )

    baseline_listing_sales = int(
        census[
            "demand_from_baseline_listings"
        ].sum()
    )

    postbaseline_listing_sales = int(
        census[
            "demand_from_postbaseline_listings"
        ].sum()
    )

    active_ahbot = int(
        census[
            "active_ahbot"
        ].sum()
    )

    active_player = int(
        census[
            "active_player"
        ].sum()
    )

    active_unknown = int(
        census[
            "active_unknown"
        ].sum()
    )

    organic_events = int(
        census[
            "new_organic_player_trades"
        ].sum()
    )

    unclassified = int(
        census[
            "new_unclassified_completed"
        ].sum()
    )

    integrity_count = (
        len(integrity_failures)
        + active_unknown
        + unclassified
    )

    if player_demand_events:
        demand_state = (
            "PLAYER_DEMAND_OBSERVED"
        )

    elif active_ahbot:
        demand_state = (
            "AHBOT_SUPPLY_AVAILABLE_NO_PLAYER_DEMAND"
        )

    else:
        demand_state = (
            "NO_AHBOT_SUPPLY_CURRENTLY_ACTIVE"
        )

    stamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    DEMAND_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    active_file = (
        DEMAND_ROOT
        / f"{stamp}-active.csv"
    )

    events_file = (
        DEMAND_ROOT
        / f"{stamp}-events.csv"
    )

    census_file = (
        DEMAND_ROOT
        / f"{stamp}-census.csv"
    )

    summary_file = (
        DEMAND_ROOT
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
            "3D.3",

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

        "pilot_a_items":
            len(
                pilot_a_ids
            ),

        "pilot_a_forms":
            len(
                census
            ),

        "demand_state":
            demand_state,

        "active_ahbot_listings":
            active_ahbot,

        "active_player_listings":
            active_player,

        "player_demand_from_bot_events":
            player_demand_events,

        "player_gil_spent":
            player_gil_spent,

        "demand_from_baseline_listings":
            baseline_listing_sales,

        "demand_from_postbaseline_listings":
            postbaseline_listing_sales,

        "organic_player_trades":
            organic_events,

        "active_unknown_rows":
            active_unknown,

        "new_unclassified_completed":
            unclassified,

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
        " Phase 3D.3 Player Demand Response"
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
        "Observation UTC:                    ",
        observed_utc,
    )

    print(
        "Baseline UTC:                       ",
        baseline.get(
            "baseline_utc"
        ),
    )

    print()
    print(
        "Active AHBot listings:              ",
        active_ahbot,
    )

    print(
        "Active player listings:             ",
        active_player,
    )

    print()
    print(
        "AHBot -> Player transactions:        ",
        player_demand_events,
    )

    print(
        "Player gil spent:                   ",
        player_gil_spent,
    )

    print(
        "Sales from T0 listings:             ",
        baseline_listing_sales,
    )

    print(
        "Sales from later listings:          ",
        postbaseline_listing_sales,
    )

    print(
        "Organic player trades:              ",
        organic_events,
    )

    print()
    print(
        "Unknown active rows:                ",
        active_unknown,
    )

    print(
        "Unclassified new completions:       ",
        unclassified,
    )

    print(
        "Integrity failures:                 ",
        integrity_count,
    )

    print()
    print(
        "Demand state:",
        demand_state,
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
