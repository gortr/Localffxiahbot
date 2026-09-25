from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import func

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

CONFIG_FILE = ROOT / "bin" / "config.yaml"

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

CENSUS_CSV = (
    OBS_ROOT
    / "baseline-census.csv"
)

ACTIVE_CSV = (
    OBS_ROOT
    / "baseline-active-listings.csv"
)

HISTORY_CSV = (
    OBS_ROOT
    / "baseline-completed-history.csv"
)


PILOT_A_IDS = {
    657,
    788,
    790,
    793,
    798,
    808,
    811,
    815,
    1234,
    4591,
    18732,
}

PILOT_B_IDS = {
    5644,
    5653,
    5655,
}

ALL_PILOT_IDS = (
    PILOT_A_IDS
    | PILOT_B_IDS
)

EXPECTED_SELLER_ROWS = 173
EXPECTED_BUYER_ROWS = 155

EXPECTED_SELLER_SHA = (
    "c6332ee2248eba43fb4a84a55f820e006"
    "921d86b830a7e7d1dc14fbe63e45e17"
)

EXPECTED_BUYER_SHA = (
    "2bcceed573437be7c6ceb255ca2c4d76a"
    "ad4d386364be7637a711095c2102363"
)


class BaselineError(RuntimeError):
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


def role_for_active(
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


def lane_for(itemid: int) -> str:
    if itemid in PILOT_A_IDS:
        return "PILOT_A_SELLER"

    if itemid in PILOT_B_IDS:
        return "PILOT_B_BUYER"

    raise BaselineError(
        f"Unexpected itemid: {itemid}"
    )


def form_name(stack: int) -> str:
    return (
        "STACK"
        if int(stack)
        else "SINGLE"
    )


def gitignored() -> bool:
    probe = (
        "economy/runtime-observations/"
        "player-market-response/.probe"
    )

    result = subprocess.run(
        [
            "git",
            "check-ignore",
            "-q",
            probe,
        ],
        cwd=ROOT,
        check=False,
    )

    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--replace-baseline",
        action="store_true",
        help=(
            "Explicitly replace an existing "
            "3D baseline."
        ),
    )

    args = parser.parse_args()

    required = [
        CONFIG_FILE,
        SELLER_RUNTIME,
        BUYER_RUNTIME,
    ]

    for path in required:
        if not path.exists():
            raise BaselineError(
                f"Missing required input: {path}"
            )

    if (
        BASELINE_JSON.exists()
        and not args.replace_baseline
    ):
        raise BaselineError(
            "3D baseline already exists. "
            "Refusing to overwrite it. "
            "Use --replace-baseline only if "
            "you intentionally want a new "
            "observation epoch."
        )

    if not gitignored():
        raise BaselineError(
            "economy/runtime-observations/ "
            "is not Git-ignored."
        )

    seller_sha = sha256(
        SELLER_RUNTIME
    )

    buyer_sha = sha256(
        BUYER_RUNTIME
    )

    if seller_sha != EXPECTED_SELLER_SHA:
        raise BaselineError(
            "Seller runtime SHA differs from "
            "the closed Pilot A runtime."
        )

    if buyer_sha != EXPECTED_BUYER_SHA:
        raise BaselineError(
            "Buyer runtime SHA differs from "
            "the closed Pilot B runtime."
        )

    seller = pd.read_csv(
        SELLER_RUNTIME,
        low_memory=False,
    )

    buyer = pd.read_csv(
        BUYER_RUNTIME,
        low_memory=False,
    )

    if len(seller) != EXPECTED_SELLER_ROWS:
        raise BaselineError(
            f"Expected {EXPECTED_SELLER_ROWS} "
            "seller rows; "
            f"found {len(seller)}."
        )

    if len(buyer) != EXPECTED_BUYER_ROWS:
        raise BaselineError(
            f"Expected {EXPECTED_BUYER_ROWS} "
            "buyer rows; "
            f"found {len(buyer)}."
        )

    seller_ids = set(
        seller["itemid"].astype(int)
    )

    buyer_ids = set(
        buyer["itemid"].astype(int)
    )

    missing_a = (
        PILOT_A_IDS
        - seller_ids
    )

    missing_b = (
        PILOT_B_IDS
        - buyer_ids
    )

    if missing_a:
        raise BaselineError(
            "Pilot A items missing from seller "
            f"runtime: {sorted(missing_a)}"
        )

    if missing_b:
        raise BaselineError(
            "Pilot B items missing from buyer "
            f"runtime: {sorted(missing_b)}"
        )

    # Build the canonical item-name mapping directly
    # from the active runtimes.
    names: dict[int, str] = {}

    for _, row in seller.iterrows():
        iid = int(row["itemid"])

        if iid in PILOT_A_IDS:
            names[iid] = str(
                row["name"]
            )

    for _, row in buyer.iterrows():
        iid = int(row["itemid"])

        if iid in PILOT_B_IDS:
            names[iid] = str(
                row["name"]
            )

    if set(names) != ALL_PILOT_IDS:
        raise BaselineError(
            "Could not resolve all pilot names."
        )

    config = Config.from_yaml(
        CONFIG_FILE
    )

    bot_name = str(
        config.name
    ).strip()

    if not bot_name:
        raise BaselineError(
            "AHBot name is empty."
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
        raise BaselineError(
            "Cannot connect to auction database."
        )

    # Primary future-event watermark.
    #
    # Future 3D transaction scans must use
    # sell_date >= baseline_epoch. Auction row ID
    # alone is insufficient because a listing that
    # already exists now can complete later without
    # receiving a new ID.
    baseline_epoch = int(
        time.time()
    )

    baseline_utc = utc_iso(
        baseline_epoch
    )

    active_records = []
    history_records = []

    max_auction_id = 0
    max_completed_sell_date = 0
    pilot_max_id = 0

    integrity_anomalies = []

    with manager.scoped_session() as session:
        global_max = (
            session.query(
                func.max(
                    AuctionHouse.id
                )
            )
            .scalar()
        )

        max_auction_id = int(
            global_max or 0
        )

        rows = (
            session.query(AuctionHouse)
            .filter(
                AuctionHouse.itemid.in_(
                    sorted(
                        ALL_PILOT_IDS
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

            buyer_name = str(
                row.buyer_name or ""
            ).strip()

            seller_name = str(
                row.seller_name or ""
            ).strip()

            pilot_max_id = max(
                pilot_max_id,
                int(row.id),
            )

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
                integrity_anomalies.append(
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
                integrity_anomalies.append(
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
                integrity_anomalies.append(
                    {
                        "id":
                            int(row.id),
                        "reason":
                            "NEITHER_ACTIVE_NOR_COMPLETED",
                    }
                )

            if is_active:
                active_records.append({
                    "id":
                        int(row.id),

                    "itemid":
                        iid,

                    "name":
                        names[iid],

                    "pilot_lane":
                        lane_for(iid),

                    "stack":
                        stack,

                    "form":
                        form_name(stack),

                    "seller":
                        int(row.seller),

                    "seller_name":
                        seller_name,

                    "seller_role":
                        role_for_active(
                            row,
                            bot_name,
                        ),

                    "listing_date":
                        int(row.date or 0),

                    "listing_date_utc":
                        utc_iso(
                            int(
                                row.date or 0
                            )
                        ),

                    "ask_price":
                        int(row.price or 0),

                    "buyer_name":
                        buyer_name,

                    "sale":
                        sale,

                    "sell_date":
                        sell_date,
                })

            if is_completed:
                max_completed_sell_date = max(
                    max_completed_sell_date,
                    sell_date,
                )

                classification = (
                    classify_completed(
                        row,
                        bot_name,
                    )
                )

                history_records.append({
                    "id":
                        int(row.id),

                    "itemid":
                        iid,

                    "name":
                        names[iid],

                    "pilot_lane":
                        lane_for(iid),

                    "stack":
                        stack,

                    "form":
                        form_name(stack),

                    "seller":
                        int(row.seller),

                    "seller_name":
                        seller_name,

                    "buyer_name":
                        buyer_name,

                    "ask_price":
                        int(row.price or 0),

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

        if (
            session.new
            or session.dirty
            or session.deleted
        ):
            raise BaselineError(
                "Read-only baseline unexpectedly "
                "created session mutations."
            )

    if integrity_anomalies:
        raise BaselineError(
            "Auction integrity anomalies detected "
            "for pilot items: "
            + json.dumps(
                integrity_anomalies,
                sort_keys=True,
            )
        )

    active = pd.DataFrame(
        active_records
    )

    history = pd.DataFrame(
        history_records
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
                "listing_date",
                "listing_date_utc",
                "ask_price",
                "buyer_name",
                "sale",
                "sell_date",
            ]
        )

    if history.empty:
        history = pd.DataFrame(
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
            ]
        )

    census_records = []

    classifications = [
        "SYNTHETIC_HISTORY",
        "PLAYER_SUPPLY_TO_BOT",
        "PLAYER_DEMAND_FROM_BOT",
        "ORGANIC_PLAYER_TRADE",
        "UNCLASSIFIED",
    ]

    for iid in sorted(
        ALL_PILOT_IDS
    ):
        for stack in [0, 1]:
            active_form = active[
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

            history_form = history[
                (
                    history["itemid"]
                    .astype(int)
                    == iid
                )
                & (
                    history["stack"]
                    .astype(int)
                    == stack
                )
            ]

            record = {
                "itemid":
                    iid,

                "name":
                    names[iid],

                "pilot_lane":
                    lane_for(iid),

                "stack":
                    stack,

                "form":
                    form_name(stack),

                "active_total":
                    len(
                        active_form
                    ),

                "active_ahbot":
                    int(
                        (
                            active_form[
                                "seller_role"
                            ]
                            == "AHBOT"
                        ).sum()
                    ),

                "active_player":
                    int(
                        (
                            active_form[
                                "seller_role"
                            ]
                            == "PLAYER"
                        ).sum()
                    ),

                "active_unknown":
                    int(
                        (
                            active_form[
                                "seller_role"
                            ]
                            == "UNKNOWN"
                        ).sum()
                    ),

                "completed_total":
                    len(
                        history_form
                    ),
            }

            for classification in classifications:
                record[
                    classification.lower()
                ] = int(
                    (
                        history_form[
                            "classification"
                        ]
                        == classification
                    ).sum()
                )

            real_mask = (
                history_form[
                    "classification"
                ].isin([
                    "PLAYER_SUPPLY_TO_BOT",
                    "PLAYER_DEMAND_FROM_BOT",
                    "ORGANIC_PLAYER_TRADE",
                ])
            )

            record[
                "player_involved_completed"
            ] = int(
                real_mask.sum()
            )

            if len(
                history_form
            ):
                record[
                    "last_completed_sell_date"
                ] = int(
                    history_form[
                        "sell_date"
                    ].max()
                )

                record[
                    "last_completed_utc"
                ] = utc_iso(
                    record[
                        "last_completed_sell_date"
                    ]
                )
            else:
                record[
                    "last_completed_sell_date"
                ] = 0

                record[
                    "last_completed_utc"
                ] = ""

            census_records.append(
                record
            )

    census = pd.DataFrame(
        census_records
    ).sort_values(
        [
            "pilot_lane",
            "itemid",
            "stack",
        ]
    )

    OBS_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    active = active.sort_values(
        [
            "itemid",
            "stack",
            "id",
        ]
    )

    history = history.sort_values(
        [
            "sell_date",
            "id",
        ]
    )

    active.to_csv(
        ACTIVE_CSV,
        index=False,
    )

    history.to_csv(
        HISTORY_CSV,
        index=False,
    )

    census.to_csv(
        CENSUS_CSV,
        index=False,
    )

    classification_counts = {
        classification:
            int(
                (
                    history[
                        "classification"
                    ]
                    == classification
                ).sum()
            )
        for classification
        in classifications
    }

    summary = {
        "status":
            "PASS",

        "phase":
            "3D.1",

        "baseline_locked":
            True,

        "baseline_epoch":
            baseline_epoch,

        "baseline_utc":
            baseline_utc,

        "future_transaction_rule":
            (
                "sell_date >= baseline_epoch"
            ),

        "auction_id_role":
            (
                "secondary diagnostic only; "
                "not the primary transaction "
                "watermark"
            ),

        "global_max_auction_id":
            max_auction_id,

        "pilot_max_auction_id":
            pilot_max_id,

        "max_completed_sell_date":
            max_completed_sell_date,

        "max_completed_sell_date_utc":
            utc_iso(
                max_completed_sell_date
            ),

        "ahbot_name":
            bot_name,

        "seller_runtime_rows":
            len(seller),

        "buyer_runtime_rows":
            len(buyer),

        "seller_runtime_sha256":
            seller_sha,

        "buyer_runtime_sha256":
            buyer_sha,

        "pilot_a_item_count":
            len(PILOT_A_IDS),

        "pilot_b_item_count":
            len(PILOT_B_IDS),

        "pilot_item_count":
            len(ALL_PILOT_IDS),

        "pilot_form_count":
            len(ALL_PILOT_IDS)
            * 2,

        "active_listing_rows":
            len(active),

        "active_ahbot_rows":
            int(
                (
                    active[
                        "seller_role"
                    ]
                    == "AHBOT"
                ).sum()
            ),

        "active_player_rows":
            int(
                (
                    active[
                        "seller_role"
                    ]
                    == "PLAYER"
                ).sum()
            ),

        "active_unknown_rows":
            int(
                (
                    active[
                        "seller_role"
                    ]
                    == "UNKNOWN"
                ).sum()
            ),

        "completed_history_rows":
            len(history),

        "classification_counts":
            classification_counts,

        "integrity_anomalies":
            0,

        "runtime_mutation_performed":
            0,

        "database_mutation_performed":
            0,

        "completed_history_modified":
            0,

        "price_influence_authorized":
            0,

        "stock_influence_authorized":
            0,

        "catalog_mutation_authorized":
            0,

        "auto_live_promotion":
            0,

        "observation_outputs": {
            "census":
                str(CENSUS_CSV),

            "active_listings":
                str(ACTIVE_CSV),

            "completed_history":
                str(HISTORY_CSV),
        },
    }

    BASELINE_JSON.write_text(
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
        " Phase 3D.1 Player Market "
        "Response Baseline"
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
        "Baseline UTC:               ",
        baseline_utc,
    )

    print(
        "Baseline epoch:             ",
        baseline_epoch,
    )

    print(
        "Global max auction ID:      ",
        max_auction_id,
    )

    print(
        "Pilot max auction ID:       ",
        pilot_max_id,
    )

    print()
    print(
        "Pilot A seller items:       ",
        len(PILOT_A_IDS),
    )

    print(
        "Pilot B buyer items:        ",
        len(PILOT_B_IDS),
    )

    print(
        "Total pilot items:          ",
        len(ALL_PILOT_IDS),
    )

    print(
        "Total pilot forms:          ",
        len(ALL_PILOT_IDS) * 2,
    )

    print()
    print(
        "Active listings:            ",
        len(active),
    )

    print(
        "  AHBot:                    ",
        summary[
            "active_ahbot_rows"
        ],
    )

    print(
        "  Player:                   ",
        summary[
            "active_player_rows"
        ],
    )

    print(
        "  Unknown:                  ",
        summary[
            "active_unknown_rows"
        ],
    )

    print()
    print(
        "Completed history rows:     ",
        len(history),
    )

    for (
        classification,
        count
    ) in classification_counts.items():
        print(
            f"  {classification:<27}",
            count,
        )

    print()
    print(
        "Future transaction rule:"
    )

    print(
        "  sell_date >= baseline_epoch"
    )

    print()
    print(
        "Runtime mutation performed:  0"
    )

    print(
        "Database mutation performed: 0"
    )

    print(
        "Completed history modified:  0"
    )

    print(
        "Price influence authorized:  0"
    )

    print(
        "Stock influence authorized:  0"
    )

    print(
        "Catalog mutation authorized: 0"
    )

    print(
        "Auto live promotion:         0"
    )

    print()
    print(
        "Baseline:",
        BASELINE_JSON,
    )

    print(
        "Census:  ",
        CENSUS_CSV,
    )

    print(
        "Active:  ",
        ACTIVE_CSV,
    )

    print(
        "History: ",
        HISTORY_CSV,
    )

    print()
    print("PASS")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
