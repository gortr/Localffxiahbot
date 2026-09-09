from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path.home() / "ffxiahbot"
RESIDUAL_FILE = ROOT / "economy" / "reports" / "unknown-residual-audit.csv"
NO_SCRIPT_FILE = ROOT / "economy" / "reports" / "unknown-no-script-audit.csv"
OUTPUT_FILE = ROOT / "economy" / "reports" / "true-unknown-acquisition-audit.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "true-unknown-acquisition-audit-summary.json"

class TrueUnknownAuditError(RuntimeError):
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

def ensure(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = ""
    return out

def main() -> int:
    for path in [RESIDUAL_FILE, NO_SCRIPT_FILE]:
        if not path.exists():
            raise TrueUnknownAuditError(f"Missing required input: {path}")

    residual = pd.read_csv(RESIDUAL_FILE, low_memory=False)
    no_script = ensure(
        pd.read_csv(NO_SCRIPT_FILE, low_memory=False),
        [
            "itemid", "semantic_families", "missing_item_enum_constant",
            "recommended_next_step", "base_sell", "rare", "can_equip",
            "pricing_ready",
        ],
    )

    target = residual[
        residual["candidate_reason"] == "UNKNOWN_ACQUISITION"
    ].copy()

    if len(target) != 2846:
        raise TrueUnknownAuditError(
            f"Expected 2846 unknown-acquisition items, found {len(target)}"
        )

    merged = target.merge(
        no_script[
            [
                "itemid", "semantic_families", "missing_item_enum_constant",
                "recommended_next_step", "base_sell", "rare", "can_equip",
                "pricing_ready",
            ]
        ],
        on="itemid",
        how="left",
        validate="one_to_one",
        suffixes=("", "_noscript"),
    )

    rows = []

    for _, row in merged.iterrows():
        ah_category = as_int(row.get("ah_category"))
        script_refs = as_int(row.get("script_reference_count"))
        repo_refs = as_int(row.get("repository_reference_count"))
        semantic = (
            clean_text(row.get("semantic_families_noscript"))
            or clean_text(row.get("semantic_families"))
        )

        if ah_category > 0 and script_refs == 0 and repo_refs == 0:
            acquisition_gap = "AH_LISTED_NO_SOURCE_ANYWHERE"
            priority = 1
        elif ah_category > 0:
            acquisition_gap = "AH_LISTED_REFERENCE_WITHOUT_PROOF"
            priority = 2
        elif ah_category == 0 and script_refs == 0 and repo_refs == 0:
            acquisition_gap = "NON_AH_NO_SOURCE_ANYWHERE"
            priority = 3
        else:
            acquisition_gap = "NON_AH_REFERENCE_WITHOUT_PROOF"
            priority = 3

        rows.append({
            "itemid": as_int(row.get("itemid")),
            "name": clean_text(row.get("name")),
            "item_type": as_int(row.get("item_type")),
            "ah_category": ah_category,
            "stack_size": as_int(row.get("stack_size")),
            "semantic_families": semantic,
            "missing_item_enum_constant": as_int(
                row.get("missing_item_enum_constant")
            ),
            "base_sell": as_int(row.get("base_sell")),
            "rare": as_int(row.get("rare")),
            "can_equip": as_int(row.get("can_equip")),
            "pricing_ready": as_int(row.get("pricing_ready")),
            "script_reference_count": script_refs,
            "repository_reference_count": repo_refs,
            "repository_source_families": clean_text(
                row.get("repository_source_families")
            ),
            "path_families": clean_text(row.get("path_families")),
            "residual_bucket": clean_text(row.get("residual_bucket")),
            "acquisition_gap": acquisition_gap,
            "audit_priority": priority,
            "activation_ready": 0,
            "auto_live_promotion": 0,
        })

    output = pd.DataFrame(rows).sort_values(
        by=[
            "audit_priority", "acquisition_gap",
            "semantic_families", "item_type", "itemid",
        ],
        kind="stable",
    )
    output.to_csv(OUTPUT_FILE, index=False)

    semantic_counts = {}
    for value in output["semantic_families"].fillna(""):
        families = [family for family in str(value).split("|") if family]
        if not families:
            semantic_counts["NONE"] = semantic_counts.get("NONE", 0) + 1
        else:
            for family in families:
                semantic_counts[family] = semantic_counts.get(family, 0) + 1

    summary = {
        "status": "PASS",
        "total_items": int(len(output)),
        "acquisition_gap_counts": {
            str(k): int(v)
            for k, v in output["acquisition_gap"].value_counts().to_dict().items()
        },
        "semantic_family_counts": dict(
            sorted(semantic_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ),
        "item_type_counts": {
            str(k): int(v)
            for k, v in (
                output["item_type"].value_counts().sort_index().to_dict().items()
            )
        },
        "ah_listed": int((output["ah_category"] > 0).sum()),
        "missing_item_enum_constant": int(
            output["missing_item_enum_constant"].sum()
        ),
        "can_equip": int(output["can_equip"].sum()),
        "rare": int(output["rare"].sum()),
        "activation_ready": 0,
        "auto_live_promotions": 0,
    }

    SUMMARY_FILE.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 48)
    print(" True Unknown Acquisition Audit")
    print("=" * 48)
    print(f"Unknown-acquisition items:       {summary['total_items']:>6}")
    print(f"AH-listed:                       {summary['ah_listed']:>6}")
    print(f"Can equip:                       {summary['can_equip']:>6}")
    print(f"Rare:                            {summary['rare']:>6}")
    print(
        f"Missing item enum constant:      "
        f"{summary['missing_item_enum_constant']:>6}"
    )
    print()
    print("Acquisition gaps:")
    for label, count in sorted(
        summary["acquisition_gap_counts"].items(),
        key=lambda pair: (-pair[1], pair[0]),
    ):
        print(f"  {label:<38} {count:>6}")
    print()
    print("Semantic families:")
    for label, count in list(summary["semantic_family_counts"].items())[:30]:
        print(f"  {label:<28} {count:>6}")
    print()
    print("Activation ready: 0")
    print("Auto live promotions: 0")
    print()
    print(f"Audit:   {OUTPUT_FILE}")
    print(f"Summary: {SUMMARY_FILE}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
