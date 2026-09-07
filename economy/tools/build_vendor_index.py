from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path


# ============================================================
# Paths
# ============================================================

LSB_ROOT = Path.home() / "server"
SCRIPTS_ROOT = LSB_ROOT / "scripts"
ITEM_ENUM = SCRIPTS_ROOT / "enum" / "item.lua"

AHBOT_ROOT = Path.home() / "ffxiahbot"
GENERATED = AHBOT_ROOT / "economy" / "generated"
REPORTS = AHBOT_ROOT / "economy" / "reports"

VENDOR_ITEMS_FILE = GENERATED / "vendor-items.csv"
VENDOR_PRICES_FILE = GENERATED / "vendor-prices.csv"
VENDOR_SOURCES_FILE = REPORTS / "vendor-price-sources.csv"
UNRESOLVED_FILE = REPORTS / "vendor-price-unresolved.csv"


# ============================================================
# Patterns
# ============================================================

ITEM_REF_RE = re.compile(
    r"\bxi\.item\.([A-Z0-9_]+)\b"
)

ITEM_ENUM_RE = re.compile(
    r"^\s*([A-Z0-9_]+)\s*=\s*(\d+)\s*,?\s*$",
    re.MULTILINE,
)

TABLE_ASSIGN_RE = re.compile(
    r"(?P<prefix>\blocal\s+)?"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_\.]*)"
    r"\s*=\s*\{"
)

SHOP_CALL_RE = re.compile(
    r"\bxi\.shop\."
    r"(?P<kind>generalGuild|curioVendorMoogle|nation|general)"
    r"\s*\("
)

DIRECT_ADD_RE = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*"
    r"\s*:\s*addShopItem\s*\("
)

NUMERIC_LITERAL_RE = re.compile(
    r"^[+]?(?:\d+(?:\.\d*)?|\.\d+)$"
)

SHOP_MARKERS = (
    "xi.shop.",
    "addShopItem(",
    "createShop(",
)


# ============================================================
# Data structures
# ============================================================

@dataclass(frozen=True)
class TableAssignment:
    name: str
    start: int
    open_brace: int
    end: int
    body: str


@dataclass(frozen=True)
class VendorSource:
    itemid: int
    name: str
    scripted_price: int | None
    effective_min_price: int | None
    effective_max_price: int | None
    shop_type: str
    price_mode: str
    hard_floor_eligible: bool
    gated: bool
    source_file: str
    source_line: int
    stock_name: str
    notes: str


# ============================================================
# Lua parsing helpers
# ============================================================

def strip_lua_comments(text: str) -> str:
    """
    Replace Lua comments with spaces while preserving line numbers.
    """

    chars = list(text)
    i = 0
    n = len(chars)

    while i < n:
        if chars[i] in {"'", '"'}:
            quote = chars[i]
            i += 1

            while i < n:
                if chars[i] == "\\":
                    i += 2
                    continue

                if chars[i] == quote:
                    i += 1
                    break

                i += 1

            continue

        if (
            i + 3 < n
            and "".join(chars[i:i + 4]) == "--[["
        ):
            j = i + 4

            while (
                j + 1 < n
                and "".join(chars[j:j + 2]) != "]]"
            ):
                j += 1

            j = min(
                n,
                j + 2,
            )

            for k in range(i, j):
                if chars[k] != "\n":
                    chars[k] = " "

            i = j
            continue

        if (
            i + 1 < n
            and chars[i] == "-"
            and chars[i + 1] == "-"
        ):
            j = i

            while (
                j < n
                and chars[j] != "\n"
            ):
                chars[j] = " "
                j += 1

            i = j
            continue

        i += 1

    return "".join(chars)


def find_balanced(
    text: str,
    open_index: int,
    open_char: str,
    close_char: str,
) -> int | None:
    if (
        open_index >= len(text)
        or text[open_index] != open_char
    ):
        return None

    depth = 0
    quote = None
    i = open_index

    while i < len(text):
        ch = text[i]

        if quote is not None:
            if ch == "\\":
                i += 2
                continue

            if ch == quote:
                quote = None

            i += 1
            continue

        if ch in {"'", '"'}:
            quote = ch

        elif ch == open_char:
            depth += 1

        elif ch == close_char:
            depth -= 1

            if depth == 0:
                return i

        i += 1

    return None


def split_top_level(text: str) -> list[str]:
    out: list[str] = []

    start = 0
    round_depth = 0
    brace_depth = 0
    square_depth = 0
    quote = None
    i = 0

    while i < len(text):
        ch = text[i]

        if quote is not None:
            if ch == "\\":
                i += 2
                continue

            if ch == quote:
                quote = None

            i += 1
            continue

        if ch in {"'", '"'}:
            quote = ch

        elif ch == "(":
            round_depth += 1

        elif ch == ")":
            round_depth = max(
                0,
                round_depth - 1,
            )

        elif ch == "{":
            brace_depth += 1

        elif ch == "}":
            brace_depth = max(
                0,
                brace_depth - 1,
            )

        elif ch == "[":
            square_depth += 1

        elif ch == "]":
            square_depth = max(
                0,
                square_depth - 1,
            )

        elif (
            ch == ","
            and round_depth == 0
            and brace_depth == 0
            and square_depth == 0
        ):
            out.append(
                text[start:i].strip()
            )
            start = i + 1

        i += 1

    out.append(
        text[start:].strip()
    )

    return out


def line_no(
    text: str,
    index: int,
) -> int:
    return text.count(
        "\n",
        0,
        index,
    ) + 1


def numeric(
    expr: str,
) -> int | None:
    expr = expr.strip()

    if not NUMERIC_LITERAL_RE.fullmatch(
        expr
    ):
        return None

    return int(
        float(expr)
    )


def item_const(
    expr: str,
) -> str | None:
    match = re.fullmatch(
        r"\s*xi\.item\.([A-Z0-9_]+)\s*",
        expr,
    )

    if match is None:
        return None

    return match.group(1)


# ============================================================
# LSB settings / item enum
# ============================================================

def load_items() -> dict[str, int]:
    text = ITEM_ENUM.read_text(
        encoding="utf-8",
        errors="replace",
    )

    out = {
        name: int(itemid)
        for name, itemid
        in ITEM_ENUM_RE.findall(text)
    }

    if not out:
        raise RuntimeError(
            f"Could not parse {ITEM_ENUM}"
        )

    return out


def load_shop_price() -> tuple[
    float,
    str,
]:
    pattern = re.compile(
        r"\bSHOP_PRICE\s*=\s*"
        r"([0-9]+(?:\.[0-9]+)?)"
    )

    candidates = (
        LSB_ROOT
        / "settings"
        / "main.lua",

        LSB_ROOT
        / "settings"
        / "default"
        / "main.lua",
    )

    for path in candidates:
        if not path.exists():
            continue

        text = strip_lua_comments(
            path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        )

        match = pattern.search(text)

        if match is None:
            continue

        value = float(
            match.group(1)
        )

        if value <= 0:
            raise RuntimeError(
                f"Invalid SHOP_PRICE={value} "
                f"in {path}"
            )

        return (
            value,
            str(
                path.relative_to(
                    LSB_ROOT
                )
            ),
        )

    return (
        1.0,
        "fallback:1.0",
    )


# ============================================================
# Price interpretation
# ============================================================

def effective_range(
    price: int,
    mode: str,
    shop_price: float,
) -> tuple[int, int]:
    """
    Return the minimum/maximum effective gil price.

    LSB general/nation shops with fame pricing use ranks 1-21:
      rank 1  -> 110% of scripted price
      rank 21 ->  90% of scripted price

    SHOP_PRICE is applied to fame-aware general/nation shops.
    """

    if mode != "FAME":
        return (
            price,
            price,
        )

    def effective_for_rank(
        rank: int,
    ) -> int:
        fame_adjusted = max(
            1,
            math.floor(
                price
                * (111 - rank)
                / 100
            ),
        )

        return math.floor(
            fame_adjusted
            * shop_price
        )

    return (
        effective_for_rank(21),
        effective_for_rank(1),
    )


# ============================================================
# Table / stock parsing
# ============================================================

def assignments(
    text: str,
) -> list[TableAssignment]:
    out: list[TableAssignment] = []

    for match in TABLE_ASSIGN_RE.finditer(
        text
    ):
        open_brace = text.find(
            "{",
            match.start(),
            match.end(),
        )

        close_brace = find_balanced(
            text,
            open_brace,
            "{",
            "}",
        )

        if close_brace is None:
            continue

        out.append(
            TableAssignment(
                name=match.group(
                    "name"
                ),
                start=match.start(),
                open_brace=open_brace,
                end=close_brace + 1,
                body=text[
                    open_brace + 1:
                    close_brace
                ],
            )
        )

    return out


def nearest(
    tables: list[TableAssignment],
    name: str,
    before: int,
) -> TableAssignment | None:
    matches = [
        table
        for table in tables
        if (
            table.name == name
            and table.start < before
        )
    ]

    if not matches:
        return None

    return max(
        matches,
        key=lambda table:
            table.start,
    )


def stock_entries(
    body: str,
    offset: int,
) -> list[
    tuple[
        str,
        int | None,
        int,
    ]
]:
    out: list[
        tuple[
            str,
            int | None,
            int,
        ]
    ] = []

    i = 0

    while True:
        match = ITEM_REF_RE.search(
            body,
            i,
        )

        if match is None:
            break

        open_brace = body.rfind(
            "{",
            0,
            match.start(),
        )

        if open_brace < 0:
            i = match.end()
            continue

        close_brace = find_balanced(
            body,
            open_brace,
            "{",
            "}",
        )

        if (
            close_brace is None
            or not (
                open_brace
                <= match.start()
                <= close_brace
            )
        ):
            i = match.end()
            continue

        fields = split_top_level(
            body[
                open_brace + 1:
                close_brace
            ]
        )

        if len(fields) >= 2:
            const = item_const(
                fields[0]
            )

            if const is not None:
                out.append(
                    (
                        const,
                        numeric(
                            fields[1]
                        ),
                        offset
                        + open_brace,
                    )
                )

        i = close_brace + 1

    return out


# ============================================================
# Vendor source construction
# ============================================================

def make_source(
    const: str,
    item_map: dict[str, int],
    price: int | None,
    shop_type: str,
    mode: str,
    gated: bool,
    path: Path,
    text: str,
    index: int,
    stock_name: str,
    shop_price: float,
    notes: str = "",
) -> VendorSource | None:
    itemid = item_map.get(
        const
    )

    if itemid is None:
        return None

    effective_min = None
    effective_max = None

    if price is not None:
        (
            effective_min,
            effective_max,
        ) = effective_range(
            price,
            mode,
            shop_price,
        )

    return VendorSource(
        itemid=itemid,
        name=const,
        scripted_price=price,
        effective_min_price=
            effective_min,
        effective_max_price=
            effective_max,
        shop_type=shop_type,
        price_mode=mode,
        hard_floor_eligible=(
            price is not None
            and effective_min
            is not None
            and effective_min > 0
        ),
        gated=gated,
        source_file=str(
            path.relative_to(
                LSB_ROOT
            )
        ),
        source_line=line_no(
            text,
            index,
        ),
        stock_name=stock_name,
        notes=notes,
    )


def classify(
    kind: str,
    args: list[str],
) -> tuple[
    str,
    str,
    bool,
]:
    if kind == "nation":
        return (
            "NATION",
            "FAME",
            True,
        )

    if kind == "generalGuild":
        return (
            "GENERAL_GUILD",
            "FIXED",
            True,
        )

    if kind == "curioVendorMoogle":
        return (
            "CURIO",
            "FIXED",
            True,
        )

    has_fame_area = (
        len(args) >= 3
        and args[2].strip()
        not in {
            "",
            "nil",
        }
    )

    if has_fame_area:
        return (
            "GENERAL_FAME",
            "FAME",
            False,
        )

    return (
        "GENERAL",
        "FIXED",
        False,
    )


# ============================================================
# Detailed price-source scanners
# ============================================================

def scan_calls(
    path: Path,
    text: str,
    item_map: dict[str, int],
    shop_price: float,
) -> list[VendorSource]:
    out: list[VendorSource] = []
    tables = assignments(text)

    for match in SHOP_CALL_RE.finditer(
        text
    ):
        kind = match.group(
            "kind"
        )

        open_paren = text.find(
            "(",
            match.start(),
            match.end(),
        )

        close_paren = find_balanced(
            text,
            open_paren,
            "(",
            ")",
        )

        if close_paren is None:
            continue

        args = split_top_level(
            text[
                open_paren + 1:
                close_paren
            ]
        )

        if len(args) < 2:
            continue

        (
            shop_type,
            mode,
            gated,
        ) = classify(
            kind,
            args,
        )

        stock_expr = args[1].strip()
        entries: list[
            tuple[
                str,
                int | None,
                int,
            ]
        ] = []

        stock_name = stock_expr

        if stock_expr.startswith(
            "{"
        ):
            body_open = text.find(
                "{",
                open_paren,
                close_paren + 1,
            )

            body_close = (
                find_balanced(
                    text,
                    body_open,
                    "{",
                    "}",
                )
                if body_open >= 0
                else None
            )

            if (
                body_close is not None
                and body_close
                <= close_paren
            ):
                entries = stock_entries(
                    text[
                        body_open + 1:
                        body_close
                    ],
                    body_open + 1,
                )

                stock_name = "<inline>"

        elif re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_\.]*",
            stock_expr,
        ):
            table = nearest(
                tables,
                stock_expr,
                match.start(),
            )

            if table is not None:
                entries = stock_entries(
                    table.body,
                    table.open_brace + 1,
                )

        for (
            const,
            price,
            index,
        ) in entries:
            source = make_source(
                const=const,
                item_map=item_map,
                price=price,
                shop_type=shop_type,
                mode=mode,
                gated=gated,
                path=path,
                text=text,
                index=index,
                stock_name=
                    stock_name,
                shop_price=
                    shop_price,
            )

            if source is not None:
                out.append(
                    source
                )

    return out


def scan_direct(
    path: Path,
    text: str,
    item_map: dict[str, int],
    shop_price: float,
) -> list[VendorSource]:
    out: list[VendorSource] = []

    for match in DIRECT_ADD_RE.finditer(
        text
    ):
        open_paren = text.find(
            "(",
            match.start(),
            match.end(),
        )

        close_paren = find_balanced(
            text,
            open_paren,
            "(",
            ")",
        )

        if close_paren is None:
            continue

        args = split_top_level(
            text[
                open_paren + 1:
                close_paren
            ]
        )

        if len(args) < 2:
            continue

        const = item_const(
            args[0]
        )

        if const is None:
            continue

        price = numeric(
            args[1]
        )

        notes = (
            ""
            if price is not None
            else
            "non-literal addShopItem price"
        )

        source = make_source(
            const=const,
            item_map=item_map,
            price=price,
            shop_type="DIRECT_ADD",
            mode="FIXED",
            gated=False,
            path=path,
            text=text,
            index=match.start(),
            stock_name="<direct>",
            shop_price=shop_price,
            notes=notes,
        )

        if source is not None:
            out.append(
                source
            )

    return out


def scan_global_stock(
    path: Path,
    text: str,
    item_map: dict[str, int],
    shop_price: float,
) -> list[VendorSource]:
    if (
        path
        != SCRIPTS_ROOT
        / "globals"
        / "shop.lua"
    ):
        return []

    out: list[VendorSource] = []

    for table in assignments(
        text
    ):
        if not (
            table.name.startswith(
                "xi.shop."
            )
            and "stock"
            in table.name.lower()
        ):
            continue

        for (
            const,
            price,
            index,
        ) in stock_entries(
            table.body,
            table.open_brace + 1,
        ):
            source = make_source(
                const=const,
                item_map=item_map,
                price=price,
                shop_type=
                    "GLOBAL_SHOP_STOCK",
                mode="FIXED",
                gated=True,
                path=path,
                text=text,
                index=index,
                stock_name=
                    table.name,
                shop_price=
                    shop_price,
                notes=(
                    "global xi.shop stock table; "
                    "availability may be gated"
                ),
            )

            if source is not None:
                out.append(
                    source
                )

    return out


# ============================================================
# Conservative vendor-presence index
# ============================================================

def shop_files() -> list[Path]:
    out: list[Path] = []

    for path in SCRIPTS_ROOT.rglob(
        "*.lua"
    ):
        raw = path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        if any(
            marker in raw
            for marker
            in SHOP_MARKERS
        ):
            out.append(
                path
            )

    return sorted(
        out
    )


def presence(
    paths: list[Path],
    item_map: dict[str, int],
) -> tuple[
    dict[int, set[str]],
    dict[int, set[str]],
    set[str],
]:
    """
    Preserve the original conservative vendor-index semantics.

    Any xi.item.* reference in a shop-related Lua file marks the
    item as vendor-associated.

    Returns:
      constants_by_itemid
      source_files_by_itemid
      unresolved_constants
    """

    constants_by_itemid: dict[
        int,
        set[str],
    ] = {}

    files_by_itemid: dict[
        int,
        set[str],
    ] = {}

    unresolved: set[str] = set()

    for path in paths:
        text = strip_lua_comments(
            path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        )

        relative = str(
            path.relative_to(
                LSB_ROOT
            )
        )

        # Set() keeps source_count tied to source files rather
        # than repeated references within a single Lua file.
        for const in set(
            ITEM_REF_RE.findall(
                text
            )
        ):
            itemid = item_map.get(
                const
            )

            if itemid is None:
                unresolved.add(
                    const
                )
                continue

            constants_by_itemid.setdefault(
                itemid,
                set(),
            ).add(
                const
            )

            files_by_itemid.setdefault(
                itemid,
                set(),
            ).add(
                relative
            )

    return (
        constants_by_itemid,
        files_by_itemid,
        unresolved,
    )


# ============================================================
# Source deduplication
# ============================================================

def dedupe(
    sources: list[VendorSource],
) -> list[VendorSource]:
    seen: set[tuple] = set()
    out: list[VendorSource] = []

    for source in sources:
        key = (
            source.itemid,
            source.scripted_price,
            source.effective_min_price,
            source.effective_max_price,
            source.shop_type,
            source.price_mode,
            source.source_file,
            source.source_line,
            source.stock_name,
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        out.append(
            source
        )

    return sorted(
        out,
        key=lambda source: (
            source.itemid,
            (
                source.effective_min_price
                if source.effective_min_price
                is not None
                else 2**63
            ),
            source.source_file,
            source.source_line,
        ),
    )


# ============================================================
# Writers
# ============================================================

def write_legacy_vendor_items(
    constants_by_itemid: dict[
        int,
        set[str],
    ],
    files_by_itemid: dict[
        int,
        set[str],
    ],
) -> None:
    """
    Preserve the original vendor-items.csv contract:

      itemid,constants,source_count,sources

    This file remains the backward-compatible conservative
    presence index consumed by existing economy tooling.
    """

    with VENDOR_ITEMS_FILE.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.writer(
            file
        )

        writer.writerow(
            [
                "itemid",
                "constants",
                "source_count",
                "sources",
            ]
        )

        for itemid in sorted(
            constants_by_itemid
        ):
            constants = sorted(
                constants_by_itemid[
                    itemid
                ]
            )

            sources = sorted(
                files_by_itemid.get(
                    itemid,
                    set(),
                )
            )

            writer.writerow(
                [
                    itemid,
                    "|".join(
                        constants
                    ),
                    len(
                        sources
                    ),
                    "|".join(
                        sources
                    ),
                ]
            )


def canonical_name(
    itemid: int,
    constants_by_itemid: dict[
        int,
        set[str],
    ],
) -> str:
    constants = sorted(
        constants_by_itemid.get(
            itemid,
            set(),
        )
    )

    if constants:
        return constants[0]

    return str(
        itemid
    )


def write_vendor_sources(
    sources: list[VendorSource],
) -> None:
    fields = [
        "itemid",
        "name",
        "scripted_price",
        "effective_min_price",
        "effective_max_price",
        "shop_type",
        "price_mode",
        "hard_floor_eligible",
        "gated",
        "source_file",
        "source_line",
        "stock_name",
        "notes",
    ]

    with VENDOR_SOURCES_FILE.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fields,
        )

        writer.writeheader()

        for source in sources:
            row = dict(
                source.__dict__
            )

            row[
                "scripted_price"
            ] = (
                ""
                if source.scripted_price
                is None
                else source.scripted_price
            )

            row[
                "effective_min_price"
            ] = (
                ""
                if source.effective_min_price
                is None
                else source.effective_min_price
            )

            row[
                "effective_max_price"
            ] = (
                ""
                if source.effective_max_price
                is None
                else source.effective_max_price
            )

            row[
                "hard_floor_eligible"
            ] = int(
                source.hard_floor_eligible
            )

            row[
                "gated"
            ] = int(
                source.gated
            )

            writer.writerow(
                row
            )


def write_vendor_prices(
    constants_by_itemid: dict[
        int,
        set[str],
    ],
    files_by_itemid: dict[
        int,
        set[str],
    ],
    sources: list[VendorSource],
) -> set[int]:
    fields = [
        "itemid",
        "name",
        "vendor_price_min",
        "vendor_price_max",
        "has_hard_floor",
        "priced_source_count",
        "vendor_source_count",
        "gated_source_count",
        "source_types",
        "source_files",
    ]

    by_item: dict[
        int,
        list[VendorSource],
    ] = {}

    for source in sources:
        by_item.setdefault(
            source.itemid,
            [],
        ).append(
            source
        )

    priced_ids: set[int] = set()

    with VENDOR_PRICES_FILE.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fields,
        )

        writer.writeheader()

        for itemid in sorted(
            constants_by_itemid
        ):
            item_sources = by_item.get(
                itemid,
                [],
            )

            priced_sources = [
                source
                for source
                in item_sources
                if source.hard_floor_eligible
            ]

            mins = [
                source.effective_min_price
                for source
                in priced_sources
                if source.effective_min_price
                is not None
            ]

            maxs = [
                source.effective_max_price
                for source
                in priced_sources
                if source.effective_max_price
                is not None
            ]

            if mins:
                priced_ids.add(
                    itemid
                )

            writer.writerow(
                {
                    "itemid":
                        itemid,

                    "name":
                        canonical_name(
                            itemid,
                            constants_by_itemid,
                        ),

                    "vendor_price_min":
                        min(mins)
                        if mins
                        else "",

                    "vendor_price_max":
                        max(maxs)
                        if maxs
                        else "",

                    "has_hard_floor":
                        int(
                            bool(mins)
                        ),

                    "priced_source_count":
                        len(
                            priced_sources
                        ),

                    "vendor_source_count":
                        len(
                            item_sources
                        ),

                    "gated_source_count":
                        sum(
                            source.gated
                            for source
                            in item_sources
                        ),

                    "source_types":
                        "|".join(
                            sorted(
                                {
                                    source.shop_type
                                    for source
                                    in item_sources
                                }
                            )
                        ),

                    "source_files":
                        "|".join(
                            sorted(
                                files_by_itemid.get(
                                    itemid,
                                    set(),
                                )
                            )
                        ),
                }
            )

    return priced_ids


def write_unresolved(
    constants_by_itemid: dict[
        int,
        set[str],
    ],
    files_by_itemid: dict[
        int,
        set[str],
    ],
    priced_ids: set[int],
) -> None:
    with UNRESOLVED_FILE.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.writer(
            file
        )

        writer.writerow(
            [
                "itemid",
                "name",
                "reason",
                "source_files",
            ]
        )

        for itemid in sorted(
            set(
                constants_by_itemid
            )
            - priced_ids
        ):
            writer.writerow(
                [
                    itemid,

                    canonical_name(
                        itemid,
                        constants_by_itemid,
                    ),

                    "VENDOR_PRESENT_PRICE_UNRESOLVED",

                    "|".join(
                        sorted(
                            files_by_itemid.get(
                                itemid,
                                set(),
                            )
                        )
                    ),
                ]
            )


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

    item_map = load_items()

    (
        shop_price,
        shop_price_source,
    ) = load_shop_price()

    paths = shop_files()

    (
        constants_by_itemid,
        files_by_itemid,
        unresolved_constants,
    ) = presence(
        paths,
        item_map,
    )

    sources: list[
        VendorSource
    ] = []

    for path in paths:
        text = strip_lua_comments(
            path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        )

        sources.extend(
            scan_calls(
                path,
                text,
                item_map,
                shop_price,
            )
        )

        sources.extend(
            scan_direct(
                path,
                text,
                item_map,
                shop_price,
            )
        )

        sources.extend(
            scan_global_stock(
                path,
                text,
                item_map,
                shop_price,
            )
        )

    sources = dedupe(
        sources
    )

    # Ensure detailed priced sources are also reflected in the
    # conservative presence maps.
    for source in sources:
        constants_by_itemid.setdefault(
            source.itemid,
            set(),
        ).add(
            source.name
        )

        files_by_itemid.setdefault(
            source.itemid,
            set(),
        ).add(
            source.source_file
        )

    write_legacy_vendor_items(
        constants_by_itemid,
        files_by_itemid,
    )

    write_vendor_sources(
        sources
    )

    priced_ids = write_vendor_prices(
        constants_by_itemid,
        files_by_itemid,
        sources,
    )

    write_unresolved(
        constants_by_itemid,
        files_by_itemid,
        priced_ids,
    )

    vendor_item_count = len(
        constants_by_itemid
    )

    unresolved_vendor_count = (
        vendor_item_count
        - len(
            priced_ids
        )
    )

    trusted_source_count = sum(
        source.hard_floor_eligible
        for source
        in sources
    )

    print()
    print(
        "======================================"
    )
    print(
        " LSB Vendor Price Index"
    )
    print(
        "======================================"
    )

    print(
        f"Item constants loaded:       "
        f"{len(item_map):>6}"
    )

    print(
        f"Shop-related Lua files:      "
        f"{len(paths):>6}"
    )

    print(
        f"Vendor-associated item IDs:  "
        f"{vendor_item_count:>6}"
    )

    print(
        f"Priced vendor item IDs:      "
        f"{len(priced_ids):>6}"
    )

    print(
        f"Unresolved vendor item IDs:  "
        f"{unresolved_vendor_count:>6}"
    )

    print(
        f"Trusted price sources:       "
        f"{trusted_source_count:>6}"
    )

    print(
        f"Unresolved item constants:   "
        f"{len(unresolved_constants):>6}"
    )

    print()

    print(
        f"SHOP_PRICE: "
        f"{shop_price} "
        f"({shop_price_source})"
    )

    print()
    print(
        "Generated:"
    )

    for path in (
        VENDOR_ITEMS_FILE,
        VENDOR_PRICES_FILE,
        VENDOR_SOURCES_FILE,
        UNRESOLVED_FILE,
    ):
        print(
            f"  {path}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
