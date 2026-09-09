from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
import pymysql
import yaml


ROOT = Path.home() / "ffxiahbot"
LSB_ROOT = Path.home() / "server"

CONFIG_FILE = ROOT / "bin" / "config.yaml"
ITEM_ENUM_FILE = LSB_ROOT / "scripts" / "enum" / "item.lua"
SCRIPTS_ROOT = LSB_ROOT / "scripts"

AUDIT_FILE = ROOT / "economy" / "reports" / "candidate-market-audit.csv"
POLICY_FILE = ROOT / "economy" / "generated" / "candidate-market-policy.csv"
PROVENANCE_FILE = ROOT / "economy" / "generated" / "item-provenance.csv"

OUTPUT_FILE = ROOT / "economy" / "reports" / "unknown-provenance-census.csv"
REFERENCE_FILE = ROOT / "economy" / "reports" / "unknown-provenance-script-references.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "unknown-provenance-census-summary.json"


ENUM_RE = re.compile(
    r"^\s*([A-Z0-9_]+)\s*=\s*(\d+)\s*,?",
    re.MULTILINE,
)

ITEM_REF_RE = re.compile(
    r"xi\.item\.([A-Z0-9_]+)"
)

REWARD_MARKERS = (
    "addItem",
    "giveItem",
    "npcUtil.giveItem",
    "npcUtil.giveItemSilent",
    "addTreasure",
    "addTreasurePool",
)

SHOP_MARKERS = (
    "xi.shop.",
    "addShopItem",
    "createShop",
)

TRADE_MARKERS = (
    "trade:hasItemQty",
    "trade:getItemQty",
    "trade:getItemCount",
    "npcUtil.tradeHas",
)

KEYITEM_MARKERS = (
    "addKeyItem",
    "hasKeyItem",
    "delKeyItem",
)


class UnknownCensusError(RuntimeError):
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


def load_db_settings() -> dict[str, Any]:
    data = yaml.safe_load(
        CONFIG_FILE.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(data, dict):
        raise UnknownCensusError(
            f"Invalid config mapping: {CONFIG_FILE}"
        )

    source = data.get("db")

    if not isinstance(source, dict):
        source = data

    def required(*keys: str):
        for key in keys:
            if key in source:
                value = source[key]

                if value not in {
                    None,
                    "",
                }:
                    return value

        raise UnknownCensusError(
            "Missing DB config key; expected one of: "
            + ", ".join(keys)
        )

    return {
        "host": required(
            "hostname",
            "host",
        ),
        "database": required(
            "database",
            "dbname",
        ),
        "user": required(
            "username",
            "user",
        ),
        "password": required(
            "password",
        ),
        "port": int(
            source.get(
                "port",
                3306,
            )
        ),
    }


def load_item_basic() -> pd.DataFrame:
    cfg = load_db_settings()

    connection = pymysql.connect(
        host=str(
            cfg["host"]
        ),
        user=str(
            cfg["user"]
        ),
        password=str(
            cfg["password"]
        ),
        database=str(
            cfg["database"]
        ),
        port=int(
            cfg["port"]
        ),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )

    query = """
        SELECT
            itemid,
            name,
            sortname,
            type,
            stackSize,
            flags,
            aH,
            BaseSell
        FROM item_basic
        ORDER BY itemid
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                query
            )

            rows = cursor.fetchall()

    finally:
        connection.close()

    frame = pd.DataFrame(
        rows
    )

    if frame.empty:
        raise UnknownCensusError(
            "item_basic returned no rows."
        )

    return frame


def parse_item_enum() -> tuple[
    dict[int, str],
    dict[str, int],
]:
    if not ITEM_ENUM_FILE.exists():
        raise UnknownCensusError(
            f"Missing item enum: {ITEM_ENUM_FILE}"
        )

    text = ITEM_ENUM_FILE.read_text(
        encoding="utf-8",
        errors="replace",
    )

    by_id: dict[int, str] = {}
    by_name: dict[str, int] = {}

    for constant, numeric in ENUM_RE.findall(
        text
    ):
        itemid = int(
            numeric
        )

        by_id[
            itemid
        ] = constant

        by_name[
            constant
        ] = itemid

    if not by_id:
        raise UnknownCensusError(
            "No item constants parsed."
        )

    return (
        by_id,
        by_name,
    )


def classify_path(
    path: Path,
) -> str:
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

    if (
        "treasure" in posix
        or "coffer" in posix
        or "chest" in posix
    ):
        return "TREASURE_SYSTEM"

    if "/zones/" in posix and "/mobs/" in posix:
        return "MOB_SCRIPT"

    if "/zones/" in posix and "/npcs/" in posix:
        return "NPC_SCRIPT"

    if "/items/" in posix:
        return "ITEM_SCRIPT"

    if "/actions/" in posix:
        return "ACTION_SCRIPT"

    if "/globals/" in posix:
        return "GLOBAL_SCRIPT"

    return "OTHER"


def scan_scripts(
    target_constants: set[str],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for path in SCRIPTS_ROOT.rglob(
        "*.lua"
    ):
        if path == ITEM_ENUM_FILE:
            continue

        text = path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        matches = list(
            ITEM_REF_RE.finditer(
                text
            )
        )

        if not matches:
            continue

        lines = text.splitlines()

        for match in matches:
            constant = match.group(
                1
            )

            if constant not in target_constants:
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
                line_number - 8,
            )

            end = min(
                len(
                    lines
                ),
                line_number + 7,
            )

            context = "\n".join(
                lines[
                    start:end
                ]
            )

            reward_like = int(
                any(
                    marker in context
                    for marker in REWARD_MARKERS
                )
            )

            shop_like = int(
                any(
                    marker in context
                    for marker in SHOP_MARKERS
                )
                or any(
                    marker in text
                    for marker in SHOP_MARKERS
                )
            )

            trade_like = int(
                any(
                    marker in context
                    for marker in TRADE_MARKERS
                )
            )

            keyitem_like = int(
                any(
                    marker in context
                    for marker in KEYITEM_MARKERS
                )
            )

            rows.append(
                {
                    "constant":
                        constant,

                    "file":
                        str(
                            path.relative_to(
                                LSB_ROOT
                            )
                        ),

                    "line":
                        line_number,

                    "path_family":
                        classify_path(
                            path
                        ),

                    "reward_like":
                        reward_like,

                    "shop_like":
                        shop_like,

                    "trade_like":
                        trade_like,

                    "keyitem_like":
                        keyitem_like,

                    "context":
                        context.replace(
                            "\n",
                            " || ",
                        ),
                }
            )

    return pd.DataFrame(
        rows
    )


def ensure_columns(
    frame: pd.DataFrame,
    columns: list[str],
) -> pd.DataFrame:
    out = frame.copy()

    for column in columns:
        if column not in out.columns:
            out[
                column
            ] = ""

    return out


def main() -> int:
    for path in [
        AUDIT_FILE,
        POLICY_FILE,
        PROVENANCE_FILE,
    ]:
        if not path.exists():
            raise UnknownCensusError(
                f"Missing required input: {path}"
            )

    audit = pd.read_csv(
        AUDIT_FILE,
        low_memory=False,
    )

    policy = pd.read_csv(
        POLICY_FILE,
        low_memory=False,
    )

    provenance = pd.read_csv(
        PROVENANCE_FILE,
        low_memory=False,
    )

    unknown = audit[
        audit[
            "audit_bucket"
        ]
        == "UNKNOWN_OR_UNCLASSIFIED_SOURCE"
    ].copy()

    if len(
        unknown
    ) != 5011:
        raise UnknownCensusError(
            "Expected 5011 unknown/unclassified items, "
            f"found {len(unknown)}"
        )

    items = load_item_basic()

    (
        enum_by_id,
        _,
    ) = parse_item_enum()

    unknown[
        "item_constant"
    ] = unknown[
        "itemid"
    ].astype(int).map(
        enum_by_id
    )

    unknown[
        "missing_item_enum_constant"
    ] = unknown[
        "item_constant"
    ].isna().astype(int)

    unknown[
        "item_constant"
    ] = unknown[
        "item_constant"
    ].fillna(
        ""
    )

    target_constants = {
        constant
        for constant in unknown[
            "item_constant"
        ].map(
            clean_text
        )
        if constant
    }

    references = scan_scripts(
        target_constants
    )

    if references.empty:
        references = pd.DataFrame(
            columns=[
                "constant",
                "file",
                "line",
                "path_family",
                "reward_like",
                "shop_like",
                "trade_like",
                "keyitem_like",
                "context",
            ]
        )

    constant_to_id = {
        clean_text(
            constant
        ):
            int(
                itemid
            )
        for itemid, constant
        in zip(
            unknown[
                "itemid"
            ],
            unknown[
                "item_constant"
            ],
        )
        if clean_text(
            constant
        )
    }

    references[
        "itemid"
    ] = references[
        "constant"
    ].map(
        constant_to_id
    )

    references = references[
        references[
            "itemid"
        ].notna()
    ].copy()

    if not references.empty:
        references[
            "itemid"
        ] = references[
            "itemid"
        ].astype(int)

        references = references.merge(
            unknown[
                [
                    "itemid",
                    "name",
                ]
            ],
            on="itemid",
            how="left",
            validate="many_to_one",
        )

    references.to_csv(
        REFERENCE_FILE,
        index=False,
    )

    policy = ensure_columns(
        policy,
        [
            "itemid",
            "candidate_class",
            "candidate_reason",
            "vendor_item",
            "has_vendor_price_floor",
            "pricing_ready",
            "stack_size",
            "rare",
            "can_equip",
            "base_sell",
            "source_override_applied",
            "source_override_types",
        ],
    )

    provenance = ensure_columns(
        provenance,
        [
            "itemid",
            "provenance_tags",
            "craft_input",
            "craft_nq_output",
            "craft_hq_output",
            "desynth_input",
            "desynth_output",
            "fishing",
            "vendor_item",
            "mob_drop",
        ],
    )

    merged = unknown.merge(
        items[
            [
                "itemid",
                "type",
                "stackSize",
                "flags",
                "aH",
                "BaseSell",
            ]
        ],
        on="itemid",
        how="left",
        validate="one_to_one",
    ).merge(
        policy[
            [
                "itemid",
                "candidate_class",
                "candidate_reason",
                "vendor_item",
                "has_vendor_price_floor",
                "pricing_ready",
                "rare",
                "can_equip",
                "source_override_applied",
                "source_override_types",
            ]
        ],
        on="itemid",
        how="left",
        validate="one_to_one",
        suffixes=(
            "",
            "_policy",
        ),
    ).merge(
        provenance[
            [
                "itemid",
                "provenance_tags",
                "craft_input",
                "craft_nq_output",
                "craft_hq_output",
                "desynth_input",
                "desynth_output",
                "fishing",
                "mob_drop",
            ]
        ],
        on="itemid",
        how="left",
        validate="one_to_one",
    )

    refs_by_item: dict[
        int,
        list[
            dict[
                str,
                Any,
            ]
        ],
    ] = defaultdict(
        list
    )

    for _, ref in references.iterrows():
        refs_by_item[
            as_int(
                ref.get(
                    "itemid"
                )
            )
        ].append(
            ref.to_dict()
        )

    rows: list[dict[str, Any]] = []

    for _, row in merged.iterrows():
        itemid = as_int(
            row.get(
                "itemid"
            )
        )

        refs = refs_by_item.get(
            itemid,
            [],
        )

        path_families = sorted(
            {
                clean_text(
                    ref.get(
                        "path_family"
                    )
                )
                for ref in refs
                if clean_text(
                    ref.get(
                        "path_family"
                    )
                )
            }
        )

        reward_like_count = sum(
            as_int(
                ref.get(
                    "reward_like"
                )
            )
            for ref in refs
        )

        shop_like_count = sum(
            as_int(
                ref.get(
                    "shop_like"
                )
            )
            for ref in refs
        )

        trade_like_count = sum(
            as_int(
                ref.get(
                    "trade_like"
                )
            )
            for ref in refs
        )

        source_signals: list[str] = []

        if refs:
            source_signals.append(
                "SCRIPT_REFERENCE"
            )

        if reward_like_count:
            source_signals.append(
                "REWARD_LIKE"
            )

        if shop_like_count:
            source_signals.append(
                "SHOP_LIKE"
            )

        if trade_like_count:
            source_signals.append(
                "TRADE_LIKE"
            )

        for family in path_families:
            source_signals.append(
                family
            )

        if as_int(
            row.get(
                "missing_item_enum_constant"
            )
        ):
            source_signals.append(
                "MISSING_ITEM_ENUM_CONSTANT"
            )

        if not refs:
            audit_priority = 2
            recommended_next_step = (
                "NO_SCRIPT_REFERENCE_REVIEW"
            )

        elif reward_like_count:
            audit_priority = 1
            recommended_next_step = (
                "REWARD_SOURCE_CLASSIFICATION"
            )

        elif any(
            family in {
                "QUEST",
                "MISSION",
                "BATTLEFIELD",
                "HELM",
                "CHOCOBO_DIGGING",
                "TREASURE_SYSTEM",
                "MOB_SCRIPT",
                "NPC_SCRIPT",
            }
            for family in path_families
        ):
            audit_priority = 1
            recommended_next_step = (
                "SOURCE_FAMILY_CLASSIFICATION"
            )

        else:
            audit_priority = 2
            recommended_next_step = (
                "REFERENCE_CONTEXT_REVIEW"
            )

        rows.append(
            {
                "itemid":
                    itemid,

                "name":
                    clean_text(
                        row.get(
                            "name"
                        )
                    ),

                "item_constant":
                    clean_text(
                        row.get(
                            "item_constant"
                        )
                    ),

                "missing_item_enum_constant":
                    as_int(
                        row.get(
                            "missing_item_enum_constant"
                        )
                    ),

                "item_type":
                    as_int(
                        row.get(
                            "type"
                        )
                    ),

                "stack_size":
                    as_int(
                        row.get(
                            "stackSize"
                        )
                    ),

                "flags":
                    as_int(
                        row.get(
                            "flags"
                        )
                    ),

                "ah_category":
                    as_int(
                        row.get(
                            "aH"
                        )
                    ),

                "base_sell":
                    as_int(
                        row.get(
                            "BaseSell"
                        )
                    ),

                "candidate_class":
                    clean_text(
                        row.get(
                            "candidate_class"
                        )
                    ),

                "candidate_reason":
                    clean_text(
                        row.get(
                            "candidate_reason"
                        )
                    ),

                "vendor_item":
                    as_int(
                        row.get(
                            "vendor_item"
                        )
                    ),

                "has_vendor_price_floor":
                    as_int(
                        row.get(
                            "has_vendor_price_floor"
                        )
                    ),

                "pricing_ready":
                    as_int(
                        row.get(
                            "pricing_ready"
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

                "provenance_tags":
                    clean_text(
                        row.get(
                            "provenance_tags"
                        )
                    ),

                "script_reference_count":
                    len(
                        refs
                    ),

                "path_families":
                    "|".join(
                        path_families
                    ),

                "reward_like_reference_count":
                    reward_like_count,

                "shop_like_reference_count":
                    shop_like_count,

                "trade_like_reference_count":
                    trade_like_count,

                "source_signals":
                    "|".join(
                        source_signals
                    ),

                "reference_files":
                    "|".join(
                        sorted(
                            {
                                clean_text(
                                    ref.get(
                                        "file"
                                    )
                                )
                                for ref in refs
                                if clean_text(
                                    ref.get(
                                        "file"
                                    )
                                )
                            }
                        )
                    ),

                "audit_priority":
                    audit_priority,

                "recommended_next_step":
                    recommended_next_step,

                "activation_ready":
                    0,

                "auto_live_promotion":
                    0,
            }
        )

    output = pd.DataFrame(
        rows
    ).sort_values(
        by=[
            "audit_priority",
            "recommended_next_step",
            "itemid",
        ],
        kind="stable",
    )

    if len(
        output
    ) != 5011:
        raise UnknownCensusError(
            f"Expected 5011 census rows, found {len(output)}"
        )

    if int(
        output[
            "activation_ready"
        ].sum()
    ) != 0:
        raise UnknownCensusError(
            "Unknown provenance census activated items."
        )

    if int(
        output[
            "auto_live_promotion"
        ].sum()
    ) != 0:
        raise UnknownCensusError(
            "Unknown provenance census attempted live promotion."
        )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    family_item_counts: dict[
        str,
        int,
    ] = {}

    for family in sorted(
        {
            family
            for value in output[
                "path_families"
            ].map(
                clean_text
            )
            for family in value.split(
                "|"
            )
            if family
        }
    ):
        family_item_counts[
            family
        ] = int(
            output[
                "path_families"
            ]
            .fillna(
                ""
            )
            .map(
                lambda value:
                    family in str(
                        value
                    ).split(
                        "|"
                    )
            )
            .sum()
        )

    summary = {
        "status":
            "PASS",

        "total_unknown_items":
            5011,

        "items_with_item_enum_constant":
            int(
                (
                    output[
                        "missing_item_enum_constant"
                    ]
                    == 0
                ).sum()
            ),

        "items_missing_item_enum_constant":
            int(
                output[
                    "missing_item_enum_constant"
                ].sum()
            ),

        "items_with_script_reference":
            int(
                (
                    output[
                        "script_reference_count"
                    ]
                    > 0
                ).sum()
            ),

        "items_without_script_reference":
            int(
                (
                    output[
                        "script_reference_count"
                    ]
                    == 0
                ).sum()
            ),

        "items_with_reward_like_reference":
            int(
                (
                    output[
                        "reward_like_reference_count"
                    ]
                    > 0
                ).sum()
            ),

        "items_with_shop_like_reference":
            int(
                (
                    output[
                        "shop_like_reference_count"
                    ]
                    > 0
                ).sum()
            ),

        "items_with_trade_like_reference":
            int(
                (
                    output[
                        "trade_like_reference_count"
                    ]
                    > 0
                ).sum()
            ),

        "path_family_item_counts":
            family_item_counts,

        "item_type_counts":
            {
                str(k):
                    int(v)
                for k, v
                in output[
                    "item_type"
                ]
                .value_counts()
                .sort_index()
                .to_dict()
                .items()
            },

        "recommended_next_steps":
            {
                str(k):
                    int(v)
                for k, v
                in output[
                    "recommended_next_step"
                ]
                .value_counts()
                .to_dict()
                .items()
            },

        "activation_ready":
            0,

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
        " Unknown Provenance Census"
    )
    print(
        "======================================"
    )

    print(
        f"Unknown items:                  "
        f"{summary['total_unknown_items']:>6}"
    )

    print(
        f"With item enum constant:        "
        f"{summary['items_with_item_enum_constant']:>6}"
    )

    print(
        f"Missing item enum constant:     "
        f"{summary['items_missing_item_enum_constant']:>6}"
    )

    print()
    print(
        f"With script reference:          "
        f"{summary['items_with_script_reference']:>6}"
    )

    print(
        f"Without script reference:       "
        f"{summary['items_without_script_reference']:>6}"
    )

    print(
        f"Reward-like references:         "
        f"{summary['items_with_reward_like_reference']:>6}"
    )

    print(
        f"Shop-like references:           "
        f"{summary['items_with_shop_like_reference']:>6}"
    )

    print(
        f"Trade-like references:          "
        f"{summary['items_with_trade_like_reference']:>6}"
    )

    print()
    print(
        "Path-family item counts:"
    )

    for family, count in sorted(
        summary[
            "path_family_item_counts"
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
            f"  {family:<24} "
            f"{count:>6}"
        )

    print()
    print(
        "Recommended next steps:"
    )

    for step, count in sorted(
        summary[
            "recommended_next_steps"
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
            f"  {step:<34} "
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
        f"Census:     {OUTPUT_FILE}"
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
