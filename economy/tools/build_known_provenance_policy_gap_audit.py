from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path.home() / "ffxiahbot"
RESIDUAL_FILE = ROOT / "economy" / "reports" / "unknown-residual-audit.csv"
PROVENANCE_FILE = ROOT / "economy" / "generated" / "item-provenance.csv"
POLICY_FILE = ROOT / "economy" / "generated" / "candidate-market-policy.csv"
OUTPUT_FILE = ROOT / "economy" / "reports" / "known-provenance-policy-gap-audit.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "known-provenance-policy-gap-audit-summary.json"

class KnownProvenanceGapError(RuntimeError):
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

def split_tags(value: Any) -> set[str]:
    text = clean_text(value)
    return {part for part in text.split("|") if part} if text else set()

def classify_signature(tags: set[str]) -> tuple[str, str]:
    if not tags:
        return "MISSING_PROVENANCE_TAGS", "KEEP_PROTECTED"
    if any(tag in tags for tag in {
        "LEGENDARY_FISH", "KEYITEM_GATED_FISH",
        "QUEST_ONLY_FISH", "DISABLED_FISH",
    }):
        return "SPECIAL_FISHING", "KEEP_PROTECTED"
    if any(tag in tags for tag in {
        "RESTRICTED_CRAFT_NQ_OUTPUT",
        "RESTRICTED_CRAFT_HQ_OUTPUT",
        "RESTRICTED_CRAFT_INPUT",
    }):
        return "RESTRICTED_CRAFT", "USE_RESTRICTED_CRAFT_POLICY"
    if "CRAFT_HQ_OUTPUT" in tags:
        return "CRAFT_HQ", "USE_HQ_RARE_POLICY"
    if "CRAFT_NQ_OUTPUT" in tags:
        return "CRAFT_NQ", "CRAFT_MATURITY_POLICY_GAP"
    if "DESYNTH_OUTPUT" in tags:
        return "DESYNTH_OUTPUT", "DESYNTH_POLICY_GAP"
    if "DESYNTH_INPUT" in tags:
        return "DESYNTH_INPUT", "SOURCE_POLICY_REVIEW"
    if "CRAFT_INPUT" in tags:
        return "CRAFT_INPUT_ONLY", "ORDINARY_INPUT_POLICY_REVIEW"
    if "NPC_VENDOR" in tags:
        return "NPC_VENDOR_ONLY", "VENDOR_POLICY_REVIEW"
    if "MOB_DROP" in tags:
        return "MOB_DROP_ONLY", "MOB_SOURCE_POLICY_REVIEW"
    if "FISHING" in tags:
        return "FISHING_ONLY", "FISHING_POLICY_REVIEW"
    return "OTHER_KNOWN_PROVENANCE", "POLICY_RULE_REQUIRED"

def ensure(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = ""
    return out

def main() -> int:
    for path in [RESIDUAL_FILE, PROVENANCE_FILE, POLICY_FILE]:
        if not path.exists():
            raise KnownProvenanceGapError(f"Missing required input: {path}")

    residual = pd.read_csv(RESIDUAL_FILE, low_memory=False)
    provenance = ensure(
        pd.read_csv(PROVENANCE_FILE, low_memory=False),
        ["itemid", "provenance_tags"],
    )
    policy = ensure(
        pd.read_csv(POLICY_FILE, low_memory=False),
        [
            "itemid", "vendor_item", "has_vendor_price_floor",
            "pricing_ready", "mob_source_class",
            "source_override_applied", "source_override_types",
        ],
    )

    target = residual[
        residual["candidate_reason"] == "KNOWN_PROVENANCE_NOT_YET_CLASSIFIED"
    ].copy()

    if len(target) != 575:
        raise KnownProvenanceGapError(
            f"Expected 575 known-provenance gaps, found {len(target)}"
        )

    merged = target.merge(
        provenance[["itemid", "provenance_tags"]],
        on="itemid", how="left", validate="one_to_one",
    ).merge(
        policy[
            [
                "itemid", "vendor_item", "has_vendor_price_floor",
                "pricing_ready", "mob_source_class",
                "source_override_applied", "source_override_types",
            ]
        ],
        on="itemid", how="left", validate="one_to_one",
    )

    rows = []
    for _, row in merged.iterrows():
        tags = split_tags(row.get("provenance_tags"))
        family, next_step = classify_signature(tags)
        rows.append({
            "itemid": as_int(row.get("itemid")),
            "name": clean_text(row.get("name")),
            "item_type": as_int(row.get("item_type")),
            "ah_category": as_int(row.get("ah_category")),
            "stack_size": as_int(row.get("stack_size")),
            "provenance_tags": "|".join(sorted(tags)),
            "policy_gap_family": family,
            "recommended_next_step": next_step,
            "vendor_item": as_int(row.get("vendor_item")),
            "has_vendor_price_floor": as_int(row.get("has_vendor_price_floor")),
            "pricing_ready": as_int(row.get("pricing_ready")),
            "mob_source_class": clean_text(row.get("mob_source_class")),
            "source_override_applied": as_int(row.get("source_override_applied")),
            "source_override_types": clean_text(row.get("source_override_types")),
            "residual_bucket": clean_text(row.get("residual_bucket")),
            "activation_ready": 0,
            "auto_live_promotion": 0,
        })

    output = pd.DataFrame(rows).sort_values(
        by=["policy_gap_family", "provenance_tags", "item_type", "itemid"],
        kind="stable",
    )
    output.to_csv(OUTPUT_FILE, index=False)

    tag_counts = {}
    for value in output["provenance_tags"].fillna(""):
        for tag in str(value).split("|"):
            if tag:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

    summary = {
        "status": "PASS",
        "total_items": int(len(output)),
        "policy_gap_family_counts": {
            str(k): int(v)
            for k, v in output["policy_gap_family"].value_counts().to_dict().items()
        },
        "top_tag_signatures": {
            str(k): int(v)
            for k, v in (
                output["provenance_tags"].replace("", "NONE")
                .value_counts().head(40).to_dict().items()
            )
        },
        "tag_item_counts": dict(
            sorted(tag_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ),
        "activation_ready": 0,
        "auto_live_promotions": 0,
    }

    SUMMARY_FILE.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 48)
    print(" Known-Provenance Policy Gap Audit")
    print("=" * 48)
    print(f"Known-provenance gaps:           {summary['total_items']:>6}")
    print()
    print("Policy-gap families:")
    for label, count in sorted(
        summary["policy_gap_family_counts"].items(),
        key=lambda pair: (-pair[1], pair[0]),
    ):
        print(f"  {label:<30} {count:>6}")
    print()
    print("Top provenance tags:")
    for label, count in list(summary["tag_item_counts"].items())[:30]:
        print(f"  {label:<34} {count:>6}")
    print()
    print("Activation ready: 0")
    print("Auto live promotions: 0")
    print()
    print(f"Audit:   {OUTPUT_FILE}")
    print(f"Summary: {SUMMARY_FILE}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
