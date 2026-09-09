from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

CENSUS_FILE = ROOT / "economy" / "reports" / "unknown-provenance-census.csv"
REFERENCE_FILE = ROOT / "economy" / "reports" / "unknown-provenance-script-references.csv"

OUTPUT_FILE = ROOT / "economy" / "generated" / "unknown-source-family-evidence.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "unknown-source-family-evidence-summary.json"


class SourceFamilyEvidenceError(RuntimeError):
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


def contains_any(text: str, needles: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(needle.lower() in low for needle in needles)


def main() -> int:
    for path in [CENSUS_FILE, REFERENCE_FILE]:
        if not path.exists():
            raise SourceFamilyEvidenceError(
                f"Missing required input: {path}"
            )

    census = pd.read_csv(
        CENSUS_FILE,
        low_memory=False,
    )

    refs = pd.read_csv(
        REFERENCE_FILE,
        low_memory=False,
    )

    if len(census) != 5011:
        raise SourceFamilyEvidenceError(
            f"Expected 5011 census rows, found {len(census)}"
        )

    required_ref_columns = {
        "itemid",
        "file",
        "path_family",
        "reward_like",
        "shop_like",
        "trade_like",
        "context",
    }

    missing = sorted(
        required_ref_columns - set(refs.columns)
    )

    if missing:
        raise SourceFamilyEvidenceError(
            "unknown-provenance-script-references.csv missing: "
            + ", ".join(missing)
        )

    rows: list[dict[str, Any]] = []

    for _, ref in refs.iterrows():
        itemid = as_int(
            ref.get("itemid")
        )

        if itemid <= 0:
            continue

        family = clean_text(
            ref.get("path_family")
        )

        context = clean_text(
            ref.get("context")
        )

        file_name = clean_text(
            ref.get("file")
        )

        source_type = ""
        confidence = ""
        policy_hint = ""
        reason = ""

        # High-confidence battlefield loot structure.
        if (
            family == "BATTLEFIELD"
            and "droprate" in context.lower()
            and "itemid" in context.lower()
        ):
            source_type = "BATTLEFIELD_DROP"
            confidence = "HIGH"
            policy_hint = "PROTECT_OR_DEMAND_ONLY"
            reason = "LSB_BATTLEFIELD_LOOT_ENTRY"

        # Quest/mission/script reward contexts.
        elif as_int(ref.get("reward_like")):
            if family == "QUEST":
                source_type = "QUEST_REWARD"
                policy_hint = "PROTECTED"
                reason = "QUEST_REWARD_LIKE_CONTEXT"
            elif family == "MISSION":
                source_type = "MISSION_REWARD"
                policy_hint = "PROTECTED"
                reason = "MISSION_REWARD_LIKE_CONTEXT"
            else:
                source_type = "SCRIPT_REWARD"
                policy_hint = "PROTECTED_REVIEW"
                reason = "GENERIC_REWARD_LIKE_CONTEXT"

            confidence = "MEDIUM"

        # HELM files are acquisition systems. Keep exact gathering subtype
        # unknown at this pass unless the file/context reveals it.
        elif family == "HELM":
            source_type = "HELM_GATHERING"
            confidence = "HIGH"
            policy_hint = "ORDINARY_SOURCE_CANDIDATE"
            reason = "LSB_HELM_SOURCE_REFERENCE"

        elif family == "CHOCOBO_DIGGING":
            source_type = "CHOCOBO_DIGGING"
            confidence = "HIGH"
            policy_hint = "ORDINARY_SOURCE_CANDIDATE"
            reason = "LSB_CHOCOBO_DIGGING_REFERENCE"

        # Only trust shop evidence when the nearby context itself contains
        # a shop construction/addition marker, not merely somewhere in file.
        elif contains_any(
            context,
            (
                "xi.shop.",
                "addShopItem",
                "createShop",
            ),
        ):
            source_type = "NPC_VENDOR_REFERENCE"
            confidence = "HIGH"
            policy_hint = "VENDOR_REVIEW"
            reason = "LOCAL_SHOP_CONTEXT"

        # Treasure references need more context before treating them as loot.
        elif family == "TREASURE_SYSTEM":
            source_type = "TREASURE_SYSTEM_REFERENCE"
            confidence = "MEDIUM"
            policy_hint = "SOURCE_REVIEW"
            reason = "TREASURE_SYSTEM_REFERENCE"

        # Preserve source-family signal for later detailed classification.
        elif family in {
            "QUEST",
            "MISSION",
            "NPC_SCRIPT",
            "MOB_SCRIPT",
        }:
            source_type = f"{family}_REFERENCE"
            confidence = "LOW"
            policy_hint = "SOURCE_REVIEW"
            reason = "REFERENCE_WITHOUT_ACQUISITION_PROOF"

        else:
            continue

        rows.append(
            {
                "itemid": itemid,
                "name": clean_text(ref.get("name")),
                "source_type": source_type,
                "confidence": confidence,
                "policy_hint": policy_hint,
                "reason": reason,
                "path_family": family,
                "file": file_name,
                "line": as_int(ref.get("line")),
                "context": context,
                "auto_live_promotion": 0,
            }
        )

    evidence = pd.DataFrame(rows)

    if evidence.empty:
        raise SourceFamilyEvidenceError(
            "No source-family evidence was produced."
        )

    evidence = evidence.sort_values(
        by=[
            "itemid",
            "source_type",
            "file",
            "line",
        ],
        kind="stable",
    )

    evidence.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    item_level = (
        evidence[
            [
                "itemid",
                "source_type",
                "confidence",
                "policy_hint",
            ]
        ]
        .drop_duplicates()
    )

    high_conf_itemids = set(
        item_level.loc[
            item_level["confidence"] == "HIGH",
            "itemid",
        ].astype(int)
    )

    summary = {
        "status": "PASS",
        "evidence_rows": int(len(evidence)),
        "items_with_any_family_evidence": int(
            evidence["itemid"].nunique()
        ),
        "items_with_high_confidence_evidence": int(
            len(high_conf_itemids)
        ),
        "source_type_item_counts": {
            str(source_type): int(
                group["itemid"].nunique()
            )
            for source_type, group
            in evidence.groupby("source_type")
        },
        "confidence_item_counts": {
            str(confidence): int(
                group["itemid"].nunique()
            )
            for confidence, group
            in evidence.groupby("confidence")
        },
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
    print("======================================")
    print(" Unknown Source-Family Evidence")
    print("======================================")
    print(
        f"Evidence rows:                  "
        f"{summary['evidence_rows']:>6}"
    )
    print(
        f"Items with any evidence:        "
        f"{summary['items_with_any_family_evidence']:>6}"
    )
    print(
        f"High-confidence items:          "
        f"{summary['items_with_high_confidence_evidence']:>6}"
    )
    print()
    print("Source types:")
    for source_type, count in sorted(
        summary["source_type_item_counts"].items(),
        key=lambda pair: (-pair[1], pair[0]),
    ):
        print(
            f"  {source_type:<30} "
            f"{count:>6}"
        )
    print()
    print("Auto live promotions: 0")
    print()
    print(f"Generated: {OUTPUT_FILE}")
    print(f"Summary:   {SUMMARY_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
