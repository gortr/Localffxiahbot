from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
import pymysql
import yaml

ROOT = Path.home() / "ffxiahbot"
CONFIG_FILE = ROOT / "bin" / "config.yaml"
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"
VENDOR_FILE = GENERATED / "vendor-items.csv"
OUTPUT_FILE = GENERATED / "item-provenance.csv"
MOB_REPORT_FILE = REPORTS / "mob-drop-sources.csv"
SUMMARY_FILE = REPORTS / "provenance-summary.json"

FLAG_GM_ONLY = 0x00000002
FLAG_NO_AUCTION = 0x00000040
FLAG_SCROLL = 0x00000080
FLAG_CAN_EQUIP = 0x00000800
FLAG_NO_SALE = 0x00001000
FLAG_EXCLUSIVE = 0x00004000
FLAG_RARE = 0x00008000

CRAFT_SKILLS = ["Wood", "Smith", "Gold", "Cloth", "Leather", "Bone", "Alchemy", "Cook"]
INGREDIENTS = [f"Ingredient{i}" for i in range(1, 9)]
HQ_RESULTS = ["ResultHQ1", "ResultHQ2", "ResultHQ3"]


class ProvenanceBuildError(RuntimeError):
    pass


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def pipe(values) -> str:
    return "|".join(sorted({clean(v) for v in values if clean(v)}))


def pipe_int(values) -> str:
    return "|".join(str(v) for v in sorted({int(v) for v in values if pd.notna(v)}))


def load_db_config() -> dict[str, Any]:
    data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ProvenanceBuildError(f"Invalid YAML mapping in {CONFIG_FILE}")

    source = data.get("db") if isinstance(data.get("db"), dict) else data

    def req(*keys: str):
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return value
        raise ProvenanceBuildError("Missing DB config key: " + "/".join(keys))

    return {
        "host": req("hostname", "host"),
        "database": req("database", "dbname"),
        "user": req("username", "user"),
        "password": req("password"),
        "port": int(source.get("port", 3306)),
    }


def load_tables() -> dict[str, pd.DataFrame]:
    cfg = load_db_config()
    conn = pymysql.connect(
        host=str(cfg["host"]),
        user=str(cfg["user"]),
        password=str(cfg["password"]),
        database=str(cfg["database"]),
        port=int(cfg["port"]),
        charset="utf8mb4",
    )

    queries = {
        "item_basic": "SELECT itemid,name,type,stackSize,flags,aH,BaseSell FROM item_basic",
        "item_equipment": "SELECT itemId,level,ilevel,su_level FROM item_equipment",
        "item_weapon": "SELECT itemId FROM item_weapon",
        "item_usable": "SELECT itemid FROM item_usable",
        "item_furnishing": "SELECT itemid FROM item_furnishing",
        "synth_recipes": """
            SELECT ID,Desynth,KeyItem,Wood,Smith,Gold,Cloth,Leather,Bone,Alchemy,Cook,
                   Ingredient1,Ingredient2,Ingredient3,Ingredient4,Ingredient5,Ingredient6,Ingredient7,Ingredient8,
                   Result,ResultHQ1,ResultHQ2,ResultHQ3,content_tag
            FROM synth_recipes
        """,
        "mob_droplist": "SELECT dropId,dropType,groupId,groupRate,itemId,itemRate FROM mob_droplist",
        "mob_groups": "SELECT groupid,poolid,zoneid,name,respawntime,spawntype,dropid,content_tag FROM mob_groups",
        "fishing_fish": """
            SELECT fishid,skill_level,rarity,legendary,legendary_flags,required_keyitem,quest_only,contest,disabled
            FROM fishing_fish
        """,
    }

    try:
        return {name: pd.read_sql_query(query, conn) for name, query in queries.items()}
    finally:
        conn.close()


def load_vendor_ids() -> set[int]:
    vendor = pd.read_csv(VENDOR_FILE)
    if "itemid" not in vendor.columns:
        raise ProvenanceBuildError("vendor-items.csv missing itemid")
    return set(vendor["itemid"].astype(int))


def build_craft(recipes: pd.DataFrame) -> dict[int, dict[str, Any]]:
    def blank():
        return {
            "craft_input": False,
            "craft_nq_output": False,
            "craft_hq_output": False,
            "desynth_input": False,
            "desynth_output": False,
            "restricted_craft_input": False,
            "restricted_nq_output": False,
            "restricted_hq_output": False,
            "recipe_ids": set(),
            "content_tags": set(),
            "min_skill": None,
            "max_skill": 0,
        }

    out: dict[int, dict[str, Any]] = defaultdict(blank)

    def touch(itemid: int, row: pd.Series, flag: str, min_skill: int, max_skill: int):
        info = out[itemid]
        info[flag] = True
        info["recipe_ids"].add(int(row["ID"]))
        tag = clean(row.get("content_tag"))
        if tag:
            info["content_tags"].add(tag)
        info["max_skill"] = max(info["max_skill"], max_skill)
        if min_skill > 0:
            info["min_skill"] = min_skill if info["min_skill"] is None else min(info["min_skill"], min_skill)

    for _, row in recipes.iterrows():
        skills = [int(row[c]) for c in CRAFT_SKILLS if pd.notna(row[c])]
        positive = [x for x in skills if x > 0]
        min_skill = min(positive, default=0)
        max_skill = max(skills, default=0)
        desynth = int(row["Desynth"]) != 0
        restricted = int(row["KeyItem"]) != 0

        for col in INGREDIENTS:
            if pd.notna(row[col]) and int(row[col]) > 0:
                flag = "desynth_input" if desynth else ("restricted_craft_input" if restricted else "craft_input")
                touch(int(row[col]), row, flag, min_skill, max_skill)

        if pd.notna(row["Result"]) and int(row["Result"]) > 0:
            flag = "desynth_output" if desynth else ("restricted_nq_output" if restricted else "craft_nq_output")
            touch(int(row["Result"]), row, flag, min_skill, max_skill)

        for col in HQ_RESULTS:
            if pd.notna(row[col]) and int(row[col]) > 0:
                flag = "desynth_output" if desynth else ("restricted_hq_output" if restricted else "craft_hq_output")
                touch(int(row[col]), row, flag, min_skill, max_skill)

    return out


def build_fishing(fishing: pd.DataFrame) -> dict[int, dict[str, Any]]:
    return {
        int(row["fishid"]): {
            "skill": int(row["skill_level"]),
            "rarity": int(row["rarity"]),
            "legendary": int(row["legendary"]),
            "legendary_flags": int(row["legendary_flags"]),
            "required_keyitem": int(row["required_keyitem"]),
            "quest_only": int(row["quest_only"]),
            "contest": int(row["contest"]),
            "disabled": int(row["disabled"]),
        }
        for _, row in fishing.iterrows()
    }


def build_mobs(drops: pd.DataFrame, groups: pd.DataFrame):
    joined = drops.merge(
        groups[["groupid", "poolid", "zoneid", "name", "respawntime", "spawntype", "dropid", "content_tag"]],
        left_on="dropId",
        right_on="dropid",
        how="left",
    )

    def blank():
        return {
            "drop_ids": set(),
            "drop_types": set(),
            "group_ids": set(),
            "pool_ids": set(),
            "zone_ids": set(),
            "spawn_types": set(),
            "content_tags": set(),
            "respawns": [],
            "item_rates": [],
            "group_rates": [],
        }

    out: dict[int, dict[str, Any]] = defaultdict(blank)
    report_rows = []

    for _, row in joined.iterrows():
        itemid = int(row["itemId"])
        info = out[itemid]
        info["drop_ids"].add(int(row["dropId"]))
        info["drop_types"].add(int(row["dropType"]))
        info["item_rates"].append(int(row["itemRate"]))
        info["group_rates"].append(int(row["groupRate"]))

        def maybe_int(key: str):
            return None if pd.isna(row.get(key)) else int(row[key])

        groupid = maybe_int("groupid")
        poolid = maybe_int("poolid")
        zoneid = maybe_int("zoneid")
        respawn = maybe_int("respawntime")
        spawntype = maybe_int("spawntype")
        tag = clean(row.get("content_tag"))

        if groupid is not None:
            info["group_ids"].add(groupid)
        if poolid is not None:
            info["pool_ids"].add(poolid)
        if zoneid is not None:
            info["zone_ids"].add(zoneid)
        if respawn is not None:
            info["respawns"].append(respawn)
        if spawntype is not None:
            info["spawn_types"].add(spawntype)
        if tag:
            info["content_tags"].add(tag)

        report_rows.append({
            "itemid": itemid,
            "drop_id": int(row["dropId"]),
            "drop_type": int(row["dropType"]),
            "drop_group_id": int(row["groupId"]),
            "group_rate": int(row["groupRate"]),
            "item_rate": int(row["itemRate"]),
            "mob_group_id": groupid,
            "mob_pool_id": poolid,
            "zone_id": zoneid,
            "mob_name": clean(row.get("name")),
            "respawn_time": respawn,
            "spawn_type": spawntype,
            "content_tag": tag,
        })

    report = pd.DataFrame(report_rows, columns=[
        "itemid", "drop_id", "drop_type", "drop_group_id", "group_rate", "item_rate",
        "mob_group_id", "mob_pool_id", "zone_id", "mob_name", "respawn_time", "spawn_type", "content_tag",
    ])
    return out, report


def main() -> int:
    GENERATED.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    tables = load_tables()
    items = tables["item_basic"].copy()
    if items["itemid"].duplicated().any():
        raise ProvenanceBuildError("item_basic has duplicate item IDs")

    vendor_ids = load_vendor_ids()
    equipment = tables["item_equipment"].set_index("itemId")
    weapon_ids = set(tables["item_weapon"]["itemId"].astype(int))
    usable_ids = set(tables["item_usable"]["itemid"].astype(int))
    furnishing_ids = set(tables["item_furnishing"]["itemid"].astype(int))
    craft = build_craft(tables["synth_recipes"])
    fishing = build_fishing(tables["fishing_fish"])
    mobs, mob_report = build_mobs(tables["mob_droplist"], tables["mob_groups"])

    rows = []
    for _, item in items.iterrows():
        itemid = int(item["itemid"])
        flags = int(item["flags"])
        c = craft.get(itemid)
        f = fishing.get(itemid)
        m = mobs.get(itemid)
        vendor = itemid in vendor_ids

        tags = []
        if vendor:
            tags.append("NPC_VENDOR")
        if c:
            for key, tag in [
                ("craft_input", "CRAFT_INPUT"),
                ("craft_nq_output", "CRAFT_NQ_OUTPUT"),
                ("craft_hq_output", "CRAFT_HQ_OUTPUT"),
                ("desynth_input", "DESYNTH_INPUT"),
                ("desynth_output", "DESYNTH_OUTPUT"),
                ("restricted_craft_input", "RESTRICTED_CRAFT_INPUT"),
                ("restricted_nq_output", "RESTRICTED_NQ_OUTPUT"),
                ("restricted_hq_output", "RESTRICTED_HQ_OUTPUT"),
            ]:
                if c[key]:
                    tags.append(tag)
        if f:
            tags.append("FISHING")
            if f["legendary"]:
                tags.append("LEGENDARY_FISH")
            if f["required_keyitem"] > 0:
                tags.append("KEYITEM_GATED_FISH")
            if f["quest_only"]:
                tags.append("QUEST_ONLY_FISH")
            if f["disabled"]:
                tags.append("DISABLED_FISH")
        if m:
            tags.append("MOB_DROP")

        tags = sorted(set(tags))
        is_equipment = itemid in equipment.index
        eq = equipment.loc[itemid] if is_equipment else None

        rows.append({
            "itemid": itemid,
            "name": item["name"],
            "item_type": int(item["type"]),
            "stack_size": int(item["stackSize"]),
            "flags": flags,
            "ah_category": int(item["aH"]),
            "base_sell": int(item["BaseSell"]),
            "gm_only": int(bool(flags & FLAG_GM_ONLY)),
            "no_auction": int(bool(flags & FLAG_NO_AUCTION)),
            "scroll": int(bool(flags & FLAG_SCROLL)),
            "can_equip": int(bool(flags & FLAG_CAN_EQUIP)),
            "no_sale": int(bool(flags & FLAG_NO_SALE)),
            "exclusive": int(bool(flags & FLAG_EXCLUSIVE)),
            "rare": int(bool(flags & FLAG_RARE)),
            "is_equipment": int(is_equipment),
            "is_weapon": int(itemid in weapon_ids),
            "is_usable": int(itemid in usable_ids),
            "is_furnishing": int(itemid in furnishing_ids),
            "equipment_level": int(eq["level"]) if is_equipment else None,
            "equipment_ilevel": int(eq["ilevel"]) if is_equipment else None,
            "equipment_su_level": int(eq["su_level"]) if is_equipment else None,
            "vendor_item": int(vendor),
            "craft_input": int(bool(c and c["craft_input"])),
            "craft_nq_output": int(bool(c and c["craft_nq_output"])),
            "craft_hq_output": int(bool(c and c["craft_hq_output"])),
            "desynth_input": int(bool(c and c["desynth_input"])),
            "desynth_output": int(bool(c and c["desynth_output"])),
            "restricted_craft_input": int(bool(c and c["restricted_craft_input"])),
            "restricted_nq_output": int(bool(c and c["restricted_nq_output"])),
            "restricted_hq_output": int(bool(c and c["restricted_hq_output"])),
            "craft_recipe_count": len(c["recipe_ids"]) if c else 0,
            "craft_min_skill": c["min_skill"] if c else None,
            "craft_max_skill": c["max_skill"] if c else None,
            "craft_content_tags": pipe(c["content_tags"]) if c else "",
            "fishing": int(f is not None),
            "fishing_skill_level": f["skill"] if f else None,
            "fishing_rarity": f["rarity"] if f else None,
            "fishing_legendary": f["legendary"] if f else 0,
            "fishing_required_keyitem": f["required_keyitem"] if f else 0,
            "fishing_quest_only": f["quest_only"] if f else 0,
            "fishing_contest": f["contest"] if f else 0,
            "fishing_disabled": f["disabled"] if f else 0,
            "mob_drop": int(m is not None),
            "mob_drop_list_count": len(m["drop_ids"]) if m else 0,
            "mob_group_count": len(m["group_ids"]) if m else 0,
            "mob_pool_count": len(m["pool_ids"]) if m else 0,
            "mob_zone_count": len(m["zone_ids"]) if m else 0,
            "mob_drop_types": pipe_int(m["drop_types"]) if m else "",
            "mob_spawn_types": pipe_int(m["spawn_types"]) if m else "",
            "mob_respawn_min": min(m["respawns"]) if m and m["respawns"] else None,
            "mob_respawn_max": max(m["respawns"]) if m and m["respawns"] else None,
            "mob_item_rate_min": min(m["item_rates"]) if m and m["item_rates"] else None,
            "mob_item_rate_max": max(m["item_rates"]) if m and m["item_rates"] else None,
            "mob_group_rate_min": min(m["group_rates"]) if m and m["group_rates"] else None,
            "mob_group_rate_max": max(m["group_rates"]) if m and m["group_rates"] else None,
            "mob_content_tags": pipe(m["content_tags"]) if m else "",
            "provenance_tags": "|".join(tags),
            "provenance_confidence": "HIGH" if tags else "UNKNOWN",
            "provenance_known": int(bool(tags)),
        })

    provenance = pd.DataFrame(rows)
    if len(provenance) != len(items):
        raise ProvenanceBuildError("provenance row count mismatch")
    if provenance["itemid"].duplicated().any():
        raise ProvenanceBuildError("duplicate item IDs in provenance output")

    provenance.to_csv(OUTPUT_FILE, index=False)
    mob_report.to_csv(MOB_REPORT_FILE, index=False)

    exploded = provenance["provenance_tags"].fillna("").str.split("|").explode()
    exploded = exploded[exploded != ""]
    tag_counts = exploded.value_counts().to_dict()

    summary = {
        "status": "PASS",
        "total_items": int(len(provenance)),
        "known_provenance": int(provenance["provenance_known"].sum()),
        "unknown_provenance": int((provenance["provenance_known"] == 0).sum()),
        "vendor_items": int(provenance["vendor_item"].sum()),
        "craft_related_items": int((provenance[[
            "craft_input", "craft_nq_output", "craft_hq_output", "desynth_input", "desynth_output",
            "restricted_craft_input", "restricted_nq_output", "restricted_hq_output"
        ]].sum(axis=1) > 0).sum()),
        "fishing_items": int(provenance["fishing"].sum()),
        "mob_drop_items": int(provenance["mob_drop"].sum()),
        "mob_source_rows": int(len(mob_report)),
        "tag_counts": {str(k): int(v) for k, v in tag_counts.items()},
    }

    SUMMARY_FILE.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("\n======================================")
    print(" LSB Item Provenance Index")
    print("======================================")
    print(f"Total items:          {summary['total_items']:>6}")
    print(f"Known provenance:     {summary['known_provenance']:>6}")
    print(f"Unknown provenance:   {summary['unknown_provenance']:>6}")
    print(f"Vendor items:         {summary['vendor_items']:>6}")
    print(f"Craft-related items:  {summary['craft_related_items']:>6}")
    print(f"Fishing items:        {summary['fishing_items']:>6}")
    print(f"Mob-drop items:       {summary['mob_drop_items']:>6}")
    print(f"Mob source rows:      {summary['mob_source_rows']:>6}")
    print("\nTop provenance tags:")
    for tag, count in list(summary["tag_counts"].items())[:25]:
        print(f"  {tag:<28} {count:>6}")
    print(f"\nGenerated: {OUTPUT_FILE}")
    print(f"Mob report: {MOB_REPORT_FILE}")
    print(f"Summary: {SUMMARY_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
