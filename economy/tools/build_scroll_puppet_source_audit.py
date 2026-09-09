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
KEY_ITEM_ENUM_FILE = LSB_ROOT / "scripts" / "enum" / "key_item.lua"
SCRIPTS_ROOT = LSB_ROOT / "scripts"

AUDIT_FILE = ROOT / "economy" / "reports" / "candidate-market-audit.csv"
POLICY_FILE = ROOT / "economy" / "generated" / "candidate-market-policy.csv"
PROVENANCE_FILE = ROOT / "economy" / "generated" / "item-provenance.csv"

OUTPUT_FILE = ROOT / "economy" / "reports" / "scroll-puppet-source-audit.csv"
REFERENCE_FILE = ROOT / "economy" / "reports" / "scroll-puppet-script-references.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "scroll-puppet-source-audit-summary.json"


ITEM_RE = re.compile(
    r"^\s*([A-Z0-9_]+)\s*=\s*(\d+)\s*,?",
    re.MULTILINE,
)

ITEM_REF_RE = re.compile(
    r"xi\.item\.([A-Z0-9_]+)"
)

LITERAL_STOCK_RE_TEMPLATE = (
    r"\{{\s*xi\.item\.{constant}\s*,\s*([0-9]+)\s*[,}}]"
)

REWARD_MARKERS = (
    "giveItem",
    "addItem",
    "addTreasure",
    "addTreasurePool",
    "tradeComplete",
)

SHOP_MARKERS = (
    "xi.shop.",
    "addShopItem",
    "createShop",
)

ACQUISITION_MARKERS = (
    *REWARD_MARKERS,
    *SHOP_MARKERS,
)


class SourceAuditError(RuntimeError):
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
        raise SourceAuditError(
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

        raise SourceAuditError(
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


def parse_enum(
    path: Path,
) -> tuple[
    dict[int, str],
    dict[str, int],
]:
    if not path.exists():
        raise SourceAuditError(
            f"Missing enum: {path}"
        )

    text = path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    by_id: dict[int, str] = {}
    by_name: dict[str, int] = {}

    for name, numeric in ITEM_RE.findall(
        text
    ):
        value = int(
            numeric
        )

        by_id[value] = name
        by_name[name] = value

    if not by_id:
        raise SourceAuditError(
            f"No enum values parsed from {path}"
        )

    return (
        by_id,
        by_name,
    )


def load_recipes() -> pd.DataFrame:
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
            ID,
            Desynth,
            KeyItem,
            Wood,
            Smith,
            Gold,
            Cloth,
            Leather,
            Bone,
            Alchemy,
            Cook,
            Result,
            ResultHQ1,
            ResultHQ2,
            ResultHQ3,
            ResultName,
            content_tag
        FROM synth_recipes
        ORDER BY ID
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                query
            )

            rows = cursor.fetchall()

    finally:
        connection.close()

    return pd.DataFrame(
        rows
    )


def recipe_skill(
    row: pd.Series,
) -> tuple[
    int,
    str,
]:
    skill_columns = [
        "Wood",
        "Smith",
        "Gold",
        "Cloth",
        "Leather",
        "Bone",
        "Alchemy",
        "Cook",
    ]

    values = {
        column:
            as_int(
                row.get(
                    column
                )
            )
        for column in skill_columns
    }

    max_skill = max(
        values.values(),
        default=0,
    )

    names = [
        name
        for name, level
        in values.items()
        if (
            level == max_skill
            and level > 0
        )
    ]

    return (
        max_skill,
        "|".join(
            names
        ),
    )


def classify_reference(
    path: Path,
    full_text: str,
    context: str,
) -> tuple[
    str,
    int,
    int,
]:
    posix = path.as_posix()

    shop_like = int(
        any(
            marker in full_text
            for marker in SHOP_MARKERS
        )
    )

    reward_like = int(
        any(
            marker in context
            for marker in REWARD_MARKERS
        )
    )

    if "/actions/abilities/pets/attachments/" in posix:
        return (
            "ATTACHMENT_BEHAVIOR",
            shop_like,
            reward_like,
        )

    if "/quests/" in posix:
        return (
            "QUEST",
            shop_like,
            reward_like,
        )

    if "/missions/" in posix:
        return (
            "MISSION",
            shop_like,
            reward_like,
        )

    if shop_like:
        return (
            "SHOP",
            shop_like,
            reward_like,
        )

    if "/npcs/" in posix:
        return (
            "NPC_OTHER",
            shop_like,
            reward_like,
        )

    return (
        "OTHER",
        shop_like,
        reward_like,
    )


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

        refs = list(
            ITEM_REF_RE.finditer(
                text
            )
        )

        if not refs:
            continue

        lines = text.splitlines()

        for match in refs:
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
                line_number - 9,
            )

            end = min(
                len(lines),
                line_number + 8,
            )

            context = "\n".join(
                lines[
                    start:end
                ]
            )

            (
                reference_type,
                shop_like,
                reward_like,
            ) = classify_reference(
                path,
                text,
                context,
            )

            literal_price = 0

            price_re = re.compile(
                LITERAL_STOCK_RE_TEMPLATE.format(
                    constant=re.escape(
                        constant
                    )
                )
            )

            price_match = price_re.search(
                context
            )

            if price_match is not None:
                literal_price = int(
                    price_match.group(
                        1
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

                    "reference_type":
                        reference_type,

                    "shop_like_file":
                        shop_like,

                    "reward_like_context":
                        reward_like,

                    "literal_shop_price":
                        literal_price,

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


def main() -> int:
    for path in [
        AUDIT_FILE,
        POLICY_FILE,
        PROVENANCE_FILE,
    ]:
        if not path.exists():
            raise SourceAuditError(
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

    target = audit[
        audit["audit_bucket"].isin(
            [
                "SCROLL_POLICY",
                "PUPPET_POLICY",
            ]
        )
    ][
        [
            "audit_bucket",
            "itemid",
            "name",
        ]
    ].copy()

    if len(
        target
    ) != 669:
        raise SourceAuditError(
            "Expected 669 Scroll/Puppet audit items, "
            f"found {len(target)}"
        )

    (
        item_by_id,
        _,
    ) = parse_enum(
        ITEM_ENUM_FILE
    )

    (
        keyitem_by_id,
        _,
    ) = parse_enum(
        KEY_ITEM_ENUM_FILE
    )

    target[
        "item_constant"
    ] = target[
        "itemid"
    ].astype(int).map(
        item_by_id
    )

    missing_enum_ids = sorted(
        target.loc[
            target[
                "item_constant"
            ].isna(),
            "itemid",
        ]
        .astype(int)
        .tolist()
    )

    target[
        "item_constant"
    ] = target[
        "item_constant"
    ].fillna(
        ""
    )

    target[
        "missing_item_enum_constant"
    ] = target[
        "item_constant"
    ].eq(
        ""
    ).astype(int)

    # LSB does not guarantee a Lua xi.item constant for every valid DB item.
    # Scan named constants where available and continue auditing missing
    # constants through database/synthesis evidence instead of aborting.
    named_constants = {
        constant
        for constant in target[
            "item_constant"
        ].map(
            clean_text
        )
        if constant
    }

    references = scan_scripts(
        named_constants
    )

    if references.empty:
        references = pd.DataFrame(
            columns=[
                "constant",
                "file",
                "line",
                "reference_type",
                "shop_like_file",
                "reward_like_context",
                "literal_shop_price",
                "context",
            ]
        )

    constant_to_id = {
        constant:
            int(
                itemid
            )
        for itemid, constant
        in zip(
            target[
                "itemid"
            ],
            target[
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

    references = references.merge(
        target[
            [
                "itemid",
                "name",
                "audit_bucket",
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

    recipes = load_recipes()

    output_columns = [
        ("Result", "NQ"),
        ("ResultHQ1", "HQ1"),
        ("ResultHQ2", "HQ2"),
        ("ResultHQ3", "HQ3"),
    ]

    target_ids = set(
        target[
            "itemid"
        ].astype(int)
    )

    recipe_evidence: dict[
        int,
        list[dict[str, Any]],
    ] = defaultdict(
        list
    )

    for _, recipe in recipes.iterrows():
        (
            skill_level,
            skill_names,
        ) = recipe_skill(
            recipe
        )

        keyitem_id = as_int(
            recipe.get(
                "KeyItem"
            )
        )

        keyitem_name = keyitem_by_id.get(
            keyitem_id,
            "",
        )

        for column, role in output_columns:
            itemid = as_int(
                recipe.get(
                    column
                )
            )

            if itemid not in target_ids:
                continue

            recipe_evidence[
                itemid
            ].append(
                {
                    "recipe_id":
                        as_int(
                            recipe.get(
                                "ID"
                            )
                        ),

                    "output_role":
                        role,

                    "desynth":
                        as_int(
                            recipe.get(
                                "Desynth"
                            )
                        ),

                    "keyitem_id":
                        keyitem_id,

                    "keyitem_name":
                        keyitem_name,

                    "craft_level":
                        skill_level,

                    "craft_skill":
                        skill_names,

                    "content_tag":
                        clean_text(
                            recipe.get(
                                "content_tag"
                            )
                        ),
                }
            )

    policy_subset = policy[
        [
            column
            for column in [
                "itemid",
                "vendor_item",
                "has_vendor_price_floor",
                "pricing_ready",
                "stack_size",
                "rare",
                "base_sell",
                "source_override_applied",
                "source_override_class",
                "source_override_types",
            ]
            if column in policy.columns
        ]
    ].copy()

    provenance_subset = provenance[
        [
            column
            for column in [
                "itemid",
                "provenance_tags",
                "scroll",
                "item_type",
                "craft_nq_output",
                "craft_hq_output",
                "restricted_nq_output",
                "restricted_hq_output",
            ]
            if column in provenance.columns
        ]
    ].copy()

    merged = target.merge(
        policy_subset,
        on="itemid",
        how="left",
        validate="one_to_one",
    ).merge(
        provenance_subset,
        on="itemid",
        how="left",
        validate="one_to_one",
    )

    ref_grouped = defaultdict(
        list
    )

    for _, row in references.iterrows():
        ref_grouped[
            as_int(
                row.get(
                    "itemid"
                )
            )
        ].append(
            row
        )

    rows: list[dict[str, Any]] = []

    for _, item in merged.iterrows():
        itemid = as_int(
            item.get(
                "itemid"
            )
        )

        refs = ref_grouped.get(
            itemid,
            [],
        )

        synth = recipe_evidence.get(
            itemid,
            [],
        )

        shop_refs = [
            ref
            for ref in refs
            if clean_text(
                ref.get(
                    "reference_type"
                )
            )
            == "SHOP"
        ]

        quest_refs = [
            ref
            for ref in refs
            if clean_text(
                ref.get(
                    "reference_type"
                )
            )
            == "QUEST"
        ]

        mission_refs = [
            ref
            for ref in refs
            if clean_text(
                ref.get(
                    "reference_type"
                )
            )
            == "MISSION"
        ]

        reward_refs = [
            ref
            for ref in refs
            if as_int(
                ref.get(
                    "reward_like_context"
                )
            )
            == 1
        ]

        attachment_refs = [
            ref
            for ref in refs
            if clean_text(
                ref.get(
                    "reference_type"
                )
            )
            == "ATTACHMENT_BEHAVIOR"
        ]

        literal_prices = sorted(
            {
                as_int(
                    ref.get(
                        "literal_shop_price"
                    )
                )
                for ref in shop_refs
                if as_int(
                    ref.get(
                        "literal_shop_price"
                    )
                ) > 0
            }
        )

        recipe_ids = sorted(
            {
                entry[
                    "recipe_id"
                ]
                for entry in synth
            }
        )

        keyitem_names = sorted(
            {
                entry[
                    "keyitem_name"
                ]
                for entry in synth
                if entry[
                    "keyitem_name"
                ]
            }
        )

        craft_levels = [
            entry[
                "craft_level"
            ]
            for entry in synth
            if entry[
                "craft_level"
            ] > 0
        ]

        craft_skills = sorted(
            {
                entry[
                    "craft_skill"
                ]
                for entry in synth
                if entry[
                    "craft_skill"
                ]
            }
        )

        source_signals: list[str] = []

        if as_int(
            item.get(
                "missing_item_enum_constant"
            )
        ):
            source_signals.append(
                "MISSING_ITEM_ENUM_CONSTANT"
            )

        if shop_refs:
            source_signals.append(
                "SCRIPT_SHOP"
            )

        if quest_refs:
            source_signals.append(
                "QUEST_REFERENCE"
            )

        if mission_refs:
            source_signals.append(
                "MISSION_REFERENCE"
            )

        if reward_refs:
            source_signals.append(
                "REWARD_LIKE_REFERENCE"
            )

        if synth:
            source_signals.append(
                "SYNTH_RECIPE"
            )

        if attachment_refs:
            source_signals.append(
                "ATTACHMENT_BEHAVIOR"
            )

        if as_int(
            item.get(
                "vendor_item"
            )
        ):
            source_signals.append(
                "BROAD_VENDOR_INDEX"
            )

        if as_int(
            item.get(
                "has_vendor_price_floor"
            )
        ):
            source_signals.append(
                "TRUSTED_VENDOR_PRICE"
            )

        rows.append(
            {
                "audit_bucket":
                    clean_text(
                        item.get(
                            "audit_bucket"
                        )
                    ),

                "itemid":
                    itemid,

                "name":
                    clean_text(
                        item.get(
                            "name"
                        )
                    ),

                "item_constant":
                    clean_text(
                        item.get(
                            "item_constant"
                        )
                    ),

                "missing_item_enum_constant":
                    as_int(
                        item.get(
                            "missing_item_enum_constant"
                        )
                    ),

                "source_signals":
                    "|".join(
                        source_signals
                    ),

                "script_reference_count":
                    len(
                        refs
                    ),

                "shop_reference_count":
                    len(
                        shop_refs
                    ),

                "quest_reference_count":
                    len(
                        quest_refs
                    ),

                "mission_reference_count":
                    len(
                        mission_refs
                    ),

                "reward_like_reference_count":
                    len(
                        reward_refs
                    ),

                "attachment_behavior_reference_count":
                    len(
                        attachment_refs
                    ),

                "synth_recipe_count":
                    len(
                        recipe_ids
                    ),

                "restricted_recipe_count":
                    sum(
                        1
                        for entry in synth
                        if entry[
                            "keyitem_id"
                        ] > 0
                    ),

                "craft_min_skill":
                    (
                        min(
                            craft_levels
                        )
                        if craft_levels
                        else 0
                    ),

                "craft_max_skill":
                    (
                        max(
                            craft_levels
                        )
                        if craft_levels
                        else 0
                    ),

                "craft_skills":
                    "|".join(
                        craft_skills
                    ),

                "keyitem_names":
                    "|".join(
                        keyitem_names
                    ),

                "literal_shop_prices":
                    "|".join(
                        str(
                            price
                        )
                        for price in literal_prices
                    ),

                "vendor_item":
                    as_int(
                        item.get(
                            "vendor_item"
                        )
                    ),

                "has_vendor_price_floor":
                    as_int(
                        item.get(
                            "has_vendor_price_floor"
                        )
                    ),

                "pricing_ready":
                    as_int(
                        item.get(
                            "pricing_ready"
                        )
                    ),

                "stack_size":
                    as_int(
                        item.get(
                            "stack_size"
                        )
                    ),

                "rare":
                    as_int(
                        item.get(
                            "rare"
                        )
                    ),

                "provenance_tags":
                    clean_text(
                        item.get(
                            "provenance_tags"
                        )
                    ),

                "shop_files":
                    "|".join(
                        sorted(
                            {
                                clean_text(
                                    ref.get(
                                        "file"
                                    )
                                )
                                for ref in shop_refs
                                if clean_text(
                                    ref.get(
                                        "file"
                                    )
                                )
                            }
                        )
                    ),

                "quest_files":
                    "|".join(
                        sorted(
                            {
                                clean_text(
                                    ref.get(
                                        "file"
                                    )
                                )
                                for ref in quest_refs
                                if clean_text(
                                    ref.get(
                                        "file"
                                    )
                                )
                            }
                        )
                    ),

                "mission_files":
                    "|".join(
                        sorted(
                            {
                                clean_text(
                                    ref.get(
                                        "file"
                                    )
                                )
                                for ref in mission_refs
                                if clean_text(
                                    ref.get(
                                        "file"
                                    )
                                )
                            }
                        )
                    ),

                "all_reference_files":
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

                "auto_live_promotion":
                    0,
            }
        )

    output = pd.DataFrame(
        rows
    ).sort_values(
        by=[
            "audit_bucket",
            "itemid",
        ],
        kind="stable",
    )

    if len(
        output
    ) != 669:
        raise SourceAuditError(
            "Source audit output does not contain 669 items."
        )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    if int(
        output[
            "auto_live_promotion"
        ].sum()
    ) != 0:
        raise SourceAuditError(
            "Source audit attempted live promotion."
        )

    summary = {
        "status":
            "PASS",

        "total_items":
            int(
                len(
                    output
                )
            ),

        "scroll_items":
            int(
                (
                    output[
                        "audit_bucket"
                    ]
                    == "SCROLL_POLICY"
                ).sum()
            ),

        "puppet_items":
            int(
                (
                    output[
                        "audit_bucket"
                    ]
                    == "PUPPET_POLICY"
                ).sum()
            ),

        "items_with_script_shop_reference":
            int(
                (
                    output[
                        "shop_reference_count"
                    ]
                    > 0
                ).sum()
            ),

        "items_with_literal_shop_price":
            int(
                output[
                    "literal_shop_prices"
                ]
                .map(
                    clean_text
                )
                .ne(
                    ""
                )
                .sum()
            ),

        "items_with_synth_recipe":
            int(
                (
                    output[
                        "synth_recipe_count"
                    ]
                    > 0
                ).sum()
            ),

        "items_with_quest_reference":
            int(
                (
                    output[
                        "quest_reference_count"
                    ]
                    > 0
                ).sum()
            ),

        "items_with_mission_reference":
            int(
                (
                    output[
                        "mission_reference_count"
                    ]
                    > 0
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

        "items_with_attachment_behavior_reference":
            int(
                (
                    output[
                        "attachment_behavior_reference_count"
                    ]
                    > 0
                ).sum()
            ),

        "items_missing_item_enum_constant":
            int(
                output[
                    "missing_item_enum_constant"
                ].sum()
            ),

        "missing_item_enum_itemids":
            missing_enum_ids,

        "items_with_no_new_source_signal":
            int(
                output[
                    "source_signals"
                ]
                .map(
                    clean_text
                )
                .isin(
                    [
                        "",
                        "BROAD_VENDOR_INDEX",
                        "MISSING_ITEM_ENUM_CONSTANT",
                        "MISSING_ITEM_ENUM_CONSTANT|BROAD_VENDOR_INDEX",
                    ]
                )
                .sum()
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
        " Scroll + Puppet Source Audit"
    )
    print(
        "======================================"
    )

    print(
        f"Total items:                    "
        f"{summary['total_items']:>6}"
    )

    print(
        f"Scroll items:                   "
        f"{summary['scroll_items']:>6}"
    )

    print(
        f"Puppet items:                   "
        f"{summary['puppet_items']:>6}"
    )

    print()
    print(
        f"Script shop references:         "
        f"{summary['items_with_script_shop_reference']:>6}"
    )

    print(
        f"Literal shop prices found:      "
        f"{summary['items_with_literal_shop_price']:>6}"
    )

    print(
        f"Synthesis recipes found:        "
        f"{summary['items_with_synth_recipe']:>6}"
    )

    print(
        f"Quest references found:         "
        f"{summary['items_with_quest_reference']:>6}"
    )

    print(
        f"Mission references found:       "
        f"{summary['items_with_mission_reference']:>6}"
    )

    print(
        f"Reward-like references found:   "
        f"{summary['items_with_reward_like_reference']:>6}"
    )

    print(
        f"Attachment behavior references: "
        f"{summary['items_with_attachment_behavior_reference']:>6}"
    )

    print(
        f"Missing item enum constants:    "
        f"{summary['items_missing_item_enum_constant']:>6}"
    )

    print(
        f"No new source signal:           "
        f"{summary['items_with_no_new_source_signal']:>6}"
    )

    print()
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
