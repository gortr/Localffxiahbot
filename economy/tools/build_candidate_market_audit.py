from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path.home() / "ffxiahbot"
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"
OVERRIDES = ROOT / "economy" / "overrides"

POLICY_FILE = GENERATED / "candidate-market-policy.csv"
AUDIT_FILE = REPORTS / "candidate-market-audit.csv"
SUMMARY_FILE = REPORTS / "candidate-market-audit-summary.json"
OVERRIDE_FILE = OVERRIDES / "candidate-policy-overrides.csv"

VALID_CLASSES = {"BLOCKED", "PROTECTED", "DEMAND_ONLY", "SCARCE", "NORMAL", "STAPLE"}


class AuditBuildError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or pd.isna(value):
        return False
    return clean_text(value).lower() in {"1", "true", "yes", "y"}


def as_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0
    return int(value)


def audit_bucket(row: pd.Series) -> tuple[str, int]:
    reason = clean_text(row.get("candidate_reason"))
    blocker = clean_text(row.get("future_activation_blocker"))
    current_seed = as_bool(row.get("current_seed_sell_allowed")) or as_bool(row.get("current_seed_buy_allowed"))

    if current_seed:
        return "CURRENT_SEED", 9

    mapping = {
        "NOTORIOUS_ONLY_RETAIL_SOURCE_AUDIT_REQUIRED": ("NOTORIOUS_SOURCE_AUDIT", 1),
        "SPECIAL_MOB_SOURCE_ONLY": ("SPECIAL_SOURCE_AUDIT", 1),
        "NOTORIOUS_OR_SPECIAL_SOURCE_ONLY": ("SPECIAL_SOURCE_AUDIT", 1),
        "QUEST_ONLY_FISH": ("PROTECTED_FISHING", 1),
        "KEYITEM_GATED_FISH": ("PROTECTED_FISHING", 1),
        "LEGENDARY_FISH": ("PROTECTED_FISHING", 1),
        "DISABLED_FISH": ("PROTECTED_FISHING", 1),
        "RESTRICTED_CRAFT_OUTPUT_ONLY": ("RESTRICTED_CRAFT", 1),
        "SCROLL_POLICY_PENDING": ("SCROLL_POLICY", 2),
        "PUPPET_POLICY_PENDING": ("PUPPET_POLICY", 2),
        "HQ_EQUIPMENT_REQUIRES_MATURITY": ("HQ_MATURITY_REVIEW", 3),
        "HQ_GOOD_REQUIRES_MATURITY": ("HQ_MATURITY_REVIEW", 3),
        "ORDINARY_RARE_EQUIPMENT": ("RARE_ITEM_REVIEW", 3),
        "ORDINARY_RARE_ITEM": ("RARE_ITEM_REVIEW", 3),
        "UNKNOWN_ACQUISITION": ("UNKNOWN_OR_UNCLASSIFIED_SOURCE", 4),
        "KNOWN_PROVENANCE_NOT_YET_CLASSIFIED": ("UNKNOWN_OR_UNCLASSIFIED_SOURCE", 4),
    }

    if reason in mapping:
        return mapping[reason]

    if blocker == "PRICING_NOT_READY":
        return "PRICING_REVIEW", 4

    if as_bool(row.get("policy_ready")):
        return "NEW_POLICY_READY", 5

    if as_bool(row.get("review_required")):
        return "GENERAL_REVIEW", 6

    return "NO_AUDIT_REQUIRED", 9


def ensure_and_validate_overrides(valid_itemids: set[int]) -> int:
    OVERRIDES.mkdir(parents=True, exist_ok=True)

    if not OVERRIDE_FILE.exists():
        pd.DataFrame(columns=["itemid", "action", "market_class", "reason"]).to_csv(OVERRIDE_FILE, index=False)

    frame = pd.read_csv(OVERRIDE_FILE, low_memory=False)
    required = {"itemid", "action", "market_class", "reason"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise AuditBuildError("candidate-policy-overrides.csv missing columns: " + ", ".join(missing))

    rows = frame[frame["itemid"].notna()].copy()
    if rows.empty:
        return 0

    rows["itemid"] = rows["itemid"].astype(int)
    if rows["itemid"].duplicated().any():
        dupes = rows.loc[rows["itemid"].duplicated(keep=False), "itemid"].unique().tolist()
        raise AuditBuildError(f"Duplicate override item IDs: {dupes[:20]}")

    unknown = sorted(set(rows["itemid"]) - valid_itemids)
    if unknown:
        raise AuditBuildError(f"Overrides reference unknown item IDs: {unknown[:20]}")

    allowed_actions = {"SET_CLASS", "KEEP_PROTECTED", "BLOCK"}
    actions = set(rows["action"].map(clean_text))
    invalid_actions = sorted(actions - allowed_actions)
    if invalid_actions:
        raise AuditBuildError(f"Invalid override actions: {invalid_actions}")

    set_class = rows[rows["action"].map(clean_text) == "SET_CLASS"]
    invalid_classes = sorted(set(set_class["market_class"].map(clean_text)) - VALID_CLASSES)
    if invalid_classes:
        raise AuditBuildError(f"Invalid override classes: {invalid_classes}")

    if (rows["reason"].map(clean_text) == "").any():
        raise AuditBuildError("Every override must include a reason.")

    return len(rows)


def main() -> int:
    REPORTS.mkdir(parents=True, exist_ok=True)

    if not POLICY_FILE.exists():
        raise AuditBuildError(f"Missing candidate policy: {POLICY_FILE}")

    policy = pd.read_csv(POLICY_FILE, low_memory=False)
    required = {
        "itemid", "name", "candidate_class", "candidate_reason", "review_required",
        "pricing_ready", "policy_ready", "activation_ready", "future_activation_blocker",
        "auto_live_promotion", "current_seed_sell_allowed", "current_seed_buy_allowed",
        "vendor_item", "mob_source_class", "has_ordinary_mob_source",
        "has_notorious_mob_source", "has_special_mob_source", "craft_nq_output",
        "craft_hq_output", "restricted_nq_output", "restricted_hq_output", "fishing",
        "rare", "can_equip", "stack_size", "base_sell", "rejection_reason",
    }
    missing = sorted(required - set(policy.columns))
    if missing:
        raise AuditBuildError("candidate-market-policy.csv missing columns: " + ", ".join(missing))

    if policy["itemid"].duplicated().any():
        raise AuditBuildError("Candidate policy contains duplicate item IDs.")
    if int(policy["current_seed_sell_allowed"].sum()) != 167:
        raise AuditBuildError("Current Seed seller count is not 167.")
    if int(policy["current_seed_buy_allowed"].sum()) != 159:
        raise AuditBuildError("Current Seed buyer count is not 159.")
    if int(policy["auto_live_promotion"].sum()) != 0:
        raise AuditBuildError("Candidate policy contains live promotions.")

    override_count = ensure_and_validate_overrides(set(policy["itemid"].astype(int)))

    rows: list[dict[str, Any]] = []
    for _, row in policy.iterrows():
        bucket, priority = audit_bucket(row)
        if bucket in {"CURRENT_SEED", "NO_AUDIT_REQUIRED"}:
            continue
        rows.append({
            "audit_priority": priority,
            "audit_bucket": bucket,
            "itemid": as_int(row.get("itemid")),
            "name": clean_text(row.get("name")),
            "candidate_class": clean_text(row.get("candidate_class")),
            "candidate_reason": clean_text(row.get("candidate_reason")),
            "future_activation_blocker": clean_text(row.get("future_activation_blocker")),
            "review_required": as_int(row.get("review_required")),
            "pricing_ready": as_int(row.get("pricing_ready")),
            "policy_ready": as_int(row.get("policy_ready")),
            "vendor_item": as_int(row.get("vendor_item")),
            "mob_source_class": clean_text(row.get("mob_source_class")),
            "has_ordinary_mob_source": as_int(row.get("has_ordinary_mob_source")),
            "has_notorious_mob_source": as_int(row.get("has_notorious_mob_source")),
            "has_special_mob_source": as_int(row.get("has_special_mob_source")),
            "craft_nq_output": as_int(row.get("craft_nq_output")),
            "craft_hq_output": as_int(row.get("craft_hq_output")),
            "restricted_nq_output": as_int(row.get("restricted_nq_output")),
            "restricted_hq_output": as_int(row.get("restricted_hq_output")),
            "fishing": as_int(row.get("fishing")),
            "rare": as_int(row.get("rare")),
            "can_equip": as_int(row.get("can_equip")),
            "stack_size": as_int(row.get("stack_size")),
            "base_sell": as_int(row.get("base_sell")),
            "rejection_reason": clean_text(row.get("rejection_reason")),
        })

    audit = pd.DataFrame(rows)
    if not audit.empty:
        audit = audit.sort_values(["audit_priority", "audit_bucket", "itemid"], kind="stable")
    audit.to_csv(AUDIT_FILE, index=False)

    bucket_counts = audit["audit_bucket"].value_counts().to_dict() if not audit.empty else {}
    priority_counts = audit["audit_priority"].value_counts().sort_index().to_dict() if not audit.empty else {}

    summary = {
        "status": "PASS",
        "total_items": int(len(policy)),
        "audit_queue_items": int(len(audit)),
        "override_rows": int(override_count),
        "current_seed_seller": 167,
        "current_seed_buyer": 159,
        "auto_live_promotions": 0,
        "audit_buckets": {str(k): int(v) for k, v in bucket_counts.items()},
        "audit_priorities": {str(k): int(v) for k, v in priority_counts.items()},
    }
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print()
    print("======================================")
    print(" Candidate Market Audit")
    print("======================================")
    print(f"Audit queue items:     {summary['audit_queue_items']:>6}")
    print(f"Override rows:         {summary['override_rows']:>6}")
    print(f"Current Seed seller:   {summary['current_seed_seller']:>6}")
    print(f"Current Seed buyer:    {summary['current_seed_buyer']:>6}")
    print(f"Auto live promotions:  {summary['auto_live_promotions']:>6}")
    print()
    print("Audit buckets:")
    for bucket, count in sorted(summary["audit_buckets"].items()):
        print(f"  {bucket:<34} {count:>6}")
    print()
    print(f"Generated: {AUDIT_FILE}")
    print(f"Overrides: {OVERRIDE_FILE}")
    print(f"Summary:   {SUMMARY_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
