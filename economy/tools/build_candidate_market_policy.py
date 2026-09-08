from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

MASTER_FILE = GENERATED / "master-market.csv"
CLASSIFICATION_FILE = GENERATED / "market-classification.csv"
PROVENANCE_FILE = GENERATED / "item-provenance.csv"
MOB_SOURCE_FILE = GENERATED / "mob-source-classification.csv"
VENDOR_PRICE_FILE = GENERATED / "vendor-prices.csv"

OUTPUT_FILE = GENERATED / "candidate-market-policy.csv"
SUMMARY_FILE = REPORTS / "candidate-market-policy-summary.json"

BLOCKED = "BLOCKED"
PROTECTED = "PROTECTED"
DEMAND_ONLY = "DEMAND_ONLY"
SCARCE = "SCARCE"
NORMAL = "NORMAL"
STAPLE = "STAPLE"

VALID_CLASSES = {
    BLOCKED,
    PROTECTED,
    DEMAND_ONLY,
    SCARCE,
    NORMAL,
    STAPLE,
}

ACTIVE_CLASSES = {
    DEMAND_ONLY,
    SCARCE,
    NORMAL,
    STAPLE,
}


class CandidatePolicyError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip()

    if text.lower() == "nan":
        return ""

    return text


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    if value is None or pd.isna(value):
        return False

    return clean_text(value).lower() in {
        "true",
        "1",
        "yes",
        "y",
    }


def as_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0

    return int(value)


def split_reasons(value: Any) -> set[str]:
    text = clean_text(value)

    if not text:
        return set()

    return {
        part.strip()
        for part in text.split("|")
        if part.strip()
    }


def require_columns(
    frame: pd.DataFrame,
    required: set[str],
    label: str,
) -> None:
    missing = sorted(
        required - set(frame.columns)
    )

    if missing:
        raise CandidatePolicyError(
            f"{label} missing required columns: "
            + ", ".join(missing)
        )


def require_unique(
    frame: pd.DataFrame,
    label: str,
) -> None:
    if not frame["itemid"].duplicated().any():
        return

    duplicate_ids = (
        frame.loc[
            frame["itemid"].duplicated(
                keep=False
            ),
            "itemid",
        ]
        .astype(int)
        .unique()
        .tolist()
    )

    raise CandidatePolicyError(
        f"{label} contains duplicate item IDs: "
        f"{duplicate_ids[:20]}"
    )


def classify_candidate(
    row: pd.Series,
) -> tuple[str, str, bool]:
    itemid = as_int(
        row.get("itemid")
    )

    if as_bool(
        row.get("gm_only")
    ):
        return (
            BLOCKED,
            "GM_ONLY",
            False,
        )

    if as_bool(
        row.get("no_auction")
    ):
        return (
            BLOCKED,
            "NO_AUCTION",
            False,
        )

    if as_bool(
        row.get("exclusive")
    ):
        return (
            BLOCKED,
            "EXCLUSIVE",
            False,
        )

    if as_bool(
        row.get(
            "current_seed_sell_allowed"
        )
    ):
        current_class = clean_text(
            row.get(
                "current_market_class"
            )
        )

        if current_class not in {
            STAPLE,
            NORMAL,
            SCARCE,
        }:
            raise CandidatePolicyError(
                f"Current Seed seller item {itemid} "
                "has invalid canonical class "
                f"{current_class!r}"
            )

        return (
            current_class,
            "CURRENT_SEED_APPROVED",
            False,
        )

    if as_bool(
        row.get(
            "fishing_disabled"
        )
    ):
        return (
            PROTECTED,
            "DISABLED_FISH",
            False,
        )

    if as_bool(
        row.get(
            "fishing_quest_only"
        )
    ):
        return (
            PROTECTED,
            "QUEST_ONLY_FISH",
            False,
        )

    if as_int(
        row.get(
            "fishing_required_keyitem"
        )
    ) > 0:
        return (
            PROTECTED,
            "KEYITEM_GATED_FISH",
            False,
        )

    if as_bool(
        row.get(
            "fishing_legendary"
        )
    ):
        return (
            PROTECTED,
            "LEGENDARY_FISH",
            False,
        )

    item_type = as_int(
        row.get(
            "item_type"
        )
    )

    if item_type == 4:
        return (
            PROTECTED,
            "PUPPET_POLICY_PENDING",
            True,
        )

    if as_bool(
        row.get(
            "scroll"
        )
    ):
        return (
            PROTECTED,
            "SCROLL_POLICY_PENDING",
            True,
        )

    mob_class = clean_text(
        row.get(
            "mob_source_class"
        )
    )

    has_ordinary_mob = as_bool(
        row.get(
            "has_ordinary_source"
        )
    )

    has_fishing = as_bool(
        row.get(
            "fishing"
        )
    )

    unrestricted_craft_output = (
        as_bool(
            row.get(
                "craft_nq_output"
            )
        )
        or as_bool(
            row.get(
                "craft_hq_output"
            )
        )
    )

    restricted_craft_output = (
        as_bool(
            row.get(
                "restricted_nq_output"
            )
        )
        or as_bool(
            row.get(
                "restricted_hq_output"
            )
        )
    )

    # vendor_item is intentionally broad for anti-arbitrage safety.
    # Only a trusted resolved NPC price counts as positive acquisition evidence.
    trusted_vendor_route = as_bool(
        row.get(
            "has_hard_floor"
        )
    )

    ordinary_acquisition = (
        has_ordinary_mob
        or has_fishing
        or unrestricted_craft_output
        or trusted_vendor_route
    )

    if (
        restricted_craft_output
        and not ordinary_acquisition
    ):
        return (
            PROTECTED,
            "RESTRICTED_CRAFT_OUTPUT_ONLY",
            False,
        )

    # Do not auto-create DEMAND_ONLY from LSB's mob evidence yet.
    # Audit found ordinary retail materials currently appearing as
    # NOTORIOUS_ONLY because LSB source coverage is incomplete.
    if (
        mob_class
        == "NOTORIOUS_ONLY"
        and not ordinary_acquisition
    ):
        return (
            PROTECTED,
            "NOTORIOUS_ONLY_RETAIL_SOURCE_AUDIT_REQUIRED",
            True,
        )

    if (
        mob_class
        in {
            "SPECIAL_ONLY",
            "NOTORIOUS_OR_SPECIAL_ONLY",
        }
        and not ordinary_acquisition
    ):
        return (
            PROTECTED,
            (
                "SPECIAL_MOB_SOURCE_ONLY"
                if mob_class
                == "SPECIAL_ONLY"
                else
                "NOTORIOUS_OR_SPECIAL_SOURCE_ONLY"
            ),
            True,
        )

    provenance_known = as_bool(
        row.get(
            "provenance_known"
        )
    )

    if not ordinary_acquisition:
        return (
            PROTECTED,
            (
                "KNOWN_PROVENANCE_NOT_YET_CLASSIFIED"
                if provenance_known
                else
                "UNKNOWN_ACQUISITION"
            ),
            True,
        )

    is_equipment = (
        as_bool(
            row.get(
                "is_equipment"
            )
        )
        or as_bool(
            row.get(
                "can_equip"
            )
        )
    )

    is_rare = as_bool(
        row.get(
            "rare"
        )
    )

    stack_size = as_int(
        row.get(
            "stack_size"
        )
    )

    reject = split_reasons(
        row.get(
            "rejection_reason"
        )
    )

    hq_variant = (
        "HQ_VARIANT_NAME"
        in reject
        or "HQ_ONLY_OUTPUT"
        in reject
    )

    if (
        is_equipment
        and stack_size <= 1
    ):
        if hq_variant:
            return (
                SCARCE,
                "HQ_EQUIPMENT_REQUIRES_MATURITY",
                True,
            )

        if is_rare:
            return (
                SCARCE,
                "ORDINARY_RARE_EQUIPMENT",
                True,
            )

        return (
            SCARCE,
            "ORDINARY_EQUIPMENT",
            False,
        )

    # Stackable equipment in FFXI often represents consumable ammo/jugs/tools.
    if is_rare:
        return (
            SCARCE,
            "ORDINARY_RARE_ITEM",
            True,
        )

    if hq_variant:
        return (
            SCARCE,
            "HQ_GOOD_REQUIRES_MATURITY",
            True,
        )

    if stack_size in {
        12,
        99,
    }:
        return (
            STAPLE,
            "ORDINARY_STACKABLE_COMMODITY",
            False,
        )

    return (
        NORMAL,
        "ORDINARY_NONSTACKABLE_GOOD",
        False,
    )


def main() -> int:
    REPORTS.mkdir(
        parents=True,
        exist_ok=True,
    )

    for path in [
        MASTER_FILE,
        CLASSIFICATION_FILE,
        PROVENANCE_FILE,
        MOB_SOURCE_FILE,
        VENDOR_PRICE_FILE,
    ]:
        if not path.exists():
            raise CandidatePolicyError(
                f"Missing required input: {path}"
            )

    master = pd.read_csv(
        MASTER_FILE,
        low_memory=False,
    )

    classification = pd.read_csv(
        CLASSIFICATION_FILE,
        low_memory=False,
    )

    provenance = pd.read_csv(
        PROVENANCE_FILE,
        low_memory=False,
    )

    mob = pd.read_csv(
        MOB_SOURCE_FILE,
        low_memory=False,
    )

    vendor_prices = pd.read_csv(
        VENDOR_PRICE_FILE,
        low_memory=False,
    )

    require_columns(
        master,
        {
            "itemid",
            "name",
            "allowed",
            "market_class",
            "base_sell",
            "stack_size",
            "rejection_reason",
        },
        "master-market.csv",
    )

    require_columns(
        classification,
        {
            "itemid",
            "market_class",
            "current_seed_sell_allowed",
            "current_seed_buy_allowed",
        },
        "market-classification.csv",
    )

    require_columns(
        provenance,
        {
            "itemid",
            "name",
            "item_type",
            "stack_size",
            "gm_only",
            "no_auction",
            "exclusive",
            "rare",
            "scroll",
            "can_equip",
            "is_equipment",
            "vendor_item",
            "craft_nq_output",
            "craft_hq_output",
            "restricted_nq_output",
            "restricted_hq_output",
            "fishing",
            "fishing_legendary",
            "fishing_required_keyitem",
            "fishing_quest_only",
            "fishing_disabled",
            "provenance_known",
        },
        "item-provenance.csv",
    )

    require_columns(
        mob,
        {
            "itemid",
            "mob_source_class",
            "has_ordinary_source",
            "has_notorious_source",
            "has_special_source",
        },
        "mob-source-classification.csv",
    )

    require_columns(
        vendor_prices,
        {
            "itemid",
            "has_hard_floor",
            "vendor_price_min",
        },
        "vendor-prices.csv",
    )

    for frame, label in [
        (master, "master-market.csv"),
        (
            classification,
            "market-classification.csv",
        ),
        (
            provenance,
            "item-provenance.csv",
        ),
        (
            mob,
            "mob-source-classification.csv",
        ),
        (
            vendor_prices,
            "vendor-prices.csv",
        ),
    ]:
        require_unique(
            frame,
            label,
        )

    merged = provenance.merge(
        master[
            [
                "itemid",
                "allowed",
                "market_class",
                "base_sell",
                "rejection_reason",
            ]
        ],
        on="itemid",
        how="left",
        validate="one_to_one",
        suffixes=(
            "",
            "_master",
        ),
    ).merge(
        classification[
            [
                "itemid",
                "market_class",
                "current_seed_sell_allowed",
                "current_seed_buy_allowed",
            ]
        ].rename(
            columns={
                "market_class":
                    "current_market_class",
            }
        ),
        on="itemid",
        how="left",
        validate="one_to_one",
    ).merge(
        mob[
            [
                "itemid",
                "mob_source_class",
                "has_ordinary_source",
                "has_notorious_source",
                "has_special_source",
            ]
        ],
        on="itemid",
        how="left",
        validate="one_to_one",
    ).merge(
        vendor_prices[
            [
                "itemid",
                "has_hard_floor",
                "vendor_price_min",
            ]
        ],
        on="itemid",
        how="left",
        validate="one_to_one",
    )

    rows: list[dict] = []

    for _, row in merged.iterrows():
        (
            candidate_class,
            candidate_reason,
            review_required,
        ) = classify_candidate(
            row
        )

        if candidate_class not in VALID_CLASSES:
            raise CandidatePolicyError(
                f"Invalid candidate class: {candidate_class}"
            )

        current_seed_sell_allowed = (
            as_bool(
                row.get(
                    "current_seed_sell_allowed"
                )
            )
        )

        current_seed_buy_allowed = (
            as_bool(
                row.get(
                    "current_seed_buy_allowed"
                )
            )
        )

        vendor_item = as_bool(
            row.get(
                "vendor_item"
            )
        )

        has_vendor_floor = as_bool(
            row.get(
                "has_hard_floor"
            )
        )

        candidate_sell_capable = (
            candidate_class
            in {
                SCARCE,
                NORMAL,
                STAPLE,
            }
        )

        candidate_buy_capable = (
            candidate_class
            in {
                DEMAND_ONLY,
                SCARCE,
                NORMAL,
                STAPLE,
            }
        )

        if vendor_item:
            candidate_buy_capable = False

        reject = split_reasons(
            row.get(
                "rejection_reason"
            )
        )

        base_sell = as_int(
            row.get(
                "base_sell"
            )
        )

        pricing_ready = (
            "NO_PRICE_ANCHOR"
            not in reject
            or base_sell > 0
            or has_vendor_floor
        )

        policy_ready = (
            candidate_class
            in ACTIVE_CLASSES
            and not review_required
            and pricing_ready
        )

        # No newly discovered item is deployment-ready during 1B.3.3.
        activation_ready = (
            current_seed_sell_allowed
            or current_seed_buy_allowed
        )

        if activation_ready:
            future_blocker = (
                "CURRENT_SEED_ACTIVE"
            )

        elif candidate_class not in ACTIVE_CLASSES:
            future_blocker = (
                "NONACTIVE_CLASS"
            )

        elif review_required:
            future_blocker = (
                "REVIEW_REQUIRED"
            )

        elif not pricing_ready:
            future_blocker = (
                "PRICING_NOT_READY"
            )

        else:
            future_blocker = (
                "MATURITY_GATE_PENDING"
            )

        rows.append(
            {
                "itemid":
                    as_int(
                        row.get(
                            "itemid"
                        )
                    ),

                "name":
                    clean_text(
                        row.get(
                            "name"
                        )
                    ),

                "candidate_class":
                    candidate_class,

                "candidate_reason":
                    candidate_reason,

                "candidate_sell_capable":
                    int(
                        candidate_sell_capable
                    ),

                "candidate_buy_capable":
                    int(
                        candidate_buy_capable
                    ),

                "review_required":
                    int(
                        review_required
                    ),

                "pricing_ready":
                    int(
                        pricing_ready
                    ),

                "policy_ready":
                    int(
                        policy_ready
                    ),

                "activation_ready":
                    int(
                        activation_ready
                    ),

                "future_activation_blocker":
                    future_blocker,

                "auto_live_promotion":
                    0,

                "current_seed_sell_allowed":
                    int(
                        current_seed_sell_allowed
                    ),

                "current_seed_buy_allowed":
                    int(
                        current_seed_buy_allowed
                    ),

                "current_market_class":
                    clean_text(
                        row.get(
                            "current_market_class"
                        )
                    ),

                "item_type":
                    as_int(
                        row.get(
                            "item_type"
                        )
                    ),

                "vendor_item":
                    int(
                        vendor_item
                    ),

                "has_vendor_price_floor":
                    int(
                        has_vendor_floor
                    ),

                "mob_source_class":
                    clean_text(
                        row.get(
                            "mob_source_class"
                        )
                    ),

                "has_ordinary_mob_source":
                    int(
                        as_bool(
                            row.get(
                                "has_ordinary_source"
                            )
                        )
                    ),

                "has_notorious_mob_source":
                    int(
                        as_bool(
                            row.get(
                                "has_notorious_source"
                            )
                        )
                    ),

                "has_special_mob_source":
                    int(
                        as_bool(
                            row.get(
                                "has_special_source"
                            )
                        )
                    ),

                "craft_nq_output":
                    as_int(
                        row.get(
                            "craft_nq_output"
                        )
                    ),

                "craft_hq_output":
                    as_int(
                        row.get(
                            "craft_hq_output"
                        )
                    ),

                "restricted_nq_output":
                    as_int(
                        row.get(
                            "restricted_nq_output"
                        )
                    ),

                "restricted_hq_output":
                    as_int(
                        row.get(
                            "restricted_hq_output"
                        )
                    ),

                "fishing":
                    as_int(
                        row.get(
                            "fishing"
                        )
                    ),

                "rare":
                    as_int(
                        row.get(
                            "rare"
                        )
                    ),

                "can_equip":
                    as_int(
                        row.get(
                            "can_equip"
                        )
                    ),

                "stack_size":
                    as_int(
                        row.get(
                            "stack_size"
                        )
                    ),

                "base_sell":
                    base_sell,

                "rejection_reason":
                    clean_text(
                        row.get(
                            "rejection_reason"
                        )
                    ),
            }
        )

    output = pd.DataFrame(
        rows
    )

    current_seed_seller = output[
        output[
            "current_seed_sell_allowed"
        ] == 1
    ]

    current_seed_buyer = output[
        output[
            "current_seed_buy_allowed"
        ] == 1
    ]

    if len(
        current_seed_seller
    ) != 167:
        raise CandidatePolicyError(
            "Current Seed seller count changed: "
            f"{len(current_seed_seller)}"
        )

    if len(
        current_seed_buyer
    ) != 159:
        raise CandidatePolicyError(
            "Current Seed buyer count changed: "
            f"{len(current_seed_buyer)}"
        )

    unexpected_activation = output[
        (output["activation_ready"] == 1)
        &
        (output["current_seed_sell_allowed"] == 0)
        &
        (output["current_seed_buy_allowed"] == 0)
    ]

    if not unexpected_activation.empty:
        raise CandidatePolicyError(
            "Non-Seed items became activation-ready: "
            f"{unexpected_activation['itemid'].astype(int).tolist()[:20]}"
        )

    if (
        output[
            "auto_live_promotion"
        ] != 0
    ).any():
        raise CandidatePolicyError(
            "1B.3.3 attempted live auto-promotion."
        )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    class_counts = (
        output[
            "candidate_class"
        ]
        .value_counts()
        .to_dict()
    )

    policy_ready_counts = (
        output[
            output[
                "policy_ready"
            ] == 1
        ][
            "candidate_class"
        ]
        .value_counts()
        .to_dict()
    )

    blocker_counts = (
        output[
            "future_activation_blocker"
        ]
        .value_counts()
        .to_dict()
    )

    summary = {
        "status":
            "PASS",

        "total_items":
            int(
                len(output)
            ),

        "classes":
            {
                str(k):
                    int(v)
                for k, v
                in class_counts.items()
            },

        "review_required":
            int(
                output[
                    "review_required"
                ].sum()
            ),

        "pricing_ready":
            int(
                output[
                    "pricing_ready"
                ].sum()
            ),

        "policy_ready_candidates":
            int(
                output[
                    "policy_ready"
                ].sum()
            ),

        "policy_ready_by_class":
            {
                str(k):
                    int(v)
                for k, v
                in policy_ready_counts.items()
            },

        "future_activation_blockers":
            {
                str(k):
                    int(v)
                for k, v
                in blocker_counts.items()
            },

        "candidate_sell_capable":
            int(
                output[
                    "candidate_sell_capable"
                ].sum()
            ),

        "candidate_buy_capable":
            int(
                output[
                    "candidate_buy_capable"
                ].sum()
            ),

        "current_seed_seller":
            167,

        "current_seed_buyer":
            159,

        "activation_ready_current":
            int(
                output[
                    "activation_ready"
                ].sum()
            ),

        "auto_live_promotions":
            0,
    }

    SUMMARY_FILE.write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print(
        "======================================"
    )
    print(
        " Candidate Market Policy v2"
    )
    print(
        "======================================"
    )

    print(
        f"Total items:               "
        f"{summary['total_items']:>6}"
    )

    print(
        f"Current Seed seller:       "
        f"{summary['current_seed_seller']:>6}"
    )

    print(
        f"Current Seed buyer:        "
        f"{summary['current_seed_buyer']:>6}"
    )

    print(
        f"Review required:           "
        f"{summary['review_required']:>6}"
    )

    print(
        f"Pricing ready:             "
        f"{summary['pricing_ready']:>6}"
    )

    print(
        f"Policy-ready candidates:   "
        f"{summary['policy_ready_candidates']:>6}"
    )

    print(
        f"Activation-ready current:  "
        f"{summary['activation_ready_current']:>6}"
    )

    print()
    print(
        "Candidate classes:"
    )

    for candidate_class in [
        BLOCKED,
        PROTECTED,
        DEMAND_ONLY,
        SCARCE,
        NORMAL,
        STAPLE,
    ]:
        print(
            f"  {candidate_class:<14} "
            f"{summary['classes'].get(candidate_class, 0):>6}"
        )

    print()
    print(
        "Policy-ready by class:"
    )

    for candidate_class in [
        DEMAND_ONLY,
        SCARCE,
        NORMAL,
        STAPLE,
    ]:
        print(
            f"  {candidate_class:<14} "
            f"{summary['policy_ready_by_class'].get(candidate_class, 0):>6}"
        )

    print()
    print(
        "Auto live promotions: 0"
    )

    print()
    print(
        f"Generated: {OUTPUT_FILE}"
    )

    print(
        f"Summary:   {SUMMARY_FILE}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
