from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
LSB_ROOT = Path.home() / "server"

EVIDENCE_FILE = ROOT / "economy" / "reports" / "missing-maturity-evidence-audit.csv"
SPELL_FILE = LSB_ROOT / "sql" / "spell_list.sql"

OUTPUT_FILE = ROOT / "economy" / "reports" / "missing-maturity-resolution.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "missing-maturity-resolution-summary.json"


class MaturityResolutionError(RuntimeError):
    pass


STAGES = {
    "SEED",
    "GROWING",
    "ESTABLISHED",
    "MATURE",
    "ADVANCED",
    "FULL",
}


# Retail Phantom Roll learn levels. Item names differ from the resulting
# ability names, so this explicit mapping is safer than name guessing.
DIE_LEVELS = {
    "warrior_die": 49,       # Fighter's Roll
    "monk_die": 31,          # Monk's Roll
    "white_mage_die": 20,    # Healer's Roll
    "black_mage_die": 58,    # Wizard's Roll
    "red_mage_die": 46,      # Warlock's Roll
    "thief_die": 43,         # Rogue's Roll
    "paladin_die": 55,       # Gallant's Roll
    "dark_knight_die": 14,   # Chaos Roll
    "beastmaster_die": 34,   # Beast Roll
    "bard_die": 26,          # Choral Roll
    "ranger_die": 11,        # Hunter's Roll
    "samurai_die": 37,       # Samurai Roll
    "ninja_die": 8,          # Ninja Roll
    "dragoon_die": 23,       # Drachen Roll
    "summoner_die": 40,      # Evoker's Roll
    "blue_mage_die": 17,     # Magus's Roll
    "corsair_die": 5,        # Corsair's Roll
    "puppetmaster_die": 52,  # Puppet Roll
    "dancer_die": 61,        # Dancer's Roll
    "scholar_die": 64,       # Scholar's Roll
    "bolters_die": 76,       # Bolter's Roll
    "casters_die": 79,       # Caster's Roll
    "coursers_die": 81,      # Courser's Roll
    "blitzers_die": 83,      # Blitzer's Roll
    "tacticians_die": 86,    # Tactician's Roll
    "allies_die": 89,        # Allies' Roll
    "misers_die": 92,        # Miser's Roll
    "companions_die": 95,    # Companion's Roll
    "avengers_die": 97,      # Avenger's Roll
    "geomancer_die": 67,     # Naturalist's Roll
    "rune_fencer_die": 70,   # Runeist's Roll
}


SPECIAL_SCROLL_LEVELS = {
    "earth_spirit_pact": 1,
    "scroll_of_inundation": 64,
}


PROTECTED_SPECIAL_ITEMS = {
    1135: "NORG_SHELL_PROGRESSION_ITEM",
    1210: "DAMP_SCROLL_QUEST_ITEM",
}


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


def parse_spell_levels(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        raise MaturityResolutionError(
            f"Missing spell source: {path}"
        )

    text = path.read_text(
        encoding="utf-8",
        errors="replace",
    )

    pattern = re.compile(
        r"INSERT\s+INTO\s+`?spell_list`?\s+VALUES\s*"
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

        usable = [
            level
            for level in levels
            if 1 <= level <= 99
        ]

        spells[normalize_name(raw_name)] = {
            "spell_id": spell_id,
            "spell_name": raw_name,
            "min_level": min(usable) if usable else 0,
        }

    if not spells:
        raise MaturityResolutionError(
            "No spell rows parsed from spell_list.sql."
        )

    return spells


def schema_spell_key(item_name: str) -> str:
    key = normalize_name(item_name)

    if key.endswith("_schema"):
        return key[:-7]

    return key


def main() -> int:
    if not EVIDENCE_FILE.exists():
        raise MaturityResolutionError(
            f"Missing evidence audit: {EVIDENCE_FILE}"
        )

    evidence = pd.read_csv(
        EVIDENCE_FILE,
        low_memory=False,
    )

    if len(evidence) != 544:
        raise MaturityResolutionError(
            f"Expected 544 evidence rows, found {len(evidence)}"
        )

    spells = parse_spell_levels(
        SPELL_FILE
    )

    rows: list[dict[str, Any]] = []

    for _, row in evidence.iterrows():
        itemid = as_int(row.get("itemid"))
        name = normalize_name(
            row.get("name")
        )
        current = clean_text(
            row.get("proposed_maturity")
        ).upper()

        final_stage = ""
        disposition = ""
        confidence = ""
        reason = ""
        evidence_level = 0
        evidence_name = ""

        live = bool(
            as_int(row.get("live_seller_baseline"))
            or as_int(row.get("live_buyer_baseline"))
        )

        if itemid in PROTECTED_SPECIAL_ITEMS:
            disposition = "KEEP_PROTECTED"
            final_stage = ""
            confidence = "HIGH"
            reason = PROTECTED_SPECIAL_ITEMS[itemid]

        elif live:
            disposition = "ASSIGN_MATURITY"
            final_stage = "SEED"
            confidence = "HIGH"
            reason = "CURRENT_PHASE0_BASELINE"

        elif current in STAGES:
            disposition = "ASSIGN_MATURITY"
            final_stage = current
            confidence = clean_text(
                row.get("maturity_confidence")
            ) or "HIGH"
            reason = clean_text(
                row.get("evidence_reason")
            ) or "EXISTING_HIGH_CONFIDENCE_EVIDENCE"

        elif name in DIE_LEVELS:
            evidence_level = DIE_LEVELS[name]
            final_stage = stage_for_level(
                evidence_level
            )
            disposition = "ASSIGN_MATURITY"
            confidence = "HIGH"
            reason = "PHANTOM_ROLL_LEARN_LEVEL"
            evidence_name = name

        elif name in SPECIAL_SCROLL_LEVELS:
            evidence_level = SPECIAL_SCROLL_LEVELS[
                name
            ]
            final_stage = stage_for_level(
                evidence_level
            )
            disposition = "ASSIGN_MATURITY"
            confidence = "HIGH"
            reason = "SPECIAL_SCROLL_LEARN_LEVEL"
            evidence_name = name

        elif name.endswith("_schema"):
            key = schema_spell_key(
                name
            )
            spell = spells.get(
                key
            )

            if spell and as_int(
                spell.get("min_level")
            ) > 0:
                evidence_level = as_int(
                    spell.get("min_level")
                )
                evidence_name = clean_text(
                    spell.get("spell_name")
                )
                final_stage = stage_for_level(
                    evidence_level
                )
                disposition = "ASSIGN_MATURITY"
                confidence = "HIGH"
                reason = "SCHEMA_SPELL_MIN_JOB_LEVEL"
            else:
                disposition = "REVIEW"
                confidence = "REVIEW"
                reason = "SCHEMA_SPELL_LEVEL_UNRESOLVED"

        else:
            disposition = "REVIEW"
            confidence = "REVIEW"
            reason = "UNRESOLVED_MATURITY_EVIDENCE"

        rows.append(
            {
                "itemid": itemid,
                "name": clean_text(row.get("name")),
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
                "disposition": disposition,
                "final_maturity": final_stage,
                "confidence": confidence,
                "resolution_reason": reason,
                "evidence_level": evidence_level,
                "evidence_name": evidence_name,
                "prior_proposed_maturity": current,
                "prior_evidence_reason": clean_text(
                    row.get("evidence_reason")
                ),
                "activation_ready": 0,
                "auto_live_promotion": 0,
            }
        )

    output = pd.DataFrame(
        rows
    ).sort_values(
        by=[
            "disposition",
            "final_maturity",
            "itemid",
        ],
        kind="stable",
    )

    unresolved = output[
        output["disposition"] == "REVIEW"
    ]

    protected = output[
        output["disposition"] == "KEEP_PROTECTED"
    ]

    assigned = output[
        output["disposition"] == "ASSIGN_MATURITY"
    ]

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    summary = {
        "status": "PASS",
        "total_items": int(len(output)),
        "assigned_maturity": int(len(assigned)),
        "protected_special_items": int(len(protected)),
        "review_required": int(len(unresolved)),
        "maturity_counts": {
            str(k): int(v)
            for k, v in (
                assigned["final_maturity"]
                .value_counts()
                .to_dict()
                .items()
            )
        },
        "resolution_reason_counts": {
            str(k): int(v)
            for k, v in (
                output["resolution_reason"]
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
    print("=" * 56)
    print(" Missing Maturity Resolution")
    print("=" * 56)
    print(
        f"Total reviewed items:                  "
        f"{summary['total_items']:>6}"
    )
    print(
        f"Maturity assigned:                     "
        f"{summary['assigned_maturity']:>6}"
    )
    print(
        f"Protected special items:               "
        f"{summary['protected_special_items']:>6}"
    )
    print(
        f"Still requiring review:                "
        f"{summary['review_required']:>6}"
    )
    print()
    print("Assigned maturity:")
    for label, count in sorted(
        summary["maturity_counts"].items(),
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
    print(f"Resolution: {OUTPUT_FILE}")
    print(f"Summary:    {SUMMARY_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
