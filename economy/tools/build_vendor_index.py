from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path


LSB_ROOT = Path.home() / "server"
ITEM_ENUM = LSB_ROOT / "scripts" / "enum" / "item.lua"
SCRIPT_ROOT = LSB_ROOT / "scripts"

OUTPUT = (
    Path.home()
    / "ffxiahbot"
    / "economy"
    / "generated"
    / "vendor-items.csv"
)

OUTPUT.parent.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------
# Load xi.item CONSTANT -> item ID mapping
# ------------------------------------------------------------

enum_text = ITEM_ENUM.read_text(
    encoding="utf-8",
    errors="ignore",
)

enum_pattern = re.compile(
    r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(\d+),",
    re.MULTILINE,
)

item_ids = {
    name: int(itemid)
    for name, itemid in enum_pattern.findall(enum_text)
}


# ------------------------------------------------------------
# Find Lua files that actually contain shop code
# ------------------------------------------------------------

shop_markers = (
    "xi.shop.",
    "addShopItem(",
    "createShop(",
)

item_reference_pattern = re.compile(
    r"xi\.item\.([A-Z][A-Z0-9_]*)"
)

sources: dict[int, set[str]] = defaultdict(set)
constants: dict[int, set[str]] = defaultdict(set)

shop_files = 0
unresolved = set()

for path in SCRIPT_ROOT.rglob("*.lua"):
    text = path.read_text(
        encoding="utf-8",
        errors="ignore",
    )

    if not any(marker in text for marker in shop_markers):
        continue

    shop_files += 1

    for constant in set(
        item_reference_pattern.findall(text)
    ):
        itemid = item_ids.get(constant)

        if itemid is None:
            unresolved.add(constant)
            continue

        sources[itemid].add(
            str(path.relative_to(LSB_ROOT))
        )

        constants[itemid].add(constant)


# ------------------------------------------------------------
# Write conservative vendor index
# ------------------------------------------------------------

with OUTPUT.open(
    "w",
    newline="",
    encoding="utf-8",
) as handle:
    writer = csv.writer(handle)

    writer.writerow(
        [
            "itemid",
            "constants",
            "source_count",
            "sources",
        ]
    )

    for itemid in sorted(sources):
        writer.writerow(
            [
                itemid,
                "|".join(sorted(constants[itemid])),
                len(sources[itemid]),
                "|".join(sorted(sources[itemid])),
            ]
        )


print()
print("======================================")
print(" LSB NPC Vendor Index")
print("======================================")
print(f"Item constants loaded: {len(item_ids):>6}")
print(f"Shop-related Lua files: {shop_files:>6}")
print(f"Vendor item IDs found:  {len(sources):>6}")
print(f"Unresolved constants:   {len(unresolved):>6}")
print()
print(f"Generated: {OUTPUT}")

if unresolved:
    print()
    print("Unresolved item constants:")
    for name in sorted(unresolved)[:30]:
        print(f"  {name}")
