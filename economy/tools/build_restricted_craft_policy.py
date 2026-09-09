from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

REPORTS = ROOT / "economy" / "reports"
GENERATED = ROOT / "economy" / "generated"

DETAIL_FILE = REPORTS / "restricted-craft-recipe-audit.csv"
OUTPUT_FILE = GENERATED / "restricted-craft-policy.csv"
SUMMARY_FILE = REPORTS / "restricted-craft-policy-summary.json"


# ------------------------------------------------------------------
# Canonical market classes
# ------------------------------------------------------------------

PROTECTED = "PROTECTED"
SCARCE = "SCARCE"
NORMAL = "NORMAL"
STAPLE = "STAPLE"

# ------------------------------------------------------------------
# Economy maturity
# ------------------------------------------------------------------

STAGES = [
    "SEED",
    "GROWING",
    "ESTABLISHED",
    "MATURE",
    "ADVANCED",
    "FULL",
]

STAGE_INDEX = {
    name: index
    for index, name in enumerate(STAGES)
}


# ------------------------------------------------------------------
# Restricted crafting technique families
# ------------------------------------------------------------------

# These are ordinary Guild Point crafting techniques. Retail requires
# Novice rank to enter a Guild Point contract, so even a recipe with a
# very low nominal skill cap should not appear in the SEED economy.
GUILD_POINT_TECHNIQUES = {
    "WOOD_PURIFICATION",
    "WOOD_ENSORCELLMENT",
    "METAL_PURIFICATION",
    "METAL_ENSORCELLMENT",
    "CHAINWORK",
    "GOLD_PURIFICATION",
    "GOLD_ENSORCELLMENT",
    "CLOCKMAKING",
    "CLOTH_PURIFICATION",
    "CLOTH_ENSORCELLMENT",
    "LEATHER_PURIFICATION",
    "LEATHER_ENSORCELLMENT",
    "BONE_PURIFICATION",
    "BONE_ENSORCELLMENT",
    "ANIMA_SYNTHESIS",
    "ALCHEMIC_PURIFICATION",
    "ALCHEMIC_ENSORCELLMENT",
    "CONCOCTION",
    "IATROCHEMISTRY",
    "RAW_FISH_HANDLING",
    "NOODLE_KNEADING",
    "PATISSIER",
    "STEWPOT_MASTERY",
}

# These are late Escutcheon/master-crafting unlocks.
MASTER_TOMES = {
    "ALCHEMISTS_ARGENTUM_TOME",
    "CULINARIANS_AURUM_TOME",
}

# This recipe is explicitly quest-specific rather than a normal
# crafter-population unlock.
QUEST_RECIPE_KEYITEMS = {
    "MIASMAL_COUNTERAGENT_RECIPE",
}


class RestrictedCraftPolicyError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip()

    if text.lower() == "nan":
        return ""

    return text


def as_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0

    return int(value)


def stage_for_skill(skill: int) -> str:
    if skill <= 20:
        return "SEED"

    if skill <= 40:
        return "GROWING"

    if skill <= 60:
        return "ESTABLISHED"

    if skill <= 80:
        return "MATURE"

    if skill <= 100:
        return "ADVANCED"

    return "FULL"


def later_stage(stage: str) -> str:
    index = STAGE_INDEX[stage]

    if index >= len(STAGES) - 1:
        return "FULL"

    return STAGES[
        index + 1
    ]


def max_stage(
    left: str,
    right: str,
) -> str:
    return (
        left
        if STAGE_INDEX[left]
        >= STAGE_INDEX[right]
        else right
    )


def classify_base_item(
    *,
    can_equip: bool,
    rare: bool,
    stack_size: int,
    hq_only: bool,
) -> tuple[str, str]:
    if hq_only:
        return (
            SCARCE,
            "RESTRICTED_CRAFT_HQ_OUTPUT",
        )

    if can_equip:
        return (
            SCARCE,
            "RESTRICTED_CRAFT_EQUIPMENT",
        )

    if rare:
        return (
            SCARCE,
            "RESTRICTED_CRAFT_RARE_ITEM",
        )

    if stack_size in {
        12,
        99,
    }:
        return (
            STAPLE,
            "RESTRICTED_CRAFT_STACKABLE_COMMODITY",
        )

    return (
        NORMAL,
        "RESTRICTED_CRAFT_NONSTACKABLE_GOOD",
    )


def main() -> int:
    GENERATED.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not DETAIL_FILE.exists():
        raise RestrictedCraftPolicyError(
            f"Missing restricted craft detail: {DETAIL_FILE}"
        )

    detail = pd.read_csv(
        DETAIL_FILE,
        low_memory=False,
    )

    required = {
        "itemid",
        "name",
        "recipe_id",
        "output_role",
        "keyitem_id",
        "keyitem_name",
        "craft_level",
        "pricing_ready",
        "vendor_item",
        "rare",
        "can_equip",
        "stack_size",
        "base_sell",
    }

    missing = sorted(
        required
        - set(detail.columns)
    )

    if missing:
        raise RestrictedCraftPolicyError(
            "restricted-craft-recipe-audit.csv missing columns: "
            + ", ".join(missing)
        )

    encountered_keyitems = set(
        detail[
            "keyitem_name"
        ]
        .dropna()
        .map(clean_text)
    )

    known_keyitems = (
        GUILD_POINT_TECHNIQUES
        | MASTER_TOMES
        | QUEST_RECIPE_KEYITEMS
    )

    unknown_keyitems = sorted(
        encountered_keyitems
        - known_keyitems
    )

    if unknown_keyitems:
        raise RestrictedCraftPolicyError(
            "Unclassified restricted-craft key items: "
            + ", ".join(unknown_keyitems)
        )

    rows: list[dict[str, Any]] = []

    for itemid, group in detail.groupby(
        "itemid",
        sort=True,
    ):
        name = clean_text(
            group[
                "name"
            ].iloc[0]
        )

        keyitem_names = sorted(
            {
                clean_text(value)
                for value
                in group[
                    "keyitem_name"
                ]
                if clean_text(value)
            }
        )

        keyitem_ids = sorted(
            {
                as_int(value)
                for value
                in group[
                    "keyitem_id"
                ]
            }
        )

        craft_levels = [
            as_int(value)
            for value
            in group[
                "craft_level"
            ]
            if as_int(value) > 0
        ]

        min_craft_level = (
            min(craft_levels)
            if craft_levels
            else 0
        )

        max_craft_level = (
            max(craft_levels)
            if craft_levels
            else 0
        )

        output_roles = {
            clean_text(value)
            for value
            in group[
                "output_role"
            ]
            if clean_text(value)
        }

        has_nq_recipe = (
            "NQ"
            in output_roles
        )

        hq_only = (
            not has_nq_recipe
            and bool(
                output_roles
            )
        )

        can_equip = bool(
            group[
                "can_equip"
            ].fillna(0).astype(int).max()
        )

        rare = bool(
            group[
                "rare"
            ].fillna(0).astype(int).max()
        )

        stack_size = as_int(
            group[
                "stack_size"
            ].iloc[0]
        )

        pricing_ready = bool(
            group[
                "pricing_ready"
            ].fillna(0).astype(int).max()
        )

        vendor_item = bool(
            group[
                "vendor_item"
            ].fillna(0).astype(int).max()
        )

        base_sell = as_int(
            group[
                "base_sell"
            ].iloc[0]
        )

        has_quest_recipe = any(
            keyitem
            in QUEST_RECIPE_KEYITEMS
            for keyitem
            in keyitem_names
        )

        has_master_tome = any(
            keyitem
            in MASTER_TOMES
            for keyitem
            in keyitem_names
        )

        has_gp_technique = any(
            keyitem
            in GUILD_POINT_TECHNIQUES
            for keyitem
            in keyitem_names
        )

        if has_quest_recipe:
            recommended_class = PROTECTED
            policy_reason = "QUEST_RECIPE_KEEP_PROTECTED"
            minimum_maturity = ""
            manual_approval_required = 1
            synthetic_supply_eligible = 0

        else:
            (
                recommended_class,
                base_reason,
            ) = classify_base_item(
                can_equip=can_equip,
                rare=rare,
                stack_size=stack_size,
                hq_only=hq_only,
            )

            recipe_stage = stage_for_skill(
                min_craft_level
            )

            if has_master_tome:
                minimum_maturity = "FULL"
                policy_reason = (
                    "ESCUTCHEON_MASTER_RECIPE_"
                    + base_reason
                )
                manual_approval_required = 1

            elif has_gp_technique:
                # Guild Point techniques require at least Novice-rank
                # crafter participation, so GROWING is the earliest stage.
                minimum_maturity = max_stage(
                    recipe_stage,
                    "GROWING",
                )

                # HQ-only outputs need one additional maturity step to
                # represent enough population/skill surplus to create HQs
                # with meaningful regularity.
                if hq_only:
                    minimum_maturity = later_stage(
                        minimum_maturity
                    )

                policy_reason = (
                    "GUILD_POINT_TECHNIQUE_"
                    + base_reason
                )
                manual_approval_required = 0

            else:
                raise RestrictedCraftPolicyError(
                    f"Item {itemid} has no recognized policy family."
                )

            synthetic_supply_eligible = 1

        # Anti-arbitrage rule. Vendor-associated goods may be seller
        # candidates later, but should never become AHBot demand.
        buyer_eligible = (
            int(
                synthetic_supply_eligible
                and not vendor_item
            )
        )

        rows.append(
            {
                "itemid":
                    int(itemid),

                "name":
                    name,

                "keyitem_ids":
                    "|".join(
                        str(value)
                        for value
                        in keyitem_ids
                    ),

                "keyitem_names":
                    "|".join(
                        keyitem_names
                    ),

                "policy_family":
                    (
                        "QUEST_RECIPE"
                        if has_quest_recipe
                        else (
                            "ESCUTCHEON_MASTER_TOME"
                            if has_master_tome
                            else
                            "GUILD_POINT_TECHNIQUE"
                        )
                    ),

                "recommended_class":
                    recommended_class,

                "policy_reason":
                    policy_reason,

                "minimum_maturity":
                    minimum_maturity,

                "synthetic_supply_eligible":
                    int(
                        synthetic_supply_eligible
                    ),

                "buyer_eligible":
                    buyer_eligible,

                "manual_approval_required":
                    int(
                        manual_approval_required
                    ),

                "pricing_ready":
                    int(
                        pricing_ready
                    ),

                "vendor_item":
                    int(
                        vendor_item
                    ),

                "rare":
                    int(
                        rare
                    ),

                "can_equip":
                    int(
                        can_equip
                    ),

                "stack_size":
                    stack_size,

                "base_sell":
                    base_sell,

                "has_nq_recipe":
                    int(
                        has_nq_recipe
                    ),

                "hq_only":
                    int(
                        hq_only
                    ),

                "min_craft_level":
                    min_craft_level,

                "max_craft_level":
                    max_craft_level,

                # This phase remains analysis-only.
                "auto_live_promotion":
                    0,
            }
        )

    output = pd.DataFrame(
        rows
    )

    if len(output) != 314:
        raise RestrictedCraftPolicyError(
            "Expected 314 restricted-craft items, "
            f"got {len(output)}."
        )

    if output[
        "itemid"
    ].duplicated().any():
        raise RestrictedCraftPolicyError(
            "Restricted craft policy contains duplicate item IDs."
        )

    if int(
        output[
            "auto_live_promotion"
        ].sum()
    ) != 0:
        raise RestrictedCraftPolicyError(
            "Restricted craft policy attempted live promotion."
        )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    family_counts = (
        output[
            "policy_family"
        ]
        .value_counts()
        .to_dict()
    )

    class_counts = (
        output[
            "recommended_class"
        ]
        .value_counts()
        .to_dict()
    )

    maturity_counts = (
        output[
            "minimum_maturity"
        ]
        .replace(
            "",
            "NONE",
        )
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

        "policy_families":
            {
                str(key):
                    int(value)
                for key, value
                in family_counts.items()
            },

        "recommended_classes":
            {
                str(key):
                    int(value)
                for key, value
                in class_counts.items()
            },

        "minimum_maturity":
            {
                str(key):
                    int(value)
                for key, value
                in maturity_counts.items()
            },

        "synthetic_supply_eligible":
            int(
                output[
                    "synthetic_supply_eligible"
                ].sum()
            ),

        "buyer_eligible":
            int(
                output[
                    "buyer_eligible"
                ].sum()
            ),

        "manual_approval_required":
            int(
                output[
                    "manual_approval_required"
                ].sum()
            ),

        "vendor_items":
            int(
                output[
                    "vendor_item"
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
        " Restricted Craft Policy"
    )
    print(
        "======================================"
    )

    print(
        f"Total restricted items:      "
        f"{summary['total_items']:>6}"
    )

    print(
        f"Synthetic-supply eligible:   "
        f"{summary['synthetic_supply_eligible']:>6}"
    )

    print(
        f"Buyer eligible:               "
        f"{summary['buyer_eligible']:>6}"
    )

    print(
        f"Manual approval required:     "
        f"{summary['manual_approval_required']:>6}"
    )

    print()
    print(
        "Policy families:"
    )

    for key, value in sorted(
        summary[
            "policy_families"
        ].items()
    ):
        print(
            f"  {key:<28} {value:>6}"
        )

    print()
    print(
        "Recommended classes:"
    )

    for key in [
        PROTECTED,
        SCARCE,
        NORMAL,
        STAPLE,
    ]:
        print(
            f"  {key:<14} "
            f"{summary['recommended_classes'].get(key, 0):>6}"
        )

    print()
    print(
        "Minimum maturity:"
    )

    for key in [
        "GROWING",
        "ESTABLISHED",
        "MATURE",
        "ADVANCED",
        "FULL",
        "NONE",
    ]:
        print(
            f"  {key:<14} "
            f"{summary['minimum_maturity'].get(key, 0):>6}"
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
