from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

AUDIT_FILE = REPORTS / "candidate-market-audit.csv"
SOURCE_AUDIT_FILE = REPORTS / "scroll-puppet-source-audit.csv"
SCROLL_SHOP_FILE = GENERATED / "scroll-shop-price-evidence.csv"
PUPPET_MIRROR_FILE = REPORTS / "puppet-internal-mirror-audit.csv"

OUTPUT_FILE = GENERATED / "scroll-puppet-policy.csv"
SUMMARY_FILE = REPORTS / "scroll-puppet-policy-summary.json"


class ScrollPuppetPolicyError(RuntimeError):
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


def require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ScrollPuppetPolicyError(
            f"{label} missing required columns: " + ", ".join(missing)
        )


def main() -> int:
    for path in [AUDIT_FILE, SOURCE_AUDIT_FILE, SCROLL_SHOP_FILE, PUPPET_MIRROR_FILE]:
        if not path.exists():
            raise ScrollPuppetPolicyError(f"Missing required input: {path}")

    audit = pd.read_csv(AUDIT_FILE, low_memory=False)
    source = pd.read_csv(SOURCE_AUDIT_FILE, low_memory=False)
    scroll_shop = pd.read_csv(SCROLL_SHOP_FILE, low_memory=False)
    puppet = pd.read_csv(PUPPET_MIRROR_FILE, low_memory=False)

    require_columns(
        audit,
        {"audit_bucket", "itemid", "name"},
        "candidate-market-audit.csv",
    )
    require_columns(
        source,
        {
            "audit_bucket", "itemid", "name", "source_signals",
            "quest_reference_count", "mission_reference_count",
            "reward_like_reference_count", "missing_item_enum_constant",
        },
        "scroll-puppet-source-audit.csv",
    )
    require_columns(
        scroll_shop,
        {
            "itemid", "name", "vendor_price_min", "vendor_price_max",
            "has_hard_floor", "buyer_allowed", "seller_candidate",
        },
        "scroll-shop-price-evidence.csv",
    )
    require_columns(
        puppet,
        {
            "internal_itemid", "name", "internal_type",
            "has_same_name_mirror", "strong_internal_mirror", "mirror_itemid",
        },
        "puppet-internal-mirror-audit.csv",
    )

    target = audit[
        audit["audit_bucket"].isin(["SCROLL_POLICY", "PUPPET_POLICY"])
    ][["audit_bucket", "itemid", "name"]].copy()

    if len(target) != 669:
        raise ScrollPuppetPolicyError(f"Expected 669 target items, found {len(target)}")

    source_small = source[
        [
            "itemid", "source_signals", "quest_reference_count",
            "mission_reference_count", "reward_like_reference_count",
            "missing_item_enum_constant",
        ]
    ].copy()

    scroll_small = scroll_shop[
        [
            "itemid", "vendor_price_min", "vendor_price_max",
            "has_hard_floor", "buyer_allowed", "seller_candidate",
        ]
    ].copy()

    puppet_small = puppet[
        [
            "internal_itemid", "internal_type", "has_same_name_mirror",
            "strong_internal_mirror", "mirror_itemid",
        ]
    ].rename(columns={"internal_itemid": "itemid"})

    merged = (
        target
        .merge(source_small, on="itemid", how="left", validate="one_to_one")
        .merge(scroll_small, on="itemid", how="left", validate="one_to_one")
        .merge(puppet_small, on="itemid", how="left", validate="one_to_one")
    )

    rows: list[dict[str, Any]] = []

    for _, row in merged.iterrows():
        bucket = clean_text(row.get("audit_bucket"))
        itemid = as_int(row.get("itemid"))

        if bucket == "PUPPET_POLICY":
            if as_int(row.get("internal_type")) != 4:
                raise ScrollPuppetPolicyError(
                    f"Puppet record {itemid} is not item type 4."
                )

            policy_family = "PUPPET_INTERNAL"
            recommended_class = "BLOCKED"
            policy_reason = "INTERNAL_PUPPET_RECORD"
            seller_eligible = 0
            buyer_eligible = 0
            has_hard_floor = 0
            vendor_price_min = 0
            vendor_price_max = 0
            manual_review_required = 0
            source_confidence = "HIGH"

        elif bucket == "SCROLL_POLICY":
            policy_family = "SCROLL"
            has_hard_floor = as_int(row.get("has_hard_floor"))

            if has_hard_floor:
                recommended_class = "SCARCE"
                policy_reason = "TRUSTED_NPC_VENDOR_SCROLL"
                seller_eligible = 1
                buyer_eligible = 0
                vendor_price_min = as_int(row.get("vendor_price_min"))
                vendor_price_max = as_int(row.get("vendor_price_max"))
                manual_review_required = 0
                source_confidence = "HIGH"
            else:
                recommended_class = "PROTECTED"
                seller_eligible = 0
                buyer_eligible = 0
                vendor_price_min = 0
                vendor_price_max = 0
                source_confidence = "LOW"

                reward_refs = as_int(row.get("reward_like_reference_count"))
                quest_refs = as_int(row.get("quest_reference_count"))
                mission_refs = as_int(row.get("mission_reference_count"))

                if reward_refs > 0:
                    policy_reason = "SCROLL_REWARD_SOURCE_AUDIT_REQUIRED"
                    manual_review_required = 1
                elif quest_refs > 0:
                    policy_reason = "SCROLL_QUEST_REFERENCE_AUDIT_REQUIRED"
                    manual_review_required = 1
                elif mission_refs > 0:
                    policy_reason = "SCROLL_MISSION_REFERENCE_AUDIT_REQUIRED"
                    manual_review_required = 1
                else:
                    policy_reason = "SCROLL_SOURCE_UNKNOWN"
                    manual_review_required = 0
        else:
            raise ScrollPuppetPolicyError(
                f"Unexpected bucket {bucket!r} for item {itemid}"
            )

        rows.append(
            {
                "itemid": itemid,
                "name": clean_text(row.get("name")),
                "policy_family": policy_family,
                "recommended_class": recommended_class,
                "policy_reason": policy_reason,
                "seller_eligible": seller_eligible,
                "buyer_eligible": buyer_eligible,
                "has_hard_floor": has_hard_floor,
                "vendor_price_min": vendor_price_min,
                "vendor_price_max": vendor_price_max,
                "manual_review_required": manual_review_required,
                "source_confidence": source_confidence,
                "source_signals": clean_text(row.get("source_signals")),
                "quest_reference_count": as_int(row.get("quest_reference_count")),
                "mission_reference_count": as_int(row.get("mission_reference_count")),
                "reward_like_reference_count": as_int(row.get("reward_like_reference_count")),
                "missing_item_enum_constant": as_int(row.get("missing_item_enum_constant")),
                "internal_type": as_int(row.get("internal_type")),
                "has_same_name_mirror": as_int(row.get("has_same_name_mirror")),
                "strong_internal_mirror": as_int(row.get("strong_internal_mirror")),
                "mirror_itemid": as_int(row.get("mirror_itemid")),
                "activation_ready": 0,
                "auto_live_promotion": 0,
            }
        )

    output = pd.DataFrame(rows).sort_values(
        by=["policy_family", "itemid"], kind="stable"
    )

    if len(output) != 669:
        raise ScrollPuppetPolicyError("Final policy row count changed.")

    if output["activation_ready"].sum() != 0:
        raise ScrollPuppetPolicyError("Scroll/Puppet policy activated items.")

    if output["auto_live_promotion"].sum() != 0:
        raise ScrollPuppetPolicyError("Scroll/Puppet policy attempted live promotion.")

    puppet_rows = output[output["policy_family"] == "PUPPET_INTERNAL"]
    if len(puppet_rows) != 127 or not (puppet_rows["recommended_class"] == "BLOCKED").all():
        raise ScrollPuppetPolicyError("Puppet internal blocking invariant failed.")

    trusted_scrolls = output[
        (output["policy_family"] == "SCROLL")
        & (output["has_hard_floor"] == 1)
    ]
    if len(trusted_scrolls) != 375:
        raise ScrollPuppetPolicyError(
            f"Expected 375 trusted vendor scrolls, found {len(trusted_scrolls)}."
        )

    output.to_csv(OUTPUT_FILE, index=False)

    summary = {
        "status": "PASS",
        "total_items": int(len(output)),
        "trusted_vendor_scrolls": int(len(trusted_scrolls)),
        "protected_scrolls": int(
            (
                (output["policy_family"] == "SCROLL")
                & (output["recommended_class"] == "PROTECTED")
            ).sum()
        ),
        "blocked_internal_puppet_records": int(
            (
                (output["policy_family"] == "PUPPET_INTERNAL")
                & (output["recommended_class"] == "BLOCKED")
            ).sum()
        ),
        "seller_eligible": int(output["seller_eligible"].sum()),
        "buyer_eligible": int(output["buyer_eligible"].sum()),
        "manual_review_required": int(output["manual_review_required"].sum()),
        "activation_ready": 0,
        "auto_live_promotions": 0,
        "policy_reasons": {
            str(k): int(v)
            for k, v in output["policy_reason"].value_counts().to_dict().items()
        },
    }

    SUMMARY_FILE.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("======================================")
    print(" Final Scroll + Puppet Policy")
    print("======================================")
    print(f"Total items:                     {summary['total_items']:>6}")
    print(f"Trusted vendor scrolls:          {summary['trusted_vendor_scrolls']:>6}")
    print(f"Protected scrolls:               {summary['protected_scrolls']:>6}")
    print(f"Blocked Puppet internal records: {summary['blocked_internal_puppet_records']:>6}")
    print()
    print(f"Seller eligible:                 {summary['seller_eligible']:>6}")
    print(f"Buyer eligible:                  {summary['buyer_eligible']:>6}")
    print(f"Manual review required:          {summary['manual_review_required']:>6}")
    print("Activation ready: 0")
    print("Auto live promotions: 0")
    print()
    print(f"Generated: {OUTPUT_FILE}")
    print(f"Summary:   {SUMMARY_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
