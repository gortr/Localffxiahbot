from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
LSB_ROOT = Path.home() / "server"

LEDGER_FILE = ROOT / "economy" / "reports" / "unknown-provenance-resolution-ledger.csv"

OUTPUT_FILE = ROOT / "economy" / "reports" / "unknown-repository-source-audit.csv"
REFERENCE_FILE = ROOT / "economy" / "reports" / "unknown-repository-source-references.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "unknown-repository-source-audit-summary.json"


TEXT_SUFFIXES = {
    ".lua",
    ".sql",
    ".cpp",
    ".h",
    ".hpp",
}

SKIP_PATH_PARTS = (
    "/modules/",
    "/build/",
    "/ext/",
    "/external/",
    "/third_party/",
    "/tools/",
    "/scripts/commands/",
    "/scripts/specs/",
    "/scripts/enum/",
    "/sql/item_basic.sql",
    "/sql/item_equipment.sql",
    "/sql/item_weapon.sql",
    "/sql/item_usable.sql",
    "/sql/item_furnishing.sql",
    "/sql/item_mods",
    "/sql/item_latents",
    "/sql/synth_recipes.sql",
    "/sql/mob_droplist.sql",
    "/sql/fishing_fish.sql",
)

SOURCE_PATH_KEYWORDS = (
    "battlefield",
    "bcnm",
    "quest",
    "mission",
    "treasure",
    "coffer",
    "chest",
    "helm",
    "harvest",
    "logging",
    "mining",
    "excavat",
    "chocobo",
    "digging",
    "shop",
    "vendor",
    "reward",
    "loot",
    "campaign",
    "assault",
    "salvage",
    "nyzul",
    "dynamis",
    "abyssea",
    "voidwatch",
    "delve",
    "legion",
    "besieged",
    "conquest",
    "roe",
    "records",
    "sparks",
    "garden",
    "mog_garden",
)

SOURCE_CONTEXT_KEYWORDS = (
    "additem",
    "giveitem",
    "reward",
    "itemid",
    "item_id",
    "loot",
    "drop",
    "droprate",
    "weight",
    "shop",
    "vendor",
    "treasure",
    "coffer",
    "chest",
    "harvest",
    "logging",
    "mining",
    "excavat",
    "digging",
)

NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9_])(\d{2,5})(?![A-Za-z0-9_])"
)


class RepositorySourceAuditError(RuntimeError):
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


def source_family(path: Path, context: str) -> str:
    low_path = path.as_posix().lower()
    low = context.lower()

    checks = [
        ("BATTLEFIELD", ("battlefield", "bcnm")),
        ("QUEST", ("quest",)),
        ("MISSION", ("mission",)),
        ("HELM", ("helm", "harvest", "logging", "mining", "excavat")),
        ("CHOCOBO_DIGGING", ("chocobo", "digging")),
        ("TREASURE", ("treasure", "coffer", "chest")),
        ("SHOP", ("shop", "vendor")),
        ("CAMPAIGN", ("campaign",)),
        ("ASSAULT_NYZUL", ("assault", "nyzul")),
        ("SALVAGE", ("salvage",)),
        ("DYNAMIS", ("dynamis",)),
        ("ABYSSEA", ("abyssea",)),
        ("VOIDWATCH", ("voidwatch",)),
        ("DELVE", ("delve",)),
        ("LEGION", ("legion",)),
        ("BESIEGED", ("besieged",)),
        ("CONQUEST", ("conquest",)),
        ("RECORDS_OF_EMINENCE", ("roe", "records", "sparks")),
        ("MOG_GARDEN", ("mog_garden", "garden")),
    ]

    for family, needles in checks:
        if any(
            needle in low_path
            for needle in needles
        ):
            return family

    for family, needles in checks:
        if any(
            needle in low
            for needle in needles
        ):
            return family

    return "OTHER_ACQUISITION_CONTEXT"


def confidence_for(
    family: str,
    context: str,
) -> str:
    low = context.lower()

    high_patterns = (
        "additem",
        "giveitem",
        "itemid",
        "item_id",
        "droprate",
        "loot",
        "shop",
        "vendor",
    )

    if any(
        marker in low
        for marker in high_patterns
    ):
        return "HIGH"

    if family != "OTHER_ACQUISITION_CONTEXT":
        return "MEDIUM"

    return "LOW"


def main() -> int:
    if not LEDGER_FILE.exists():
        raise RepositorySourceAuditError(
            f"Missing resolution ledger: {LEDGER_FILE}"
        )

    ledger = pd.read_csv(
        LEDGER_FILE,
        low_memory=False,
    )

    unresolved = ledger[
        ledger["source_status"]
        .str.startswith("UNRESOLVED")
    ].copy()

    target_ids = set(
        unresolved["itemid"].astype(int)
    )

    if not target_ids:
        raise RepositorySourceAuditError(
            "No unresolved target items remain."
        )

    name_by_id = {
        int(itemid): clean_text(name)
        for itemid, name
        in zip(
            unresolved["itemid"],
            unresolved["name"],
        )
    }

    references: list[dict[str, Any]] = []

    for path in LSB_ROOT.rglob("*"):
        if not path.is_file():
            continue

        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue

        rel = "/" + str(
            path.relative_to(LSB_ROOT)
        ).replace("\\", "/")

        low_rel = rel.lower()

        if any(
            part in low_rel
            for part in SKIP_PATH_PARTS
        ):
            continue

        path_source_shaped = any(
            keyword in low_rel
            for keyword in SOURCE_PATH_KEYWORDS
        )

        try:
            text = path.read_text(
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            continue

        matches = list(
            NUMBER_RE.finditer(text)
        )

        if not matches:
            continue

        lines = text.splitlines()

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
                line_number - 8,
            )
            end = min(
                len(lines),
                line_number + 7,
            )

            context = "\n".join(
                lines[start:end]
            )

            context_source_shaped = any(
                keyword in context.lower()
                for keyword in SOURCE_CONTEXT_KEYWORDS
            )

            if not (
                path_source_shaped
                or context_source_shaped
            ):
                continue

            family = source_family(
                path,
                context,
            )

            confidence = confidence_for(
                family,
                context,
            )

            references.append(
                {
                    "itemid": itemid,
                    "name": name_by_id.get(
                        itemid,
                        "",
                    ),
                    "source_family": family,
                    "confidence": confidence,
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

    refs = pd.DataFrame(
        references
    )

    if refs.empty:
        refs = pd.DataFrame(
            columns=[
                "itemid",
                "name",
                "source_family",
                "confidence",
                "file",
                "line",
                "context",
            ]
        )

    refs = refs.drop_duplicates(
        subset=[
            "itemid",
            "source_family",
            "file",
            "line",
        ]
    ).sort_values(
        by=[
            "itemid",
            "source_family",
            "file",
            "line",
        ],
        kind="stable",
    )

    refs.to_csv(
        REFERENCE_FILE,
        index=False,
    )

    rank = {
        "HIGH": 3,
        "MEDIUM": 2,
        "LOW": 1,
        "": 0,
    }

    groups = {
        int(itemid): group.copy()
        for itemid, group
        in refs.groupby("itemid")
    } if not refs.empty else {}

    rows: list[dict[str, Any]] = []

    for _, row in unresolved.iterrows():
        itemid = as_int(
            row.get("itemid")
        )

        group = groups.get(
            itemid
        )

        if group is None:
            families: list[str] = []
            files: list[str] = []
            best_confidence = ""
            count = 0
        else:
            families = sorted(
                set(
                    group["source_family"]
                    .map(clean_text)
                )
            )
            files = sorted(
                set(
                    group["file"]
                    .map(clean_text)
                )
            )
            best_confidence = max(
                group["confidence"]
                .map(clean_text),
                key=lambda value:
                    rank.get(value, 0),
            )
            count = len(group)

        if best_confidence == "HIGH":
            resolution = "REPOSITORY_SOURCE_FOUND_HIGH"
        elif best_confidence == "MEDIUM":
            resolution = "REPOSITORY_SOURCE_FOUND_REVIEW"
        elif best_confidence == "LOW":
            resolution = "REPOSITORY_REFERENCE_REVIEW"
        else:
            resolution = "STILL_NO_REPOSITORY_SOURCE"

        rows.append(
            {
                "itemid": itemid,
                "name": clean_text(
                    row.get("name")
                ),
                "prior_resolution": clean_text(
                    row.get("resolution")
                ),
                "item_type": as_int(
                    row.get("item_type")
                ),
                "ah_category": as_int(
                    row.get("ah_category")
                ),
                "repository_source_families": "|".join(
                    families
                ),
                "best_confidence": best_confidence,
                "repository_reference_count": count,
                "repository_reference_files": "|".join(
                    files
                ),
                "resolution": resolution,
                "activation_ready": 0,
                "auto_live_promotion": 0,
            }
        )

    output = pd.DataFrame(
        rows
    ).sort_values(
        by=[
            "resolution",
            "item_type",
            "itemid",
        ],
        kind="stable",
    )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    summary = {
        "status": "PASS",
        "target_unresolved_items": int(
            len(output)
        ),
        "items_with_repository_source_reference": int(
            (
                output["repository_reference_count"]
                > 0
            ).sum()
        ),
        "items_still_without_repository_source": int(
            (
                output["repository_reference_count"]
                == 0
            ).sum()
        ),
        "resolution_counts": {
            str(k): int(v)
            for k, v in (
                output["resolution"]
                .value_counts()
                .to_dict()
                .items()
            )
        },
        "family_item_counts": {
            family: int(
                refs.loc[
                    refs["source_family"] == family,
                    "itemid",
                ].nunique()
            )
            for family in sorted(
                refs["source_family"].unique()
            )
        } if not refs.empty else {},
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
    print("=" * 44)
    print(" Unknown Repository Source Audit")
    print("=" * 44)
    print(
        f"Target unresolved items:        "
        f"{summary['target_unresolved_items']:>6}"
    )
    print(
        f"Repository source references:   "
        f"{summary['items_with_repository_source_reference']:>6}"
    )
    print(
        f"Still no repository source:     "
        f"{summary['items_still_without_repository_source']:>6}"
    )
    print()
    print("Resolution:")
    for label, count in sorted(
        summary["resolution_counts"].items(),
        key=lambda pair: (-pair[1], pair[0]),
    ):
        print(
            f"  {label:<36} "
            f"{count:>6}"
        )
    print()
    print("Source families:")
    for label, count in sorted(
        summary["family_item_counts"].items(),
        key=lambda pair: (-pair[1], pair[0]),
    ):
        print(
            f"  {label:<30} "
            f"{count:>6}"
        )
    print()
    print("Activation ready: 0")
    print("Auto live promotions: 0")
    print()
    print(f"Audit:      {OUTPUT_FILE}")
    print(f"References: {REFERENCE_FILE}")
    print(f"Summary:    {SUMMARY_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
