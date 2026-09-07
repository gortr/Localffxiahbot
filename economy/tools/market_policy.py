from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


class MarketClass(StrEnum):
    """
    Canonical long-term AH economy classifications.

    BLOCKED
        Cannot participate in the Auction House economy at all.

    PROTECTED
        Kept out of synthetic supply/demand until an explicit future rule
        approves it. This includes progression/reward/special items and the
        conservative default for items not yet fully classified.

    DEMAND_ONLY
        May be purchased from players by AHBot, but AHBot must never create
        synthetic supply for it.

    SCARCE
        Synthetic supply/demand is permitted, but intentionally very limited.

    NORMAL
        Ordinary market goods.

    STAPLE
        High-volume consumables/materials that support normal gameplay.
    """

    BLOCKED = "BLOCKED"
    PROTECTED = "PROTECTED"
    DEMAND_ONLY = "DEMAND_ONLY"
    SCARCE = "SCARCE"
    NORMAL = "NORMAL"
    STAPLE = "STAPLE"


HARD_BLOCK_REASONS = frozenset(
    {
        "GM_ONLY",
        "NO_AUCTION",
        "EXCLUSIVE",
    }
)

SPECIAL_PROTECTED_REASONS = frozenset(
    {
        "KEYITEM_ONLY_NQ_OUTPUT",
        "KEYITEM_ONLY_HQ_OUTPUT",
        "KEYITEM_GATED_FISH",
        "QUEST_ONLY_FISH",
        "LEGENDARY_FISH",
        "HQ_ONLY_OUTPUT",
        "HQ_VARIANT_NAME",
    }
)

LEGACY_ALLOWED_CLASS_MAP = {
    "STAPLE": MarketClass.STAPLE,
    "STAPLE_CRYSTAL": MarketClass.STAPLE,
    "NORMAL": MarketClass.NORMAL,
    "SCARCE": MarketClass.SCARCE,
}

CLASS_BASE_CAPABILITIES = {
    MarketClass.BLOCKED: (False, False),
    MarketClass.PROTECTED: (False, False),
    MarketClass.DEMAND_ONLY: (False, True),
    MarketClass.SCARCE: (True, True),
    MarketClass.NORMAL: (True, True),
    MarketClass.STAPLE: (True, True),
}


@dataclass(frozen=True)
class ClassificationDecision:
    market_class: MarketClass
    classification_reason: str
    base_sell_capable: bool
    base_buy_capable: bool
    review_required: bool


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if value is None:
        return False

    text = str(value).strip().lower()

    return text in {
        "true",
        "1",
        "yes",
        "y",
    }


def split_reasons(value: Any) -> set[str]:
    if value is None:
        return set()

    text = str(value).strip()

    if not text or text.lower() == "nan":
        return set()

    return {
        reason.strip()
        for reason in text.split("|")
        if reason.strip()
    }


def _decision(
    market_class: MarketClass,
    reason: str,
    *,
    review_required: bool,
) -> ClassificationDecision:
    (
        sell_capable,
        buy_capable,
    ) = CLASS_BASE_CAPABILITIES[
        market_class
    ]

    return ClassificationDecision(
        market_class=market_class,
        classification_reason=reason,
        base_sell_capable=sell_capable,
        base_buy_capable=buy_capable,
        review_required=review_required,
    )


def classify_row(
    row: Mapping[str, Any],
) -> ClassificationDecision:
    """
    Classify one master-market row into the canonical market taxonomy.

    Phase 1B.3 intentionally preserves today's proven Seed market while
    default-denying everything that has not yet received richer provenance
    rules. Future 1B.3 work will promote appropriate PROTECTED rows into
    DEMAND_ONLY, SCARCE, NORMAL, or STAPLE as provenance becomes authoritative.
    """

    reasons = split_reasons(
        row.get(
            "rejection_reason"
        )
    )

    hard = sorted(
        reasons
        & HARD_BLOCK_REASONS
    )

    if hard:
        return _decision(
            MarketClass.BLOCKED,
            "HARD_BLOCK:"
            + "|".join(hard),
            review_required=False,
        )

    current_allowed = as_bool(
        row.get(
            "allowed"
        )
    )

    legacy_class = str(
        row.get(
            "market_class",
            "",
        )
    ).strip()

    if (
        legacy_class.lower()
        == "nan"
    ):
        legacy_class = ""

    if current_allowed:
        mapped = LEGACY_ALLOWED_CLASS_MAP.get(
            legacy_class
        )

        if mapped is None:
            # Current production-approved goods must not disappear simply
            # because an old builder emitted an unfamiliar class. Keep the
            # item market-capable, but make the condition visible for review.
            return _decision(
                MarketClass.NORMAL,
                (
                    "CURRENT_SEED_APPROVED:"
                    f"UNMAPPED_LEGACY_CLASS:{legacy_class or 'EMPTY'}"
                ),
                review_required=True,
            )

        return _decision(
            mapped,
            (
                "CURRENT_SEED_APPROVED:"
                f"{legacy_class}"
            ),
            review_required=False,
        )

    special = sorted(
        reasons
        & SPECIAL_PROTECTED_REASONS
    )

    if special:
        return _decision(
            MarketClass.PROTECTED,
            "SPECIAL_PROTECTION:"
            + "|".join(special),
            review_required=False,
        )

    # Conservative default for all items not yet covered by authoritative
    # expanded provenance. This is deliberate deny-by-default behavior.
    return _decision(
        MarketClass.PROTECTED,
        "DEFAULT_DENY_PENDING_PROVENANCE_REVIEW",
        review_required=True,
    )
