from __future__ import annotations

import datetime
import random
from collections import Counter
from dataclasses import dataclass
from typing import Any

import pandas as pd
from pydantic import SecretStr

from ffxiahbot import timeutils
from ffxiahbot.auction.browser import Browser
from ffxiahbot.auction.buyer import Buyer
from ffxiahbot.auction.cleaner import Cleaner
from ffxiahbot.auction.seller import Seller
from ffxiahbot.auction.worker import Worker
from ffxiahbot.common import progress_bar
from ffxiahbot.database import Database
from ffxiahbot.itemlist import ItemList
from ffxiahbot.logutils import capture, logger
from ffxiahbot.tables.auctionhouse import AuctionHouse


@dataclass(frozen=True)
class Manager(Worker):
    """Coordinate AH browsing, buying, cleaning, selling, and restocking."""

    blacklist: set[int]
    browser: Browser
    cleaner: Cleaner
    seller: Seller
    buyer: Buyer

    @classmethod
    def create_database_and_manager(
        cls,
        hostname: str,
        database: str,
        username: str,
        password: str | SecretStr,
        port: int,
        **kwargs: Any,
    ) -> Manager:
        db = Database.pymysql(
            hostname=hostname,
            database=database,
            username=username,
            password=password if isinstance(password, str) else password.get_secret_value(),
            port=port,
        )
        return cls.from_db(db=db, **kwargs)

    @classmethod
    def from_db(
        cls,
        db: Database,
        name: str,
        fail: bool = True,
        rollback: bool = True,
        blacklist: set[int] | None = None,
    ) -> Manager:
        return cls(
            db=db,
            blacklist=blacklist if blacklist is not None else set(),
            browser=Browser(db=db, rollback=rollback, fail=fail),
            cleaner=Cleaner(db=db, rollback=rollback, fail=fail),
            seller=Seller(
                db=db,
                rollback=rollback,
                fail=fail,
                seller=0,
                seller_name=name,
            ),
            buyer=Buyer(
                db=db,
                rollback=rollback,
                fail=fail,
                buyer_name=name,
            ),
            rollback=rollback,
            fail=fail,
        )

    def add_to_blacklist(self, rowid: int) -> None:
        logger.info("blacklisting: row=%d", rowid)
        self.blacklist.add(rowid)

    def buy_items(
        self,
        item_list: ItemList,
        use_buying_rates: bool = False,
    ) -> None:  # noqa: C901
        """Process eligible player listings and simulate market demand."""

        with self.scoped_session(fail=self.fail) as session:
            rows = session.query(AuctionHouse).filter(
                AuctionHouse.seller != self.seller.seller,
                AuctionHouse.sell_date == 0,
                AuctionHouse.sale == 0,
            )

            counts: Counter[str] = Counter()

            # Custom LSB safety rule:
            # A single buyer cycle may purchase at most one listing for each
            # (itemid, stack-state) pair. Singles and stacks are separate keys.
            purchased_this_cycle: set[tuple[int, bool]] = set()

            for row in rows:
                if row.id in self.blacklist:
                    logger.debug("skipping blacklisted row %d", row.id)
                    counts["blacklisted"] += 1
                    continue

                if row.itemid not in item_list:
                    logger.error("item missing from database: %d", row.itemid)
                    self.add_to_blacklist(row.id)
                    counts["unknown itemid"] += 1
                    continue

                with capture(fail=self.fail):
                    item = item_list[row.itemid]
                    purchase_key = (row.itemid, bool(row.stack))

                    if purchase_key in purchased_this_cycle:
                        logger.debug(
                            "purchase cap reached for this cycle: itemid=%d stack=%s",
                            row.itemid,
                            bool(row.stack),
                        )
                        counts["per-cycle purchase cap"] += 1
                        continue

                    if row.stack:
                        if not item.buy_stacks:
                            logger.debug(
                                "not allowed to buy item! itemid=%d",
                                row.itemid,
                            )
                            self.add_to_blacklist(row.id)
                            counts["forbidden item"] += 1
                            continue

                        if use_buying_rates and random.random() > item.buy_rate_stacks:
                            counts["buy rate too low"] += 1
                            continue

                        if self._buy_row(row, item.price_stacks):
                            purchased_this_cycle.add(purchase_key)
                            counts["stack purchased"] += 1
                        else:
                            counts["price too high"] += 1

                    else:
                        if not item.buy_single:
                            logger.debug(
                                "not allowed to buy item! itemid=%d",
                                row.itemid,
                            )
                            self.add_to_blacklist(row.id)
                            counts["forbidden item"] += 1
                            continue

                        if use_buying_rates and random.random() > item.buy_rate_single:
                            counts["buy rate too low"] += 1
                            continue

                        if self._buy_row(row, item.price_single):
                            purchased_this_cycle.add(purchase_key)
                            counts["single purchased"] += 1
                        else:
                            counts["price too high"] += 1

            counts_frame = pd.DataFrame.from_dict(
                counts,
                orient="index",
            ).rename(columns={0: "count"})

            if counts_frame.empty:
                logger.warning(
                    "no rows processed when buying items (no items for sale?)"
                )
            else:
                logger.debug("manager.buy_items counts: \n%s", counts_frame)

    def _buy_row(self, row: AuctionHouse, max_price: int) -> bool:
        """Buy a listing when its asking price is within the configured bid."""

        if max_price <= 0:
            logger.error("max buying price is zero! itemid=%d", row.itemid)
            self.add_to_blacklist(row.id)
            return False

        if row.price <= max_price:
            date = timeutils.timestamp(datetime.datetime.now())
            self.buyer.set_row_buyer_info(row, date, max_price)
            return True

        logger.info(
            "price too high! itemid=%d %d <= %d",
            row.itemid,
            row.price,
            max_price,
        )
        self.add_to_blacklist(row.id)
        return False

    def restock_items(
        self,
        item_list: ItemList,
        use_selling_rates: bool = False,
    ) -> None:
        """Restock configured synthetic listings."""

        with progress_bar(
            "[red]Restocking Items...",
            total=len(item_list),
        ) as (progress, task):
            for item in item_list.items.values():
                if item.sell_single and item.price_single > 0:
                    self._sell_item(
                        item.itemid,
                        stack=False,
                        price=item.price_single,
                        stock=item.stock_single,
                        rate=None if not use_selling_rates else item.sell_rate_single,
                    )
                    progress.update(task, advance=0.5)

                if item.sell_stacks and item.price_stacks > 0:
                    self._sell_item(
                        item.itemid,
                        stack=True,
                        price=item.price_stacks,
                        stock=item.stock_stacks,
                        rate=None if not use_selling_rates else item.sell_rate_stacks,
                    )
                    progress.update(task, advance=0.5)

    @property
    def _sell_time(self) -> int:
        """Timestamp used by AHBot for synthetic seller listings/history."""

        return timeutils.timestamp(datetime.datetime(2099, 1, 1))

    def _sell_item(
        self,
        itemid: int,
        stack: bool,
        price: int,
        stock: int,
        rate: float | None,
    ) -> None:
        """Maintain the configured stock target for one item form."""

        history_price = self.browser.get_price(
            itemid=itemid,
            stack=stack,
            seller=self.seller.seller,
        )

        if history_price is None or history_price <= 0:
            self.seller.set_history(
                itemid=itemid,
                stack=stack,
                price=price,
                date=self._sell_time,
                count=1,
            )

        # Custom LSB market-maker rule:
        # Count ALL active AH supply, not only AHBot's own listings. Player
        # listings therefore satisfy the market stock target and suppress
        # synthetic duplicate restocks.
        current_stock = self.browser.get_stock(
            itemid=itemid,
            stack=stack,
            seller=None,
        )

        if current_stock >= stock:
            return

        for _ in range(stock - current_stock):
            if rate is None or random.random() <= rate:
                self.seller.sell_item(
                    itemid=itemid,
                    stack=stack,
                    date=self._sell_time,
                    price=price,
                    count=1,
                )
