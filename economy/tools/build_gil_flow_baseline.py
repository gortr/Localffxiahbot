from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import func

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

TRANSACTIONS_CSV = (
    OBS_ROOT
    / "baseline-completed-transactions.csv"
)

ITEM_SUMMARY_CSV = (
    OBS_ROOT
    / "baseline-item-summary.csv"
)


class GilFlowError(RuntimeError):
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


def classify_transaction(
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

    if seller_is_bot and buyer_is_bot:
        return "SYNTHETIC_HISTORY"

    if seller_is_player and buyer_is_bot:
        return "PLAYER_SUPPLY_TO_BOT"

    if seller_is_bot and buyer_is_player:
        return "PLAYER_DEMAND_FROM_BOT"

    if seller_is_player and buyer_is_player:
        return "ORGANIC_PLAYER_TRADE"

    return "UNCLASSIFIED"


def flow_role(
    classification: str,
) -> str:
    if classification == "PLAYER_SUPPLY_TO_BOT":
        return "FAUCET"

    if classification == "PLAYER_DEMAND_FROM_BOT":
        return "SINK"

    if classification == "ORGANIC_PLAYER_TRADE":
        return "NEUTRAL"

    if classification == "SYNTHETIC_HISTORY":
        return "EXCLUDED_SYNTHETIC"

    return "UNCLASSIFIED"


def catalog_role(
    itemid: int,
    seller_ids: set[int],
    buyer_ids: set[int],
) -> str:
    in_seller = itemid in seller_ids
    in_buyer = itemid in buyer_ids

    if in_seller and in_buyer:
        return "TWO_SIDED"

    if in_seller:
        return "SELLER_ONLY"

    if in_buyer:
        return "BUYER_ONLY"

    return "OUTSIDE_CURRENT_RUNTIME"


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--replace-baseline",
        action="store_true",
        help=(
            "Explicitly replace the existing "
            "3E gil-flow baseline."
        ),
    )

    args = parser.parse_args()

    for path in [
        CONFIG_FILE,
        SELLER_RUNTIME,
        BUYER_RUNTIME,
    ]:
        if not path.exists():
            raise GilFlowError(
                f"Missing required input: {path}"
            )

    if (
        BASELINE_JSON.exists()
        and not args.replace_baseline
    ):
        raise GilFlowError(
            "3E gil-flow baseline already exists. "
            "Refusing to overwrite it. Use "
            "--replace-baseline only if an "
            "intentional new accounting epoch "
            "is required."
        )

    seller = pd.read_csv(
        SELLER_RUNTIME,
        low_memory=False,
    )

    buyer = pd.read_csv(
        BUYER_RUNTIME,
        low_memory=False,
    )

    seller_ids = set(
        seller["itemid"].astype(int)
    )

    buyer_ids = set(
        buyer["itemid"].astype(int)
    )

    names: dict[int, str] = {}

    for frame in [seller, buyer]:
        for _, row in frame.iterrows():
            names[
                int(row["itemid"])
            ] = str(
                row["name"]
            )

    seller_sha = sha256(
        SELLER_RUNTIME
    )

    buyer_sha = sha256(
        BUYER_RUNTIME
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
        raise GilFlowError(
            "Cannot connect to auction database."
        )

    baseline_epoch = int(
        time.time()
    )

    baseline_utc = utc_iso(
        baseline_epoch
    )

    records = []
    integrity_failures = []

    global_max_id = 0
    max_completed_sell_date = 0

    with manager.scoped_session() as session:
        global_max_id = int(
            session.query(
                func.max(
                    AuctionHouse.id
                )
            ).scalar()
            or 0
        )

        rows = (
            session.query(
                AuctionHouse
            )
            .filter(
                AuctionHouse.sell_date != 0
            )
            .all()
        )

        for row in rows:
            row_id = int(
                row.id
            )

            itemid = int(
                row.itemid
            )

            stack = int(
                row.stack
            )

            ask_price = int(
                row.price or 0
            )

            paid_price = int(
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

            if paid_price <= 0:
                integrity_failures.append({
                    "id":
                        row_id,

                    "reason":
                        "COMPLETED_WITH_NONPOSITIVE_SALE",
                })

            classification = (
                classify_transaction(
                    row,
                    bot_name,
                )
            )

            role = flow_role(
                classification
            )

            faucet_gil = (
                paid_price
                if role == "FAUCET"
                else 0
            )

            sink_gil = (
                paid_price
                if role == "SINK"
                else 0
            )

            neutral_gil = (
                paid_price
                if role == "NEUTRAL"
                else 0
            )

            synthetic_gil = (
                paid_price
                if role == "EXCLUDED_SYNTHETIC"
                else 0
            )

            # Positive means AHBot removed gil
            # from the player economy.
            net_player_gil_sink = (
                sink_gil
                - faucet_gil
            )

            max_completed_sell_date = max(
                max_completed_sell_date,
                sell_date,
            )

            records.append({
                "id":
                    row_id,

                "itemid":
                    itemid,

                "name":
                    names.get(
                        itemid,
                        "",
                    ),

                "catalog_role":
                    catalog_role(
                        itemid,
                        seller_ids,
                        buyer_ids,
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
                    ask_price,

                "paid_price":
                    paid_price,

                "sell_date":
                    sell_date,

                "sell_date_utc":
                    utc_iso(
                        sell_date
                    ),

                "classification":
                    classification,

                "flow_role":
                    role,

                "faucet_gil":
                    faucet_gil,

                "sink_gil":
                    sink_gil,

                "neutral_gil":
                    neutral_gil,

                "excluded_synthetic_gil":
                    synthetic_gil,

                "net_player_gil_sink":
                    net_player_gil_sink,
            })

        if (
            session.new
            or session.dirty
            or session.deleted
        ):
            raise GilFlowError(
                "Read-only baseline created "
                "unexpected DB session mutations."
            )

    transactions = pd.DataFrame(
        records
    )

    if transactions.empty:
        transactions = pd.DataFrame(
            columns=[
                "id",
                "itemid",
                "name",
                "catalog_role",
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
                "flow_role",
                "faucet_gil",
                "sink_gil",
                "neutral_gil",
                "excluded_synthetic_gil",
                "net_player_gil_sink",
            ]
        )

    transactions = transactions.sort_values(
        [
            "sell_date",
            "id",
        ]
    )

    item_records = []

    for (
        itemid,
        group
    ) in transactions.groupby(
        "itemid",
        sort=True,
    ):
        itemid = int(
            itemid
        )

        faucet_mask = (
            group["flow_role"]
            == "FAUCET"
        )

        sink_mask = (
            group["flow_role"]
            == "SINK"
        )

        neutral_mask = (
            group["flow_role"]
            == "NEUTRAL"
        )

        synthetic_mask = (
            group["flow_role"]
            == "EXCLUDED_SYNTHETIC"
        )

        unclassified_mask = (
            group["flow_role"]
            == "UNCLASSIFIED"
        )

        faucet_count = int(
            faucet_mask.sum()
        )

        sink_count = int(
            sink_mask.sum()
        )

        faucet_gil = int(
            group[
                "faucet_gil"
            ].sum()
        )

        sink_gil = int(
            group[
                "sink_gil"
            ].sum()
        )

        item_records.append({
            "itemid":
                itemid,

            "name":
                names.get(
                    itemid,
                    "",
                ),

            "catalog_role":
                catalog_role(
                    itemid,
                    seller_ids,
                    buyer_ids,
                ),

            "completed_transactions":
                len(group),

            "faucet_transactions":
                faucet_count,

            "sink_transactions":
                sink_count,

            "organic_transactions":
                int(
                    neutral_mask.sum()
                ),

            "synthetic_transactions":
                int(
                    synthetic_mask.sum()
                ),

            "unclassified_transactions":
                int(
                    unclassified_mask.sum()
                ),

            "faucet_gil":
                faucet_gil,

            "sink_gil":
                sink_gil,

            "net_player_gil_sink":
                sink_gil
                - faucet_gil,
        })

    item_summary = pd.DataFrame(
        item_records
    )

    if item_summary.empty:
        item_summary = pd.DataFrame(
            columns=[
                "itemid",
                "name",
                "catalog_role",
                "completed_transactions",
                "faucet_transactions",
                "sink_transactions",
                "organic_transactions",
                "synthetic_transactions",
                "unclassified_transactions",
                "faucet_gil",
                "sink_gil",
                "net_player_gil_sink",
            ]
        )

    classification_counts = (
        transactions[
            "classification"
        ]
        .value_counts()
        .sort_index()
        .to_dict()
    )

    flow_counts = (
        transactions[
            "flow_role"
        ]
        .value_counts()
        .sort_index()
        .to_dict()
    )

    gross_faucet = int(
        transactions[
            "faucet_gil"
        ].sum()
    )

    gross_sink = int(
        transactions[
            "sink_gil"
        ].sum()
    )

    neutral_gil = int(
        transactions[
            "neutral_gil"
        ].sum()
    )

    synthetic_gil = int(
        transactions[
            "excluded_synthetic_gil"
        ].sum()
    )

    net_sink = (
        gross_sink
        - gross_faucet
    )

    real_ahbot_transactions = int(
        (
            transactions[
                "flow_role"
            ].isin(
                [
                    "FAUCET",
                    "SINK",
                ]
            )
        ).sum()
    )

    unclassified = int(
        (
            transactions[
                "flow_role"
            ]
            == "UNCLASSIFIED"
        ).sum()
    )

    integrity_count = (
        len(integrity_failures)
        + unclassified
    )

    OBS_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    transactions.to_csv(
        TRANSACTIONS_CSV,
        index=False,
    )

    item_summary.to_csv(
        ITEM_SUMMARY_CSV,
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
            "3E.1",

        "baseline_locked":
            True,

        "baseline_epoch":
            baseline_epoch,

        "baseline_utc":
            baseline_utc,

        "future_transaction_rule":
            (
                "sell_date >= baseline_epoch "
                "AND id not in baseline "
                "completed transaction IDs"
            ),

        "net_flow_sign_convention":
            (
                "net_player_gil_sink = "
                "sink_gil - faucet_gil; "
                "positive removes gil, "
                "negative injects gil"
            ),

        "global_max_auction_id":
            global_max_id,

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

        "completed_transactions":
            len(transactions),

        "real_ahbot_transactions":
            real_ahbot_transactions,

        "classification_counts":
            {
                str(k):
                    int(v)
                for k, v
                in classification_counts.items()
            },

        "flow_counts":
            {
                str(k):
                    int(v)
                for k, v
                in flow_counts.items()
            },

        "gross_faucet_gil":
            gross_faucet,

        "gross_sink_gil":
            gross_sink,

        "net_player_gil_sink":
            net_sink,

        "organic_neutral_gil":
            neutral_gil,

        "excluded_synthetic_gil":
            synthetic_gil,

        "unclassified_transactions":
            unclassified,

        "integrity_failures":
            integrity_count,

        "database_mutation_performed":
            0,

        "runtime_mutation_performed":
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
            "transactions":
                str(
                    TRANSACTIONS_CSV
                ),

            "item_summary":
                str(
                    ITEM_SUMMARY_CSV
                ),
        },

        "integrity_details":
            integrity_failures,
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
        " Phase 3E.1 Gil-Flow "
        "Taxonomy + Baseline"
    )
    print("=" * 108)
    print()

    print(
        "Baseline UTC:                  ",
        baseline_utc,
    )

    print(
        "Global max auction ID:         ",
        global_max_id,
    )

    print()
    print(
        "Completed transactions:        ",
        len(transactions),
    )

    print(
        "Real AHBot transactions:       ",
        real_ahbot_transactions,
    )

    print()
    print("Transaction classifications:")

    for key in sorted(
        classification_counts
    ):
        print(
            f"  {key:<28}",
            classification_counts[
                key
            ],
        )

    print()
    print("Economic flow roles:")

    for key in sorted(
        flow_counts
    ):
        print(
            f"  {key:<28}",
            flow_counts[
                key
            ],
        )

    print()
    print(
        "Gross AHBot faucet:            ",
        gross_faucet,
    )

    print(
        "Gross AHBot sink:              ",
        gross_sink,
    )

    print(
        "Net player gil sink:           ",
        net_sink,
    )

    print()
    print(
        "Organic neutral gil:           ",
        neutral_gil,
    )

    print(
        "Excluded synthetic gil:        ",
        synthetic_gil,
    )

    print()
    print(
        "Unclassified transactions:     ",
        unclassified,
    )

    print(
        "Integrity failures:            ",
        integrity_count,
    )

    print()
    print(
        "Sign convention:"
    )

    print(
        "  positive net = gil removed "
        "from players"
    )

    print(
        "  negative net = gil injected "
        "into players"
    )

    print()
    print(
        "Database mutation performed:   0"
    )

    print(
        "Runtime mutation performed:    0"
    )

    print(
        "Price change authorized:       0"
    )

    print(
        "Rate change authorized:        0"
    )

    print(
        "Stock change authorized:       0"
    )

    print(
        "Catalog change authorized:     0"
    )

    print(
        "Auto live promotion:           0"
    )

    print()
    print(
        "Baseline:",
        BASELINE_JSON,
    )

    print(
        "Transactions:",
        TRANSACTIONS_CSV,
    )

    print(
        "Item summary:",
        ITEM_SUMMARY_CSV,
    )

    print()
    print(summary["status"])

    if integrity_count:
        raise SystemExit(1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
