from dataclasses import dataclass

from ffxiahbot.auction.worker import Worker
from ffxiahbot.logutils import capture, logger
from ffxiahbot.tables.auctionhouse import AuctionHouse


@dataclass(frozen=True)
class Cleaner(Worker):
    """
    Auction House cleaner.

    Local LSB customization:
    Clear operations remove only active, unsold listings.
    Completed auction history is preserved.
    """

    @staticmethod
    def _active_listing_query(session, seller: int | None = None):
        """
        Return a query containing only active, unsold AH listings.

        LandSandBoat / FFXIAHBot completed history rows have a sale value
        and/or sell_date populated. Those rows must never be removed by
        ordinary stock-maintenance operations.
        """

        query = session.query(AuctionHouse).filter(
            AuctionHouse.sale == 0,
            AuctionHouse.sell_date == 0,
        )

        if seller is not None:
            query = query.filter(
                AuctionHouse.seller == seller,
            )

        return query

    def clear(self, seller: int | None = None) -> None:
        """
        Clear active auction-house listings while preserving sale history.

        Args:
            seller:
                If supplied, clear only active listings belonging to that
                seller. If None, clear all active listings.

        Important:
            Completed sale-history rows are intentionally preserved.
        """

        if seller is not None:
            with capture(fail=self.fail):
                if not isinstance(seller, int) or seller < 0:
                    raise RuntimeError(
                        "invalid seller: %s",
                        seller,
                    )

        with self.scoped_session() as session:
            query = self._active_listing_query(
                session,
                seller=seller,
            )

            n = query.delete(
                synchronize_session=False,
            )

            if seller is None:
                logger.info(
                    "%d active auction listings dropped; "
                    "completed sale history preserved",
                    n,
                )
            else:
                logger.info(
                    "%d active auction listings dropped for seller=%d; "
                    "completed sale history preserved",
                    n,
                    seller,
                )

    def count(self, seller: int | None = None) -> int:
        """
        Count active listings that would be dropped by clear().

        Args:
            seller:
                If supplied, count only active listings belonging to that
                seller. If None, count all active listings.

        Returns:
            Number of active, unsold listings that would be removed.
        """

        if seller is not None:
            with capture(fail=self.fail):
                if not isinstance(seller, int) or seller < 0:
                    raise RuntimeError(
                        "invalid seller: %s",
                        seller,
                    )

        with self.scoped_session() as session:
            n = self._active_listing_query(
                session,
                seller=seller,
            ).count()

            return int(n)
