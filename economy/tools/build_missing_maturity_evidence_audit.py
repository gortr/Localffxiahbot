from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
LSB_ROOT = Path.home() / "server"

COVERAGE_FILE = ROOT / "economy" / "reports" / "economy-maturity-coverage-audit.csv"
SCROLL_POLICY_FILE = ROOT / "economy" / "generated" / "scroll-puppet-policy.csv"
HQ_POLICY_FILE = ROOT / "economy" / "generated" / "hq-rare-maturity-policy.csv"
VENDOR_PRICES_FILE = ROOT / "economy" / "generated" / "vendor-prices.csv"

SPELL_FILES = [
    LSB_ROOT / "sql" / "spell_list.sql",
    LSB_ROOT / "sql" / "spell_data.sql",
]

OUTPUT_FILE = ROOT / "economy" / "reports" / "missing-maturity-evidence-audit.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "missing-maturity-evidence-summary.json"


EXPECTED_MISSING = 544
EXPECTED_LIVE = 167
EXPECTED_FUTURE = 377


class MissingMaturityEvidenceError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def as_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def normalize_name(value: str) -> str:
    value = clean_text(value).lower()
    value = value.replace("'", "")
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def stage_for_level(level: int) -> str:
    if level <= 0:
        return "REVIEW"
    if level <= 20:
        return "SEED"
    if level <= 40:
        return "GROWING"
    if level <= 60:
        return "ESTABLISHED"
    if level <= 80:
        return "MATURE"
    if level <= 99:
        return "ADVANCED"
    return "FULL"


def load_unique(path: Path, label: str) -> pd.DataFrame:
    if not path.exists():
        raise MissingMaturityEvidenceError(
            f"Missing {label}: {path}"
        )

    frame = pd.read_csv(
        path,
        low_memory=False,
    )

    if "itemid" not in frame.columns:
        raise MissingMaturityEvidenceError(
            f"{label} has no itemid column."
        )

    frame = frame.copy()
    frame["itemid"] = frame["itemid"].map(as_int)

    if frame["itemid"].duplicated().any():
        raise MissingMaturityEvidenceError(
            f"{label} contains duplicate itemids."
        )

    return frame


def find_spell_file() -> Path:
    for path in SPELL_FILES:
        if path.exists():
            return path

    raise MissingMaturityEvidenceError(
        "Could not find spell_list.sql or spell_data.sql under ~/server/sql."
    )


def parse_spell_levels(path: Path) -> dict[str, dict[str, Any]]:
    text = path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    # Current/legacy LSB spell_list rows use:
    # INSERT INTO `spell_list` VALUES (id,'name',0x<job-level blob>,...)
    pattern = re.compile(
        r"INSERT\s+INTO\s+`?spell_(?:list|data)`?\s+VALUES\s*"
        r"\(\s*(\d+)\s*,\s*'([^']+)'\s*,\s*(0x[0-9A-Fa-f]+)",
        re.IGNORECASE,
    )

    spells: dict[str, dict[str, Any]] = {}

    for match in pattern.finditer(text):
        spell_id = int(match.group(1))
        raw_name = match.group(2)
        blob = match.group(3)[2:]

        if len(blob) % 2:
            continue

        levels = [
            int(blob[i:i + 2], 16)
            for i in range(0, len(blob), 2)
        ]

        usable_levels = [
            value
            for value in levels
            if 1 <= value <= 99
        ]

        above_99 = [
            value
            for value in levels
            if value > 99
        ]

        min_level = min(usable_levels) if usable_levels else 0
        max_level = max(usable_levels) if usable_levels else 0

        key = normalize_name(raw_name)

        spells[key] = {
            "spell_id": spell_id,
            "spell_name": raw_name,
            "job_level_blob": "0x" + blob,
            "min_job_level": min_level,
            "max_job_level": max_level,
            "usable_job_count": len(usable_levels),
            "has_above_99_requirement": int(bool(above_99)),
        }

    if not spells:
        raise MissingMaturityEvidenceError(
            f"No spell rows could be parsed from {path}"
        )

    return spells


def scroll_to_spell_key(item_name: str) -> str:
    key = normalize_name(item_name)

    prefixes = [
        "scroll_of_",
        "scroll_of_an_",
        "scroll_of_a_",
        "scroll_of_the_",
    ]

    for prefix in prefixes:
        if key.startswith(prefix):
            return key[len(prefix):]

    return key


def main() -> int:
    coverage = load_unique(
        COVERAGE_FILE,
        "maturity coverage audit",
    )

    scroll_policy = load_unique(
        SCROLL_POLICY_FILE,
        "scroll/puppet policy",
    )

    hq_policy = load_unique(
        HQ_POLICY_FILE,
        "HQ/Rare maturity policy",
    )

    vendor_prices = load_unique(
        VENDOR_PRICES_FILE,
        "vendor prices",
    )

    spell_file = find_spell_file()
    spells = parse_spell_levels(spell_file)

    missing = coverage[
        (coverage["maturity_present"] == 0)
        & (
            (coverage["seller_capable"] == 1)
            | (coverage["buyer_capable"] == 1)
        )
    ].copy()

    if len(missing) != EXPECTED_MISSING:
        raise MissingMaturityEvidenceError(
            f"Expected {EXPECTED_MISSING} capable missing-maturity rows, "
            f"found {len(missing)}"
        )

    scroll_ids = set(
        scroll_policy["itemid"].astype(int)
    )

    hq_ids = set(
        hq_policy["itemid"].astype(int)
    )

    vendor_by_id = {
        int(row["itemid"]): row.to_dict()
        for _, row in vendor_prices.iterrows()
    }

    hq_by_id = {
        int(row["itemid"]): row.to_dict()
        for _, row in hq_policy.iterrows()
    }

    rows: list[dict[str, Any]] = []

    for _, row in missing.iterrows():
        itemid = as_int(row.get("itemid"))
        name = clean_text(row.get("name"))
        live = bool(
            as_int(row.get("live_seller_baseline"))
            or as_int(row.get("live_buyer_baseline"))
        )

        overlay = clean_text(
            row.get("applied_overlays")
        )

        evidence_family = ""
        proposed_stage = "REVIEW"
        confidence = "REVIEW"
        evidence_reason = ""

        spell_key = ""
        spell_id = 0
        spell_name = ""
        min_job_level = 0
        max_job_level = 0
        usable_job_count = 0
        has_above_99_requirement = 0

        if live:
            evidence_family = "LIVE_BASELINE"
            proposed_stage = "SEED"
            confidence = "HIGH"
            evidence_reason = "CURRENT_PHASE0_BASELINE"

        elif itemid in scroll_ids or "scroll-puppet" in overlay:
            evidence_family = "SCROLL"

            spell_key = scroll_to_spell_key(name)
            spell = spells.get(spell_key)

            if spell:
                spell_id = as_int(spell.get("spell_id"))
                spell_name = clean_text(spell.get("spell_name"))
                min_job_level = as_int(spell.get("min_job_level"))
                max_job_level = as_int(spell.get("max_job_level"))
                usable_job_count = as_int(spell.get("usable_job_count"))
                has_above_99_requirement = as_int(
                    spell.get("has_above_99_requirement")
                )

                if min_job_level > 0:
                    proposed_stage = stage_for_level(min_job_level)
                    confidence = "HIGH"
                    evidence_reason = "SPELL_MIN_JOB_LEVEL"
                elif has_above_99_requirement:
                    proposed_stage = "FULL"
                    confidence = "MEDIUM"
                    evidence_reason = "SPELL_REQUIREMENT_ABOVE_99_ONLY"
                else:
                    proposed_stage = "REVIEW"
                    confidence = "REVIEW"
                    evidence_reason = "SPELL_FOUND_WITHOUT_PLAYER_LEVEL"

            else:
                evidence_reason = "NO_SPELL_NAME_MATCH"

        elif itemid in hq_ids or "hq-rare" in overlay:
            evidence_family = "HQ_RARE"

            hq_row = hq_by_id.get(itemid, {})
            existing = clean_text(
                hq_row.get("maturity")
            ) or clean_text(
                hq_row.get("required_maturity")
            )

            if existing and existing.upper() != "NONE":
                proposed_stage = existing.upper()
                confidence = "HIGH"
                evidence_reason = "HQ_RARE_POLICY_EXISTING_STAGE"
            else:
                proposed_stage = "REVIEW"
                confidence = "REVIEW"
                evidence_reason = "HQ_RARE_POLICY_HAS_NO_STAGE"

        else:
            evidence_family = "OTHER"
            evidence_reason = "NO_DEDICATED_MATURITY_EVIDENCE"

        vendor = vendor_by_id.get(itemid, {})

        rows.append(
            {
                "itemid": itemid,
                "name": name,
                "canonical_class": clean_text(
                    row.get("canonical_class")
                ),
                "seller_capable": as_int(
                    row.get("seller_capable")
                ),
                "buyer_capable": as_int(
                    row.get("buyer_capable")
                ),
                "live_seller_baseline": as_int(
                    row.get("live_seller_baseline")
                ),
                "live_buyer_baseline": as_int(
                    row.get("live_buyer_baseline")
                ),
                "evidence_family": evidence_family,
                "proposed_maturity": proposed_stage,
                "maturity_confidence": confidence,
                "evidence_reason": evidence_reason,
                "spell_key": spell_key,
                "spell_id": spell_id,
                "spell_name": spell_name,
                "min_job_level": min_job_level,
                "max_job_level": max_job_level,
                "usable_job_count": usable_job_count,
                "has_above_99_requirement":
                    has_above_99_requirement,
                "vendor_price_min": vendor.get(
                    "vendor_price_min"
                ),
                "vendor_price_max": vendor.get(
                    "vendor_price_max"
                ),
                "has_vendor_price_floor": as_int(
                    vendor.get("has_hard_floor")
                ),
                "applied_overlays": overlay,
                "activation_ready": 0,
                "auto_live_promotion": 0,
            }
        )

    output = pd.DataFrame(rows).sort_values(
        by=[
            "evidence_family",
            "proposed_maturity",
            "itemid",
        ],
        kind="stable",
    )

    live_count = int(
        (
            (
                output["live_seller_baseline"] == 1
            )
            | (
                output["live_buyer_baseline"] == 1
            )
        ).sum()
    )

    future_count = len(output) - live_count

    if live_count != EXPECTED_LIVE:
        raise MissingMaturityEvidenceError(
            f"Expected {EXPECTED_LIVE} live rows, found {live_count}"
        )

    if future_count != EXPECTED_FUTURE:
        raise MissingMaturityEvidenceError(
            f"Expected {EXPECTED_FUTURE} future rows, found {future_count}"
        )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    def counts(column: str) -> dict[str, int]:
        return {
            str(k): int(v)
            for k, v in (
                output[column]
                .fillna("NONE")
                .replace("", "NONE")
                .value_counts()
                .to_dict()
                .items()
            )
        }

    future = output[
        ~(
            (output["live_seller_baseline"] == 1)
            | (output["live_buyer_baseline"] == 1)
        )
    ].copy()

    scrolls = future[
        future["evidence_family"] == "SCROLL"
    ].copy()

    summary = {
        "status": "PASS",
        "total_missing_capable": int(len(output)),
        "live_seed_assignments": live_count,
        "future_missing": future_count,
        "future_evidence_family_counts": {
            str(k): int(v)
            for k, v in (
                future["evidence_family"]
                .value_counts()
                .to_dict()
                .items()
            )
        },
        "proposed_maturity_counts": counts(
            "proposed_maturity"
        ),
        "confidence_counts": counts(
            "maturity_confidence"
        ),
        "scroll_items": int(len(scrolls)),
        "scroll_spell_matches": int(
            (scrolls["spell_id"] > 0).sum()
        ),
        "scroll_level_matches": int(
            (scrolls["min_job_level"] > 0).sum()
        ),
        "scroll_unmatched": int(
            (scrolls["spell_id"] == 0).sum()
        ),
        "scroll_without_player_level": int(
            (
                (scrolls["spell_id"] > 0)
                & (scrolls["min_job_level"] == 0)
            ).sum()
        ),
        "review_required": int(
            (
                output["proposed_maturity"]
                == "REVIEW"
            ).sum()
        ),
        "spell_source_file": str(spell_file),
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
    print("=" * 56)
    print(" Missing Maturity Evidence Audit")
    print("=" * 56)
    print(
        f"Missing capable items:                  "
        f"{summary['total_missing_capable']:>6}"
    )
    print(
        f"Live → proposed SEED:                   "
        f"{summary['live_seed_assignments']:>6}"
    )
    print(
        f"Future missing:                         "
        f"{summary['future_missing']:>6}"
    )
    print(
        f"Scroll items:                           "
        f"{summary['scroll_items']:>6}"
    )
    print(
        f"Scroll spell-name matches:              "
        f"{summary['scroll_spell_matches']:>6}"
    )
    print(
        f"Scrolls with player learn level:        "
        f"{summary['scroll_level_matches']:>6}"
    )
    print(
        f"Scrolls unmatched:                      "
        f"{summary['scroll_unmatched']:>6}"
    )
    print(
        f"Review required:                        "
        f"{summary['review_required']:>6}"
    )
    print()
    print("Proposed maturity:")
    for label, count in sorted(
        summary["proposed_maturity_counts"].items(),
        key=lambda pair: (-pair[1], pair[0]),
    ):
        print(
            f"  {label:<20} "
            f"{count:>6}"
        )
    print()
    print("Activation ready: 0")
    print("Auto live promotions: 0")
    print()
    print(f"Audit:   {OUTPUT_FILE}")
    print(f"Summary: {SUMMARY_FILE}")
    print(f"Spells:  {spell_file}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
