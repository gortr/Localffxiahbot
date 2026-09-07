from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from ffxiahbot.config import Config
from ffxiahbot.database import Database


# ============================================================
# Paths
# ============================================================

ROOT = Path.home() / "ffxiahbot"

CONFIG = ROOT / "bin" / "config.yaml"
UPSTREAM = ROOT / "bin" / "items.upstream.csv"

ECONOMY = ROOT / "economy"
GENERATED = ECONOMY / "generated"
REPORTS = ECONOMY / "reports"
OVERRIDES = ECONOMY / "overrides"

VENDOR_INDEX = GENERATED / "vendor-items.csv"
OVERRIDE_FILE = OVERRIDES / "market-overrides.csv"

GENERATED.mkdir(parents=True, exist_ok=True)
REPORTS.mkdir(parents=True, exist_ok=True)
OVERRIDES.mkdir(parents=True, exist_ok=True)


# ============================================================
# Market phase
# ============================================================

PHASE_NAME = "SEED"

MAX_SYNTH_SKILL = 20
MAX_FISHING_SKILL = 20

# Input-only materials are harder to classify safely because
# being used by a low-level recipe does not necessarily mean
# the material itself is a low-level/common acquisition.
#
# For Seed, keep those relatively inexpensive.
INPUT_ONLY_BASESELL_MAX = 1000


# ============================================================
# LSB item flags
# ============================================================

GM_ONLY = 0x00000002
NO_AUCTION = 0x00000040
SCROLL = 0x00000080
CAN_EQUIP = 0x00000800
NO_SALE = 0x00001000
EXCLUSIVE = 0x00004000
RARE = 0x00008000


# ============================================================
# Elemental crystals
# ============================================================

# These are deliberately treated as fundamental AH staples.
#
# Our vendor scanner is intentionally conservative and may
# identify them through guild/shop-related Lua files. That
# should not prevent normal elemental crystals from appearing
# in the simulated player market.

CRYSTAL_NAMES = {
    "fire_crystal",
    "ice_crystal",
    "wind_crystal",
    "earth_crystal",
    "lightning_crystal",
    "water_crystal",
    "light_crystal",
    "dark_crystal",
}


# ============================================================
# Helpers
# ============================================================

def secret_value(value):
    if hasattr(value, "get_secret_value"):
        return value.get_secret_value()

    return value


def store_min(
    mapping: dict[int, int],
    itemid: int,
    skill: int,
):
    """
    Store the lowest known applicable skill level for an item.
    """

    if itemid <= 0:
        return

    if itemid not in mapping:
        mapping[itemid] = skill
    else:
        mapping[itemid] = min(
            mapping[itemid],
            skill,
        )


# ============================================================
# Database connection
# ============================================================

cfg = Config.from_yaml(CONFIG)

db = Database.pymysql(
    hostname=cfg.hostname,
    database=cfg.database,
    username=cfg.username,
    password=secret_value(cfg.password),
    port=cfg.port,
)


# ============================================================
# Read authoritative LSB data
# ============================================================

with db.engine.connect() as conn:
    items = pd.read_sql(
        text(
            """
            SELECT
                itemid,
                name,
                stackSize,
                flags,
                aH,
                BaseSell
            FROM item_basic
            WHERE itemid > 0
            """
        ),
        conn,
    )

    synth = pd.read_sql(
        text(
            """
            SELECT
                KeyItem,
                Wood,
                Smith,
                Gold,
                Cloth,
                Leather,
                Bone,
                Alchemy,
                Cook,
                Crystal,
                HQCrystal,
                Ingredient1,
                Ingredient2,
                Ingredient3,
                Ingredient4,
                Ingredient5,
                Ingredient6,
                Ingredient7,
                Ingredient8,
                `Result`,
                ResultHQ1,
                ResultHQ2,
                ResultHQ3,
                content_tag
            FROM synth_recipes
            WHERE Desynth = 0
            """
        ),
        conn,
    )

    fishing = pd.read_sql(
        text(
            """
            SELECT
                fishid,
                skill_level,
                legendary,
                quest_only,
                required_keyitem,
                disabled
            FROM fishing_fish
            WHERE disabled = 0
            """
        ),
        conn,
    )


# ============================================================
# Vendor index
# ============================================================

vendor_ids: set[int] = set()

if VENDOR_INDEX.exists():
    vendor_df = pd.read_csv(VENDOR_INDEX)

    vendor_ids = {
        int(value)
        for value in vendor_df["itemid"].tolist()
    }


# ============================================================
# Manual BLOCK overrides
# ============================================================

blocked_overrides: dict[int, str] = {}

if OVERRIDE_FILE.exists():
    overrides_df = pd.read_csv(OVERRIDE_FILE)

    for _, row in overrides_df.iterrows():
        action = str(
            row.get("action", "")
        ).strip().upper()

        if action != "BLOCK":
            continue

        itemid = int(row["itemid"])

        reason = str(
            row.get("reason", "")
        ).strip()

        blocked_overrides[itemid] = reason


# ============================================================
# Synthesis provenance
# ============================================================

skill_cols = [
    "Wood",
    "Smith",
    "Gold",
    "Cloth",
    "Leather",
    "Bone",
    "Alchemy",
    "Cook",
]

ingredient_cols = [
    "Crystal",
    "HQCrystal",
    "Ingredient1",
    "Ingredient2",
    "Ingredient3",
    "Ingredient4",
    "Ingredient5",
    "Ingredient6",
    "Ingredient7",
    "Ingredient8",
]

hq_cols = [
    "ResultHQ1",
    "ResultHQ2",
    "ResultHQ3",
]


# ------------------------------------------------------------
# Normal unrestricted recipe information
# ------------------------------------------------------------

min_input_skill: dict[int, int] = {}
min_nq_skill: dict[int, int] = {}
min_hq_skill: dict[int, int] = {}

craft_inputs: set[int] = set()
nq_outputs: set[int] = set()
hq_outputs: set[int] = set()


# ------------------------------------------------------------
# Restricted synthesis-key-item recipe information
# ------------------------------------------------------------

restricted_inputs: set[int] = set()
restricted_nq_outputs: set[int] = set()
restricted_hq_outputs: set[int] = set()


# ============================================================
# Parse synthesis recipes
# ============================================================

for _, recipe in synth.iterrows():
    recipe_skill = max(
        int(recipe[col])
        for col in skill_cols
    )

    key_item = int(recipe["KeyItem"])

    # --------------------------------------------------------
    # Key-item-required synthesis
    #
    # These recipes represent special synthesis knowledge or
    # progression. Record them for auditing but do NOT allow
    # them to establish normal Seed-market provenance.
    # --------------------------------------------------------

    if key_item != 0:
        for col in ingredient_cols:
            itemid = int(recipe[col])

            if itemid > 0:
                restricted_inputs.add(itemid)

        nq_itemid = int(recipe["Result"])

        if nq_itemid > 0:
            restricted_nq_outputs.add(
                nq_itemid
            )

        for col in hq_cols:
            itemid = int(recipe[col])

            if itemid > 0:
                restricted_hq_outputs.add(
                    itemid
                )

        continue

    # --------------------------------------------------------
    # Ordinary synthesis ingredients
    # --------------------------------------------------------

    for col in ingredient_cols:
        itemid = int(recipe[col])

        if itemid <= 0:
            continue

        craft_inputs.add(itemid)

        store_min(
            min_input_skill,
            itemid,
            recipe_skill,
        )

    # --------------------------------------------------------
    # Ordinary NQ synthesis result
    # --------------------------------------------------------

    nq_itemid = int(recipe["Result"])

    if nq_itemid > 0:
        nq_outputs.add(nq_itemid)

        store_min(
            min_nq_skill,
            nq_itemid,
            recipe_skill,
        )

    # --------------------------------------------------------
    # Ordinary HQ synthesis results
    # --------------------------------------------------------

    for col in hq_cols:
        itemid = int(recipe[col])

        if itemid <= 0:
            continue

        hq_outputs.add(itemid)

        store_min(
            min_hq_skill,
            itemid,
            recipe_skill,
        )


# ============================================================
# Fishing provenance
# ============================================================

fish_info: dict[int, dict] = {}

for _, row in fishing.iterrows():
    itemid = int(row["fishid"])

    fish_info[itemid] = {
        "skill": int(row["skill_level"]),
        "legendary": bool(row["legendary"]),
        "quest_only": bool(row["quest_only"]),
        "required_keyitem": int(
            row["required_keyitem"]
        ),
    }


# ============================================================
# Upstream FFXIAHBot price anchors
# ============================================================

price_df = pd.read_csv(
    UPSTREAM,
    skipinitialspace=True,
)

price_df.columns = [
    column.strip()
    for column in price_df.columns
]

price_by_id = {
    int(row["itemid"]): row
    for _, row in price_df.iterrows()
}


# ============================================================
# Build economy
# ============================================================

master_rows = []
seller_rows = []
rejected_rows = []


for _, item in items.iterrows():
    itemid = int(item["itemid"])
    name = str(item["name"])

    stack_size = int(item["stackSize"])
    flags = int(item["flags"])
    base_sell = int(item["BaseSell"])

    reasons: list[str] = []
    provenance: list[str] = []

    # ========================================================
    # Item flags
    # ========================================================

    is_gm = bool(
        flags & GM_ONLY
    )

    no_auction = bool(
        flags & NO_AUCTION
    )

    is_scroll = bool(
        flags & SCROLL
    )

    can_equip = bool(
        flags & CAN_EQUIP
    )

    no_sale = bool(
        flags & NO_SALE
    )

    exclusive = bool(
        flags & EXCLUSIVE
    )

    rare = bool(
        flags & RARE
    )

    is_crystal = (
        name in CRYSTAL_NAMES
    )

    # ========================================================
    # Synthesis provenance
    # ========================================================

    input_skill = min_input_skill.get(
        itemid
    )

    nq_skill = min_nq_skill.get(
        itemid
    )

    hq_skill = min_hq_skill.get(
        itemid
    )

    is_craft_input = (
        itemid in craft_inputs
    )

    is_nq_output = (
        itemid in nq_outputs
    )

    is_hq_output = (
        itemid in hq_outputs
    )

    # --------------------------------------------------------
    # Restricted recipe provenance
    # --------------------------------------------------------

    restricted_input = (
        itemid in restricted_inputs
    )

    restricted_nq_output = (
        itemid in restricted_nq_outputs
    )

    restricted_hq_output = (
        itemid in restricted_hq_outputs
    )

    # An item produced by a restricted recipe is only
    # considered "restricted-only" when it has no ordinary
    # unrestricted synthesis recipe producing it.

    keyitem_only_nq_output = (
        restricted_nq_output
        and not is_nq_output
    )

    keyitem_only_hq_output = (
        restricted_hq_output
        and not is_hq_output
    )

    # --------------------------------------------------------
    # Human-readable provenance
    # --------------------------------------------------------

    if is_craft_input:
        provenance.append(
            "CRAFT_INPUT"
        )

    if is_nq_output:
        provenance.append(
            "CRAFT_NQ_OUTPUT"
        )

    if is_hq_output:
        provenance.append(
            "CRAFT_HQ_OUTPUT"
        )

    if restricted_input:
        provenance.append(
            "RESTRICTED_CRAFT_INPUT"
        )

    if restricted_nq_output:
        provenance.append(
            "RESTRICTED_NQ_OUTPUT"
        )

    if restricted_hq_output:
        provenance.append(
            "RESTRICTED_HQ_OUTPUT"
        )

    # ========================================================
    # Fishing provenance
    # ========================================================

    fishing_data = fish_info.get(
        itemid
    )

    fish_skill = None
    ordinary_seed_fish = False

    if fishing_data is not None:
        fish_skill = fishing_data[
            "skill"
        ]

        provenance.append(
            "FISHING"
        )

        ordinary_seed_fish = (
            fish_skill <= MAX_FISHING_SKILL
            and not fishing_data["legendary"]
            and not fishing_data["quest_only"]
            and fishing_data[
                "required_keyitem"
            ] == 0
        )

    # ========================================================
    # Hard exclusions
    # ========================================================

    if is_gm:
        reasons.append(
            "GM_ONLY"
        )

    if no_auction:
        reasons.append(
            "NO_AUCTION"
        )

    if exclusive:
        reasons.append(
            "EXCLUSIVE"
        )

    if rare:
        reasons.append(
            "RARE"
        )

    if is_scroll:
        reasons.append(
            "SCROLL"
        )

    if can_equip:
        reasons.append(
            "CAN_EQUIP"
        )

    if no_sale:
        reasons.append(
            "NO_NPC_SALE"
        )

    if stack_size not in (
        12,
        99,
    ):
        reasons.append(
            "NOT_STACKABLE_12_OR_99"
        )

    if base_sell <= 0:
        reasons.append(
            "NO_BASE_SELL_VALUE"
        )

    # ========================================================
    # Vendor exclusion
    # ========================================================

    is_vendor_item = (
        itemid in vendor_ids
    )

    # Ordinary elemental crystals are explicitly exempt from
    # the vendor exclusion because they are core AH staples.
    if (
        is_vendor_item
        and not is_crystal
    ):
        reasons.append(
            "NPC_VENDOR"
        )

    # ========================================================
    # HQ exclusion
    # ========================================================

    hq_only = (
        is_hq_output
        and not is_nq_output
    )

    if hq_only:
        reasons.append(
            "HQ_ONLY_OUTPUT"
        )

    if name.endswith("_+1"):
        reasons.append(
            "HQ_VARIANT_NAME"
        )

    # ========================================================
    # Special synthesis-key-item exclusions
    # ========================================================

    if keyitem_only_nq_output:
        reasons.append(
            "KEYITEM_ONLY_NQ_OUTPUT"
        )

    if keyitem_only_hq_output:
        reasons.append(
            "KEYITEM_ONLY_HQ_OUTPUT"
        )

    # ========================================================
    # Seed synthesis-source rules
    # ========================================================

    early_nq_output = (
        nq_skill is not None
        and nq_skill <= MAX_SYNTH_SKILL
    )

    early_input = (
        input_skill is not None
        and input_skill <= MAX_SYNTH_SKILL
    )

    # --------------------------------------------------------
    # Input-only materials
    #
    # If an item is merely consumed by a low-level recipe, we
    # do not automatically know whether the source itself is
    # ordinary. Seed therefore uses a conservative value cap.
    # --------------------------------------------------------

    safe_input_only = (
        early_input
        and not is_nq_output
        and base_sell
        <= INPUT_ONLY_BASESELL_MAX
    )

    # --------------------------------------------------------
    # Final Seed provenance
    # --------------------------------------------------------

    seed_source = (
        is_crystal
        or early_nq_output
        or safe_input_only
        or ordinary_seed_fish
    )

    if not seed_source:
        reasons.append(
            "NOT_SEED_SOURCE"
        )

    # ========================================================
    # Fishing special restrictions
    # ========================================================

    if fishing_data is not None:
        if fishing_data[
            "legendary"
        ]:
            reasons.append(
                "LEGENDARY_FISH"
            )

        if fishing_data[
            "quest_only"
        ]:
            reasons.append(
                "QUEST_ONLY_FISH"
            )

        if fishing_data[
            "required_keyitem"
        ] != 0:
            reasons.append(
                "KEYITEM_GATED_FISH"
            )

    # ========================================================
    # Manual override
    # ========================================================

    if itemid in blocked_overrides:
        manual_reason = (
            blocked_overrides[itemid]
        )

        if manual_reason:
            reasons.append(
                "MANUAL_BLOCK:"
                + manual_reason
            )
        else:
            reasons.append(
                "MANUAL_BLOCK"
            )

    # ========================================================
    # Price anchor
    # ========================================================

    upstream = price_by_id.get(
        itemid
    )

    if upstream is None:
        reasons.append(
            "NO_PRICE_ANCHOR"
        )

    # Remove duplicate rejection reasons while preserving
    # their original ordering.
    reasons = list(
        dict.fromkeys(reasons)
    )

    allowed = (
        len(reasons) == 0
    )

    # ========================================================
    # Market class
    # ========================================================

    market_class = "PROTECTED"

    if allowed:
        if is_crystal:
            market_class = (
                "STAPLE_CRYSTAL"
            )

        elif is_craft_input:
            market_class = (
                "STAPLE"
            )

        else:
            market_class = (
                "NORMAL"
            )

    # ========================================================
    # Master audit record
    # ========================================================

    master_row = {
        "itemid": itemid,
        "name": name,

        "phase": PHASE_NAME,

        "stack_size": stack_size,
        "ah_category": int(
            item["aH"]
        ),
        "flags": flags,
        "base_sell": base_sell,

        "rare": rare,
        "scroll": is_scroll,
        "can_equip": can_equip,

        "vendor_item": (
            is_vendor_item
        ),

        "crystal": is_crystal,

        # ----------------------------------------------------
        # Normal synthesis provenance
        # ----------------------------------------------------

        "craft_input": (
            is_craft_input
        ),

        "nq_output": (
            is_nq_output
        ),

        "hq_output": (
            is_hq_output
        ),

        # ----------------------------------------------------
        # Special/key-item synthesis provenance
        # ----------------------------------------------------

        "restricted_input": (
            restricted_input
        ),

        "restricted_nq_output": (
            restricted_nq_output
        ),

        "restricted_hq_output": (
            restricted_hq_output
        ),

        # ----------------------------------------------------
        # Lowest ordinary synthesis levels
        # ----------------------------------------------------

        "input_min_skill": (
            input_skill
        ),

        "nq_min_skill": (
            nq_skill
        ),

        "hq_min_skill": (
            hq_skill
        ),

        # ----------------------------------------------------
        # Fishing
        # ----------------------------------------------------

        "fish_skill": fish_skill,

        # ----------------------------------------------------
        # Classification
        # ----------------------------------------------------

        "provenance": "|".join(
            provenance
        ),

        "market_class": (
            market_class
        ),

        "allowed": allowed,

        "rejection_reason": "|".join(
            reasons
        ),
    }

    master_rows.append(
        master_row
    )

    # Nothing below here is generated for protected items.
    if not allowed:
        rejected_rows.append(
            master_row
        )

        continue

    # ========================================================
    # Seller price calculation
    # ========================================================

    reference_single = max(
        1,
        int(
            upstream[
                "price_single"
            ]
        ),
    )

    reference_stack = max(
        1,
        int(
            upstream[
                "price_stacks"
            ]
        ),
    )

    # --------------------------------------------------------
    # NPC resale protection
    #
    # Never allow the synthetic seller to list items cheaply
    # enough that purchasing from the AH and selling to an NPC
    # becomes an obvious gil generator.
    # --------------------------------------------------------

    npc_floor_single = math.ceil(
        base_sell * 1.50
    )

    ask_single = max(
        reference_single,
        npc_floor_single,
    )

    npc_floor_stack = math.ceil(
        base_sell
        * stack_size
        * 1.50
    )

    # Also ensure a stack isn't bizarrely cheap relative to
    # buying the same number of singles.
    single_equivalent_floor = (
        math.ceil(
            ask_single
            * stack_size
            * 0.90
        )
    )

    ask_stack = max(
        reference_stack,
        npc_floor_stack,
        single_equivalent_floor,
    )

    # ========================================================
    # Seed liquidity
    # ========================================================

    if (
        market_class
        == "STAPLE_CRYSTAL"
    ):
        stock_single = 2
        stock_stacks = 2

        sell_rate_single = 0.60
        sell_rate_stacks = 0.70

    elif market_class == "STAPLE":
        stock_single = 1
        stock_stacks = 1

        sell_rate_single = 0.35
        sell_rate_stacks = 0.45

    else:
        stock_single = 1
        stock_stacks = 1

        sell_rate_single = 0.20
        sell_rate_stacks = 0.25

    # ========================================================
    # FFXIAHBot seller row
    # ========================================================

    seller_rows.append(
        {
            "itemid": itemid,
            "name": name,

            # Seller process only
            "sell_single": 1,
            "buy_single": 0,

            "price_single": (
                ask_single
            ),

            "stock_single": (
                stock_single
            ),

            "buy_rate_single": 0.0,

            "sell_rate_single": (
                sell_rate_single
            ),

            "sell_stacks": 1,
            "buy_stacks": 0,

            "price_stacks": (
                ask_stack
            ),

            "stock_stacks": (
                stock_stacks
            ),

            "buy_rate_stacks": 0.0,

            "sell_rate_stacks": (
                sell_rate_stacks
            ),
        }
    )


# ============================================================
# Output DataFrames
# ============================================================

master = pd.DataFrame(
    master_rows
)

seller = pd.DataFrame(
    seller_rows
)

rejected = pd.DataFrame(
    rejected_rows
)


# ============================================================
# Write files
# ============================================================

master.to_csv(
    GENERATED / "master-market.csv",
    index=False,
)

seller.to_csv(
    GENERATED / "market-sell-phase0.csv",
    index=False,
)

rejected.to_csv(
    REPORTS / "phase0-rejected-v3.csv",
    index=False,
)


# ============================================================
# Summary
# ============================================================

print()
print(
    "======================================"
)
print(
    " Phase 0 / Seed Economy v3"
)
print(
    "======================================"
)

print(
    f"LSB items:               "
    f"{len(items):>6}"
)

print(
    f"Seed seller items:       "
    f"{len(seller):>6}"
)

print(
    f"Protected / rejected:    "
    f"{len(rejected):>6}"
)

print(
    f"Known vendor item IDs:   "
    f"{len(vendor_ids):>6}"
)

print(
    f"Restricted craft inputs: "
    f"{len(restricted_inputs):>6}"
)

print(
    f"Restricted NQ outputs:   "
    f"{len(restricted_nq_outputs):>6}"
)

print(
    f"Restricted HQ outputs:   "
    f"{len(restricted_hq_outputs):>6}"
)

print()


# ============================================================
# Allowed-market summary
# ============================================================

if not seller.empty:
    allowed_master = master[
        master["allowed"]
    ]

    print(
        "Market classes:"
    )

    print(
        allowed_master[
            "market_class"
        ]
        .value_counts()
        .to_string()
    )

    print()

    print(
        "Provenance:"
    )

    print(
        allowed_master[
            "provenance"
        ]
        .value_counts()
        .head(25)
        .to_string()
    )

    print()

    print(
        "Stack sizes:"
    )

    print(
        allowed_master[
            "stack_size"
        ]
        .value_counts()
        .sort_index()
        .to_string()
    )

    print()


# ============================================================
# Rejection summary
# ============================================================

print(
    "Top rejection reasons:"
)

if not rejected.empty:
    exploded = (
        rejected[
            "rejection_reason"
        ]
        .str.split("|")
        .explode()
    )

    print(
        exploded
        .value_counts()
        .head(25)
        .to_string()
    )

print()


# ============================================================
# Crystal diagnostic
# ============================================================

print(
    "Elemental crystal status:"
)

crystal_status = master[
    master["name"].isin(
        CRYSTAL_NAMES
    )
][
    [
        "itemid",
        "name",
        "stack_size",
        "base_sell",
        "vendor_item",
        "craft_input",
        "input_min_skill",
        "market_class",
        "allowed",
        "rejection_reason",
    ]
].sort_values(
    "itemid"
)

if crystal_status.empty:
    print(
        "No elemental crystal rows found."
    )
else:
    print(
        crystal_status.to_string(
            index=False
        )
    )

print()


# ============================================================
# Generated file locations
# ============================================================

print(
    "Generated:"
)

print(
    GENERATED
    / "master-market.csv"
)

print(
    GENERATED
    / "market-sell-phase0.csv"
)

print(
    REPORTS
    / "phase0-rejected-v3.csv"
)
