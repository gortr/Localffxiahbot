from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
LSB_ROOT = Path.home() / "server"

CENSUS_FILE = ROOT / "economy" / "reports" / "unknown-provenance-census.csv"
SCRIPTS_ROOT = LSB_ROOT / "scripts"

OUTPUT_FILE = ROOT / "economy" / "reports" / "unknown-numeric-lua-source-audit.csv"
REFERENCE_FILE = ROOT / "economy" / "reports" / "unknown-numeric-lua-references.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "unknown-numeric-lua-source-audit-summary.json"


NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])(\d{2,5})(?![A-Za-z0-9_])")

REWARD_MARKERS = (
    "addItem",
    "giveItem",
    "npcUtil.giveItem",
    "npcUtil.completeQuest",
    "npcUtil.completeMission",
    "reward",
)

SHOP_MARKERS = (
    "xi.shop.",
    "addShopItem",
    "createShop",
)

TRADE_MARKERS = (
    "trade:hasItemQty",
    "trade:getItemQty",
    "npcUtil.tradeHas",
)

ITEM_CONTEXT_MARKERS = (
    "itemid",
    "itemId",
    "itemID",
    "item =",
    "item=",
    "addItem",
    "giveItem",
    "reward",
    "loot",
    "droprate",
    "trade",
    "shop",
)

IGNORE_PATH_PARTS = (
    "/scripts/commands/",
    "/scripts/specs/",
    "/scripts/enum/",
    "/scripts/tests/",
)


class NumericLuaAuditError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def as_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0
    return int(value)


def classify_path(path: Path) -> str:
    posix = path.as_posix().lower()

    if "/quests/" in posix:
        return "QUEST"
    if "/missions/" in posix:
        return "MISSION"
    if "/battlefields/" in posix:
        return "BATTLEFIELD"
    if "helm" in posix:
        return "HELM"
    if "chocobo" in posix and "dig" in posix:
        return "CHOCOBO_DIGGING"
    if "treasure" in posix or "coffer" in posix or "chest" in posix:
        return "TREASURE_SYSTEM"
    if "/zones/" in posix and "/mobs/" in posix:
        return "MOB_SCRIPT"
    if "/zones/" in posix and "/npcs/" in posix:
        return "NPC_SCRIPT"
    if "/globals/" in posix:
        return "GLOBAL_SCRIPT"
    if "/items/" in posix:
        return "ITEM_SCRIPT"

    return "OTHER"


def classify_context(
    family: str,
    context: str,
) -> tuple[str, str, str]:
    low = context.lower()

    if (
        family == "BATTLEFIELD"
        and "itemid" in low
        and "droprate" in low
    ):
        return (
            "BATTLEFIELD_DROP",
            "HIGH",
            "BATTLEFIELD_ITEMID_DROPRATE_CONTEXT",
        )

    if family == "HELM":
        return (
            "HELM_NUMERIC_REFERENCE",
            "MEDIUM",
            "NUMERIC_REFERENCE_IN_HELM_SYSTEM",
        )

    if family == "CHOCOBO_DIGGING":
        return (
            "CHOCOBO_DIGGING_NUMERIC_REFERENCE",
            "MEDIUM",
            "NUMERIC_REFERENCE_IN_CHOCOBO_DIGGING_SYSTEM",
        )

    if family == "TREASURE_SYSTEM" and (
        "item" in low
        or "loot" in low
        or "reward" in low
    ):
        return (
            "TREASURE_LOOT_REFERENCE",
            "MEDIUM",
            "NUMERIC_REFERENCE_IN_TREASURE_CONTEXT",
        )

    if any(
        marker.lower() in low
        for marker in SHOP_MARKERS
    ):
        return (
            "NPC_VENDOR_NUMERIC_REFERENCE",
            "HIGH",
            "NUMERIC_REFERENCE_IN_SHOP_CONTEXT",
        )

    if any(
        marker.lower() in low
        for marker in REWARD_MARKERS
    ) and any(
        marker.lower() in low
        for marker in ITEM_CONTEXT_MARKERS
    ):
        if family == "QUEST":
            source_type = "QUEST_REWARD_NUMERIC_REFERENCE"
        elif family == "MISSION":
            source_type = "MISSION_REWARD_NUMERIC_REFERENCE"
        else:
            source_type = "SCRIPT_REWARD_NUMERIC_REFERENCE"

        return (
            source_type,
            "MEDIUM",
            "NUMERIC_REFERENCE_IN_REWARD_CONTEXT",
        )

    if any(
        marker.lower() in low
        for marker in TRADE_MARKERS
    ):
        return (
            "TRADE_NUMERIC_REFERENCE",
            "LOW",
            "NUMERIC_REFERENCE_IN_TRADE_CONTEXT",
        )

    if (
        family in {
            "QUEST",
            "MISSION",
            "NPC_SCRIPT",
            "MOB_SCRIPT",
        }
        and any(
            marker.lower() in low
            for marker in ITEM_CONTEXT_MARKERS
        )
    ):
        return (
            f"{family}_NUMERIC_REFERENCE",
            "LOW",
            "NUMERIC_REFERENCE_IN_ITEM_SHAPED_CONTEXT",
        )

    return (
        "",
        "",
        "",
    )


def main() -> int:
    if not CENSUS_FILE.exists():
        raise NumericLuaAuditError(
            f"Missing census file: {CENSUS_FILE}"
        )

    census = pd.read_csv(
        CENSUS_FILE,
        low_memory=False,
    )

    target = census[
        census["script_reference_count"] == 0
    ].copy()

    if len(target) != 2749:
        raise NumericLuaAuditError(
            f"Expected 2749 no-script items, found {len(target)}"
        )

    target_ids = set(
        target["itemid"].astype(int)
    )

    name_by_id = {
        int(itemid): clean_text(name)
        for itemid, name
        in zip(
            target["itemid"],
            target["name"],
        )
    }

    refs: list[dict[str, Any]] = []

    for path in SCRIPTS_ROOT.rglob("*.lua"):
        rel = "/" + str(
            path.relative_to(LSB_ROOT)
        ).replace("\\", "/")

        if any(
            part in rel
            for part in IGNORE_PATH_PARTS
        ):
            continue

        text = path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        matches = list(
            NUMBER_RE.finditer(text)
        )

        if not matches:
            continue

        lines = text.splitlines()
        family = classify_path(path)

        for match in matches:
            itemid = int(
                match.group(1)
            )

            if itemid not in target_ids:
                continue

            line_number = (
                text.count(
                    "\n",
                    0,
                    match.start(),
                )
                + 1
            )

            start = max(
                0,
                line_number - 7,
            )
            end = min(
                len(lines),
                line_number + 6,
            )

            context = "\n".join(
                lines[start:end]
            )

            (
                source_type,
                confidence,
                reason,
            ) = classify_context(
                family,
                context,
            )

            if not source_type:
                continue

            refs.append(
                {
                    "itemid": itemid,
                    "name": name_by_id.get(
                        itemid,
                        "",
                    ),
                    "source_type": source_type,
                    "confidence": confidence,
                    "reason": reason,
                    "path_family": family,
                    "file": str(
                        path.relative_to(
                            LSB_ROOT
                        )
                    ),
                    "line": line_number,
                    "context": context.replace(
                        "\n",
                        " || ",
                    ),
                }
            )

    references = pd.DataFrame(
        refs
    )

    if references.empty:
        references = pd.DataFrame(
            columns=[
                "itemid",
                "name",
                "source_type",
                "confidence",
                "reason",
                "path_family",
                "file",
                "line",
                "context",
            ]
        )

    references = references.drop_duplicates(
        subset=[
            "itemid",
            "source_type",
            "file",
            "line",
        ]
    ).sort_values(
        by=[
            "itemid",
            "source_type",
            "file",
            "line",
        ],
        kind="stable",
    )

    references.to_csv(
        REFERENCE_FILE,
        index=False,
    )

    item_rows: list[
        dict[
            str,
            Any,
        ]
    ] = []

    refs_by_item: dict[
        int,
        pd.DataFrame,
    ] = {
        int(itemid): group.copy()
        for itemid, group
        in references.groupby(
            "itemid"
        )
    } if not references.empty else {}

    confidence_rank = {
        "HIGH": 3,
        "MEDIUM": 2,
        "LOW": 1,
        "": 0,
    }

    for _, row in target.iterrows():
        itemid = as_int(
            row.get(
                "itemid"
            )
        )

        item_refs = refs_by_item.get(
            itemid
        )

        if item_refs is None:
            source_types: list[str] = []
            files: list[str] = []
            best_confidence = ""
        else:
            source_types = sorted(
                set(
                    item_refs[
                        "source_type"
                    ].map(
                        clean_text
                    )
                )
            )

            files = sorted(
                set(
                    item_refs[
                        "file"
                    ].map(
                        clean_text
                    )
                )
            )

            best_confidence = max(
                item_refs[
                    "confidence"
                ].map(
                    clean_text
                ),
                key=lambda value:
                    confidence_rank.get(
                        value,
                        0,
                    ),
            )

        if best_confidence == "HIGH":
            resolution = "SOURCE_FOUND_HIGH_CONFIDENCE"
        elif best_confidence == "MEDIUM":
            resolution = "SOURCE_FOUND_REVIEW"
        elif best_confidence == "LOW":
            resolution = "NUMERIC_REFERENCE_REVIEW"
        else:
            resolution = "STILL_NO_LUA_SOURCE"

        item_rows.append(
            {
                "itemid": itemid,
                "name": clean_text(
                    row.get(
                        "name"
                    )
                ),
                "item_type": as_int(
                    row.get(
                        "item_type"
                    )
                ),
                "ah_category": as_int(
                    row.get(
                        "ah_category"
                    )
                ),
                "missing_item_enum_constant": as_int(
                    row.get(
                        "missing_item_enum_constant"
                    )
                ),
                "numeric_source_types": "|".join(
                    source_types
                ),
                "best_confidence": best_confidence,
                "numeric_reference_files": "|".join(
                    files
                ),
                "numeric_reference_count": (
                    0
                    if item_refs is None
                    else len(
                        item_refs
                    )
                ),
                "resolution": resolution,
                "activation_ready": 0,
                "auto_live_promotion": 0,
            }
        )

    output = pd.DataFrame(
        item_rows
    ).sort_values(
        by=[
            "resolution",
            "item_type",
            "itemid",
        ],
        kind="stable",
    )

    if len(output) != 2749:
        raise NumericLuaAuditError(
            f"Expected 2749 output items, found {len(output)}"
        )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    summary = {
        "status": "PASS",
        "total_no_script_items": 2749,
        "items_with_numeric_source_reference": int(
            (
                output[
                    "numeric_reference_count"
                ]
                > 0
            ).sum()
        ),
        "items_still_without_lua_source": int(
            (
                output[
                    "numeric_reference_count"
                ]
                == 0
            ).sum()
        ),
        "confidence_item_counts": {
            str(k): int(v)
            for k, v in (
                output[
                    "best_confidence"
                ]
                .replace(
                    "",
                    "NONE",
                )
                .value_counts()
                .to_dict()
                .items()
            )
        },
        "source_type_item_counts": {
            source_type: int(
                references.loc[
                    references[
                        "source_type"
                    ]
                    == source_type,
                    "itemid",
                ].nunique()
            )
            for source_type in sorted(
                references[
                    "source_type"
                ].unique()
            )
        } if not references.empty else {},
        "resolution_counts": {
            str(k): int(v)
            for k, v in (
                output[
                    "resolution"
                ]
                .value_counts()
                .to_dict()
                .items()
            )
        },
        "activation_ready": 0,
        "auto_live_promotions": 0,
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
        " Unknown Numeric Lua Source Audit"
    )
    print(
        "======================================"
    )
    print(
        f"No-script items:                "
        f"{summary['total_no_script_items']:>6}"
    )
    print(
        f"Numeric source references:      "
        f"{summary['items_with_numeric_source_reference']:>6}"
    )
    print(
        f"Still no Lua source:            "
        f"{summary['items_still_without_lua_source']:>6}"
    )
    print()
    print(
        "Resolution:"
    )
    for label, count in sorted(
        summary[
            "resolution_counts"
        ].items(),
        key=lambda pair:
            (
                -pair[
                    1
                ],
                pair[
                    0
                ],
            ),
    ):
        print(
            f"  {label:<34} "
            f"{count:>6}"
        )
    print()
    print(
        "Source types:"
    )
    for label, count in sorted(
        summary[
            "source_type_item_counts"
        ].items(),
        key=lambda pair:
            (
                -pair[
                    1
                ],
                pair[
                    0
                ],
            ),
    ):
        print(
            f"  {label:<38} "
            f"{count:>6}"
        )
    print()
    print(
        "Activation ready: 0"
    )
    print(
        "Auto live promotions: 0"
    )
    print()
    print(
        f"Audit:      {OUTPUT_FILE}"
    )
    print(
        f"References: {REFERENCE_FILE}"
    )
    print(
        f"Summary:    {SUMMARY_FILE}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
