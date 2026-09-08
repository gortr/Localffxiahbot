from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
import pymysql
import yaml


# ============================================================
# Paths
# ============================================================

ROOT = Path.home() / "ffxiahbot"
LSB_ROOT = Path.home() / "server"

CONFIG_FILE = ROOT / "bin" / "config.yaml"
DROP_SQL_FILE = LSB_ROOT / "sql" / "mob_droplist.sql"

GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

ITEM_PROVENANCE_FILE = GENERATED / "item-provenance.csv"
OUTPUT_FILE = GENERATED / "mob-source-classification.csv"

SOURCE_REPORT = REPORTS / "mob-source-classification-sources.csv"
SUMMARY_FILE = REPORTS / "mob-source-classification-summary.json"


# ============================================================
# Evidence labels
# ============================================================

ORDINARY = "ORDINARY_AVAILABLE"
NOTORIOUS_ONLY = "NOTORIOUS_ONLY"
SPECIAL_ONLY = "SPECIAL_ONLY"
NOTORIOUS_OR_SPECIAL = "NOTORIOUS_OR_SPECIAL_ONLY"
UNRESOLVED = "UNRESOLVED"

SOURCE_ORDINARY = "ORDINARY_SOURCE"
SOURCE_NOTORIOUS = "NOTORIOUS_SOURCE"
SOURCE_SPECIAL = "SPECIAL_SOURCE"
SOURCE_UNRESOLVED = "UNRESOLVED_SOURCE"

NOTORIOUS_MOBTYPE_BIT = 0x02


# ============================================================
# SQL annotation patterns
# ============================================================

ZONE_COMMENT_RE = re.compile(
    r"^\s*--\s*ZoneID:\s*(\d+)\s*-\s*(.+?)\s*$"
)

DROP_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+`?mob_droplist`?\s+VALUES\s*"
    r"\(\s*(\d+)\s*,",
    re.IGNORECASE,
)


# ============================================================
# Helpers
# ============================================================

class MobSourceBuildError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip()

    if text.lower() == "nan":
        return ""

    return text


def normalize_name(value: Any) -> str:
    text = clean_text(value).lower()

    return re.sub(
        r"[^a-z0-9]+",
        "",
        text,
    )


def pipe_int(values) -> str:
    return "|".join(
        str(value)
        for value in sorted(
            {
                int(value)
                for value in values
                if value is not None
            }
        )
    )


def pipe_text(values) -> str:
    return "|".join(
        sorted(
            {
                clean_text(value)
                for value in values
                if clean_text(value)
            }
        )
    )



def int_or_zero(value: Any) -> int:
    """
    Convert nullable pandas/database numeric values to int safely.

    pandas NaN does not behave like None with `value or 0`, so
    nullable DB fields must be checked explicitly before int().
    """

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
        raise MobSourceBuildError(
            f"Invalid YAML mapping in {CONFIG_FILE}"
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

        raise MobSourceBuildError(
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


# ============================================================
# Database evidence
# ============================================================

def load_database() -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    cfg = load_db_settings()

    connection = pymysql.connect(
        host=str(cfg["host"]),
        user=str(cfg["user"]),
        password=str(cfg["password"]),
        database=str(cfg["database"]),
        port=int(cfg["port"]),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )

    droplist_query = """
        SELECT
            dropId AS drop_id,
            dropType AS drop_type,
            groupId AS drop_group_id,
            groupRate AS group_rate,
            itemId AS itemid,
            itemRate AS item_rate
        FROM mob_droplist
        ORDER BY dropId, itemId
    """

    group_query = """
        SELECT
            g.groupid AS mob_group_id,
            g.poolid AS mob_pool_id,
            g.zoneid AS zone_id,
            g.name AS mob_group_name,
            g.respawntime AS respawn_time,
            g.spawntype AS spawn_type,
            g.dropid AS drop_id,
            g.content_tag AS content_tag,

            p.name AS mob_pool_name,
            p.packet_name AS packet_name,
            p.speciesid AS species_id,
            p.mobType AS mob_type,
            p.aggro AS aggro,
            p.true_detection AS true_detection,
            p.links AS links

        FROM mob_groups AS g

        LEFT JOIN mob_pools AS p
            ON p.poolid = g.poolid
    """

    direct_query = """
        SELECT
            d.itemId AS itemid,
            d.dropId AS drop_id,
            d.dropType AS drop_type,
            d.groupId AS drop_group_id,
            d.groupRate AS group_rate,
            d.itemRate AS item_rate,

            g.groupid AS mob_group_id,
            g.poolid AS mob_pool_id,
            g.zoneid AS zone_id,
            g.name AS mob_group_name,
            g.respawntime AS respawn_time,
            g.spawntype AS spawn_type,
            g.content_tag AS content_tag,

            p.name AS mob_pool_name,
            p.packet_name AS packet_name,
            p.speciesid AS species_id,
            p.mobType AS mob_type,
            p.aggro AS aggro,
            p.true_detection AS true_detection,
            p.links AS links

        FROM mob_droplist AS d

        LEFT JOIN mob_groups AS g
            ON g.dropid = d.dropId

        LEFT JOIN mob_pools AS p
            ON p.poolid = g.poolid

        ORDER BY
            d.itemId,
            d.dropId,
            g.zoneid,
            g.groupid
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                droplist_query
            )
            droplist = pd.DataFrame(
                cursor.fetchall()
            )

            cursor.execute(
                group_query
            )
            groups = pd.DataFrame(
                cursor.fetchall()
            )

            cursor.execute(
                direct_query
            )
            direct = pd.DataFrame(
                cursor.fetchall()
            )

    finally:
        connection.close()

    if droplist.empty:
        raise MobSourceBuildError(
            "mob_droplist returned no rows."
        )

    return (
        droplist,
        groups,
        direct,
    )


# ============================================================
# Source metadata
# ============================================================

def build_notorious_name_set(
    groups: pd.DataFrame,
) -> set[str]:
    names: set[str] = set()

    if groups.empty:
        return names

    mask = (
        groups[
            "mob_type"
        ]
        .fillna(0)
        .astype(int)
        .map(
            lambda value:
                bool(
                    value
                    & NOTORIOUS_MOBTYPE_BIT
                )
        )
    )

    notorious = groups[
        mask
    ]

    for column in [
        "mob_group_name",
        "mob_pool_name",
        "packet_name",
    ]:
        for value in notorious[
            column
        ].dropna():
            normalized = normalize_name(
                value
            )

            if normalized:
                names.add(
                    normalized
                )

    return names


def build_special_zone_set(
    groups: pd.DataFrame,
) -> set[int]:
    """
    Conservatively identify zones whose current mob_groups definitions are
    entirely dynamic/special-style groups.

    This is evidence only. It is not a market classification.
    """

    if groups.empty:
        return set()

    out: set[int] = set()

    for zone_id, frame in groups.groupby(
        "zone_id"
    ):
        if pd.isna(
            zone_id
        ):
            continue

        spawn_types = (
            frame[
                "spawn_type"
            ]
            .fillna(0)
            .astype(int)
        )

        respawns = (
            frame[
                "respawn_time"
            ]
            .fillna(0)
            .astype(int)
        )

        if (
            len(frame) > 0
            and (spawn_types == 128).all()
            and (respawns == 0).all()
        ):
            out.add(
                int(
                    zone_id
                )
            )

    return out


# ============================================================
# Parse mob_droplist.sql annotations
# ============================================================

def parse_drop_annotations() -> dict[
    int,
    list[dict[str, Any]],
]:
    if not DROP_SQL_FILE.exists():
        raise MobSourceBuildError(
            f"Missing LSB drop SQL: {DROP_SQL_FILE}"
        )

    text = DROP_SQL_FILE.read_text(
        encoding="utf-8",
        errors="replace",
    )

    pending: list[
        dict[str, Any]
    ] = []

    annotations: dict[
        int,
        list[dict[str, Any]],
    ] = defaultdict(
        list
    )

    assigned_dropids: set[int] = set()

    for line_number, line in enumerate(
        text.splitlines(),
        start=1,
    ):
        comment = ZONE_COMMENT_RE.match(
            line
        )

        if comment is not None:
            zone_id = int(
                comment.group(1)
            )

            remainder = (
                comment.group(2)
                .strip()
            )

            note = ""

            if " -- " in remainder:
                (
                    mob_name,
                    note,
                ) = remainder.split(
                    " -- ",
                    1,
                )

                mob_name = (
                    mob_name.strip()
                )

                note = (
                    note.strip()
                )

            else:
                mob_name = remainder

            pending.append(
                {
                    "zone_id":
                        zone_id,

                    "mob_name":
                        mob_name,

                    "annotation_note":
                        note,

                    "annotation_line":
                        line_number,
                }
            )

            continue

        insert = DROP_INSERT_RE.search(
            line
        )

        if insert is None:
            continue

        drop_id = int(
            insert.group(1)
        )

        # The comments immediately preceding the first INSERT for a
        # drop list document the mobs/zones sharing that drop ID.
        if (
            pending
            and drop_id
            not in assigned_dropids
        ):
            annotations[
                drop_id
            ].extend(
                pending
            )

            assigned_dropids.add(
                drop_id
            )

            pending = []

    return dict(
        annotations
    )


# ============================================================
# Classify individual evidence rows
# ============================================================

def classify_direct_source(
    row: pd.Series,
) -> tuple[
    str,
    str,
]:
    if pd.isna(
        row.get(
            "mob_group_id"
        )
    ):
        return (
            SOURCE_UNRESOLVED,
            "DROPID_NOT_RESOLVED_TO_MOB_GROUP",
        )

    mob_type = int_or_zero(
        row.get(
            "mob_type"
        )
    )

    spawn_type = int_or_zero(
        row.get(
            "spawn_type"
        )
    )

    respawn_time = int_or_zero(
        row.get(
            "respawn_time"
        )
    )

    if (
        mob_type
        & NOTORIOUS_MOBTYPE_BIT
    ):
        return (
            SOURCE_NOTORIOUS,
            "MOBTYPE_0x02",
        )

    reasons: list[str] = []

    if spawn_type != 0:
        reasons.append(
            f"SPAWNTYPE_{spawn_type}"
        )

    if respawn_time <= 0:
        reasons.append(
            "RESPAWN_0"
        )

    content_tag = clean_text(
        row.get(
            "content_tag"
        )
    )

    if content_tag:
        reasons.append(
            "CONTENT_TAG:"
            + content_tag
        )

    if reasons:
        return (
            SOURCE_SPECIAL,
            "|".join(
                reasons
            ),
        )

    return (
        SOURCE_ORDINARY,
        "STANDARD_DB_WORLD_SOURCE",
    )


def classify_annotation_source(
    *,
    mob_name: str,
    zone_id: int,
    note: str,
    notorious_names: set[str],
    special_zones: set[int],
) -> tuple[
    str,
    str,
]:
    normalized = normalize_name(
        mob_name
    )

    note_upper = note.upper()

    if (
        " NM" in f" {note_upper}"
        or note_upper.endswith(
            "NM"
        )
        or "NOTORIOUS" in note_upper
        or normalized
        in notorious_names
    ):
        return (
            SOURCE_NOTORIOUS,
            "SQL_ANNOTATION_NOTORIOUS_EVIDENCE",
        )

    if zone_id in special_zones:
        return (
            SOURCE_SPECIAL,
            "SQL_ANNOTATION_SPECIAL_ZONE",
        )

    return (
        SOURCE_ORDINARY,
        "SQL_ANNOTATION_WORLD_SOURCE",
    )


# ============================================================
# Build source report
# ============================================================

def build_source_report(
    droplist: pd.DataFrame,
    groups: pd.DataFrame,
    direct: pd.DataFrame,
) -> pd.DataFrame:
    notorious_names = (
        build_notorious_name_set(
            groups
        )
    )

    special_zones = (
        build_special_zone_set(
            groups
        )
    )

    annotations = (
        parse_drop_annotations()
    )

    rows: list[dict] = []

    # --------------------------------------------------------
    # Direct DB-linked evidence
    # --------------------------------------------------------

    for _, row in direct.iterrows():
        (
            source_class,
            source_reason,
        ) = classify_direct_source(
            row
        )

        rows.append(
            {
                "itemid":
                    int(
                        row[
                            "itemid"
                        ]
                    ),

                "drop_id":
                    int(
                        row[
                            "drop_id"
                        ]
                    ),

                "source_origin":
                    "DB_GROUP",

                "source_class":
                    source_class,

                "source_reason":
                    source_reason,

                "zone_id":
                    (
                        None
                        if pd.isna(
                            row.get(
                                "zone_id"
                            )
                        )
                        else int(
                            row[
                                "zone_id"
                            ]
                        )
                    ),

                "mob_name":
                    clean_text(
                        row.get(
                            "mob_group_name"
                        )
                    ),

                "mob_pool_name":
                    clean_text(
                        row.get(
                            "mob_pool_name"
                        )
                    ),

                "mob_group_id":
                    (
                        None
                        if pd.isna(
                            row.get(
                                "mob_group_id"
                            )
                        )
                        else int(
                            row[
                                "mob_group_id"
                            ]
                        )
                    ),

                "mob_pool_id":
                    (
                        None
                        if pd.isna(
                            row.get(
                                "mob_pool_id"
                            )
                        )
                        else int(
                            row[
                                "mob_pool_id"
                            ]
                        )
                    ),

                "spawn_type":
                    (
                        None
                        if pd.isna(
                            row.get(
                                "spawn_type"
                            )
                        )
                        else int(
                            row[
                                "spawn_type"
                            ]
                        )
                    ),

                "mob_type":
                    (
                        None
                        if pd.isna(
                            row.get(
                                "mob_type"
                            )
                        )
                        else int(
                            row[
                                "mob_type"
                            ]
                        )
                    ),

                "respawn_time":
                    (
                        None
                        if pd.isna(
                            row.get(
                                "respawn_time"
                            )
                        )
                        else int(
                            row[
                                "respawn_time"
                            ]
                        )
                    ),

                "content_tag":
                    clean_text(
                        row.get(
                            "content_tag"
                        )
                    ),

                "annotation_note":
                    "",

                "annotation_line":
                    None,

                "drop_type":
                    int(
                        row[
                            "drop_type"
                        ]
                    ),

                "group_rate":
                    int(
                        row[
                            "group_rate"
                        ]
                    ),

                "item_rate":
                    int(
                        row[
                            "item_rate"
                        ]
                    ),
            }
        )

    # --------------------------------------------------------
    # SQL comment annotation evidence
    # --------------------------------------------------------

    drop_items: dict[
        int,
        list[dict[str, Any]],
    ] = defaultdict(
        list
    )

    for _, row in droplist.iterrows():
        drop_items[
            int(
                row[
                    "drop_id"
                ]
            )
        ].append(
            {
                "itemid":
                    int(
                        row[
                            "itemid"
                        ]
                    ),

                "drop_type":
                    int(
                        row[
                            "drop_type"
                        ]
                    ),

                "group_rate":
                    int(
                        row[
                            "group_rate"
                        ]
                    ),

                "item_rate":
                    int(
                        row[
                            "item_rate"
                        ]
                    ),
            }
        )

    for (
        drop_id,
        source_annotations,
    ) in annotations.items():
        for annotation in (
            source_annotations
        ):
            (
                source_class,
                source_reason,
            ) = classify_annotation_source(
                mob_name=annotation[
                    "mob_name"
                ],
                zone_id=int(
                    annotation[
                        "zone_id"
                    ]
                ),
                note=annotation[
                    "annotation_note"
                ],
                notorious_names=
                    notorious_names,
                special_zones=
                    special_zones,
            )

            for item in drop_items.get(
                drop_id,
                []
            ):
                rows.append(
                    {
                        "itemid":
                            item[
                                "itemid"
                            ],

                        "drop_id":
                            drop_id,

                        "source_origin":
                            "SQL_COMMENT",

                        "source_class":
                            source_class,

                        "source_reason":
                            source_reason,

                        "zone_id":
                            int(
                                annotation[
                                    "zone_id"
                                ]
                            ),

                        "mob_name":
                            annotation[
                                "mob_name"
                            ],

                        "mob_pool_name":
                            "",

                        "mob_group_id":
                            None,

                        "mob_pool_id":
                            None,

                        "spawn_type":
                            None,

                        "mob_type":
                            None,

                        "respawn_time":
                            None,

                        "content_tag":
                            "",

                        "annotation_note":
                            annotation[
                                "annotation_note"
                            ],

                        "annotation_line":
                            annotation[
                                "annotation_line"
                            ],

                        "drop_type":
                            item[
                                "drop_type"
                            ],

                        "group_rate":
                            item[
                                "group_rate"
                            ],

                        "item_rate":
                            item[
                                "item_rate"
                            ],
                    }
                )

    report = pd.DataFrame(
        rows
    )

    if report.empty:
        raise MobSourceBuildError(
            "No mob source evidence was generated."
        )

    return report


# ============================================================
# Aggregate to item-level acquisition evidence
# ============================================================

def aggregate_items(
    provenance: pd.DataFrame,
    sources: pd.DataFrame,
) -> pd.DataFrame:
    mob_items = provenance[
        provenance[
            "mob_drop"
        ].astype(int)
        == 1
    ][
        [
            "itemid",
            "name",
        ]
    ].copy()

    grouped = defaultdict(
        lambda: {
            "classes": [],
            "origins": [],
            "drop_ids": [],
            "zone_ids": [],
            "mob_names": [],
            "reasons": [],
            "source_rows": 0,
        }
    )

    for _, row in sources.iterrows():
        itemid = int(
            row[
                "itemid"
            ]
        )

        info = grouped[
            itemid
        ]

        info[
            "source_rows"
        ] += 1

        info[
            "classes"
        ].append(
            row[
                "source_class"
            ]
        )

        info[
            "origins"
        ].append(
            row[
                "source_origin"
            ]
        )

        info[
            "drop_ids"
        ].append(
            int(
                row[
                    "drop_id"
                ]
            )
        )

        if pd.notna(
            row.get(
                "zone_id"
            )
        ):
            info[
                "zone_ids"
            ].append(
                int(
                    row[
                        "zone_id"
                    ]
                )
            )

        mob_name = clean_text(
            row.get(
                "mob_name"
            )
        )

        if mob_name:
            info[
                "mob_names"
            ].append(
                mob_name
            )

        reason = clean_text(
            row.get(
                "source_reason"
            )
        )

        if reason:
            info[
                "reasons"
            ].append(
                reason
            )

    name_lookup = (
        mob_items
        .set_index(
            "itemid"
        )[
            "name"
        ]
        .to_dict()
    )

    rows: list[dict] = []

    for itemid in sorted(
        mob_items[
            "itemid"
        ].astype(int)
    ):
        info = grouped.get(
            itemid
        )

        if info is None:
            rows.append(
                {
                    "itemid":
                        itemid,

                    "name":
                        name_lookup.get(
                            itemid,
                            "",
                        ),

                    "mob_source_class":
                        UNRESOLVED,

                    "has_ordinary_source":
                        0,

                    "has_notorious_source":
                        0,

                    "has_special_source":
                        0,

                    "has_unresolved_source":
                        1,

                    "ordinary_source_count":
                        0,

                    "notorious_source_count":
                        0,

                    "special_source_count":
                        0,

                    "unresolved_source_count":
                        0,

                    "source_row_count":
                        0,

                    "source_origins":
                        "",

                    "drop_ids":
                        "",

                    "zone_ids":
                        "",

                    "mob_names":
                        "",

                    "evidence_reasons":
                        "NO_SOURCE_EVIDENCE",

                    "auto_market_promotion_allowed":
                        0,

                    "review_required":
                        1,
                }
            )

            continue

        classes = info[
            "classes"
        ]

        ordinary_count = classes.count(
            SOURCE_ORDINARY
        )

        notorious_count = classes.count(
            SOURCE_NOTORIOUS
        )

        special_count = classes.count(
            SOURCE_SPECIAL
        )

        unresolved_count = classes.count(
            SOURCE_UNRESOLVED
        )

        has_ordinary = ordinary_count > 0
        has_notorious = notorious_count > 0
        has_special = special_count > 0

        if has_ordinary:
            item_class = ORDINARY

        elif (
            has_notorious
            and has_special
        ):
            item_class = (
                NOTORIOUS_OR_SPECIAL
            )

        elif has_notorious:
            item_class = (
                NOTORIOUS_ONLY
            )

        elif has_special:
            item_class = (
                SPECIAL_ONLY
            )

        else:
            item_class = (
                UNRESOLVED
            )

        rows.append(
            {
                "itemid":
                    itemid,

                "name":
                    name_lookup.get(
                        itemid,
                        "",
                    ),

                "mob_source_class":
                    item_class,

                "has_ordinary_source":
                    int(
                        has_ordinary
                    ),

                "has_notorious_source":
                    int(
                        has_notorious
                    ),

                "has_special_source":
                    int(
                        has_special
                    ),

                "has_unresolved_source":
                    int(
                        unresolved_count
                        > 0
                    ),

                "ordinary_source_count":
                    ordinary_count,

                "notorious_source_count":
                    notorious_count,

                "special_source_count":
                    special_count,

                "unresolved_source_count":
                    unresolved_count,

                "source_row_count":
                    int(
                        info[
                            "source_rows"
                        ]
                    ),

                "source_origins":
                    pipe_text(
                        info[
                            "origins"
                        ]
                    ),

                "drop_ids":
                    pipe_int(
                        info[
                            "drop_ids"
                        ]
                    ),

                "zone_ids":
                    pipe_int(
                        info[
                            "zone_ids"
                        ]
                    ),

                "mob_names":
                    pipe_text(
                        info[
                            "mob_names"
                        ]
                    ),

                "evidence_reasons":
                    pipe_text(
                        info[
                            "reasons"
                        ]
                    ),

                # Evidence only. No market expansion in this phase.
                "auto_market_promotion_allowed":
                    0,

                "review_required":
                    int(
                        item_class
                        != ORDINARY
                    ),
            }
        )

    output = pd.DataFrame(
        rows
    )

    if len(
        output
    ) != len(
        mob_items
    ):
        raise MobSourceBuildError(
            "Item-level mob source output does not "
            "match the mob-drop item count."
        )

    return output


# ============================================================
# Main
# ============================================================

def main() -> int:
    GENERATED.mkdir(
        parents=True,
        exist_ok=True,
    )

    REPORTS.mkdir(
        parents=True,
        exist_ok=True,
    )

    if not ITEM_PROVENANCE_FILE.exists():
        raise MobSourceBuildError(
            f"Missing provenance index: {ITEM_PROVENANCE_FILE}"
        )

    provenance = pd.read_csv(
        ITEM_PROVENANCE_FILE,
        low_memory=False,
    )

    required = {
        "itemid",
        "name",
        "mob_drop",
    }

    missing = sorted(
        required
        - set(
            provenance.columns
        )
    )

    if missing:
        raise MobSourceBuildError(
            "item-provenance.csv missing columns: "
            + ", ".join(
                missing
            )
        )

    (
        droplist,
        groups,
        direct,
    ) = load_database()

    sources = build_source_report(
        droplist,
        groups,
        direct,
    )

    output = aggregate_items(
        provenance,
        sources,
    )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    sources.to_csv(
        SOURCE_REPORT,
        index=False,
    )

    class_counts = (
        output[
            "mob_source_class"
        ]
        .value_counts()
        .to_dict()
    )

    origin_counts = (
        sources[
            "source_origin"
        ]
        .value_counts()
        .to_dict()
    )

    summary = {
        "status":
            "PASS",

        "mob_drop_items":
            int(
                len(
                    output
                )
            ),

        "source_rows":
            int(
                len(
                    sources
                )
            ),

        "source_origins":
            {
                str(key):
                    int(value)
                for key, value
                in origin_counts.items()
            },

        "classes":
            {
                str(key):
                    int(value)
                for key, value
                in class_counts.items()
            },

        "items_with_ordinary_source":
            int(
                output[
                    "has_ordinary_source"
                ].sum()
            ),

        "items_with_notorious_source":
            int(
                output[
                    "has_notorious_source"
                ].sum()
            ),

        "items_with_special_source":
            int(
                output[
                    "has_special_source"
                ].sum()
            ),

        "auto_market_promotions":
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
        " Mob Acquisition Source Index"
    )
    print(
        "======================================"
    )

    print(
        f"Mob-drop items:              "
        f"{summary['mob_drop_items']:>6}"
    )

    print(
        f"Source evidence rows:        "
        f"{summary['source_rows']:>6}"
    )

    print()
    print(
        "Item acquisition classes:"
    )

    for label in [
        ORDINARY,
        NOTORIOUS_ONLY,
        SPECIAL_ONLY,
        NOTORIOUS_OR_SPECIAL,
        UNRESOLVED,
    ]:
        print(
            f"  {label:<30} "
            f"{summary['classes'].get(label, 0):>6}"
        )

    print()
    print(
        f"Items with ordinary source:  "
        f"{summary['items_with_ordinary_source']:>6}"
    )

    print(
        f"Items with notorious source: "
        f"{summary['items_with_notorious_source']:>6}"
    )

    print(
        f"Items with special source:   "
        f"{summary['items_with_special_source']:>6}"
    )

    print()
    print(
        "Auto market promotions: 0"
    )

    print()
    print(
        f"Generated: {OUTPUT_FILE}"
    )

    print(
        f"Sources:   {SOURCE_REPORT}"
    )

    print(
        f"Summary:   {SUMMARY_FILE}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
