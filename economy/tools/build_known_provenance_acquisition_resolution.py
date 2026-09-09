from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

AUDIT_FILE = ROOT / "economy" / "reports" / "known-provenance-policy-gap-audit.csv"

OUTPUT_FILE = ROOT / "economy" / "reports" / "known-provenance-acquisition-resolution.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "known-provenance-acquisition-resolution-summary.json"


ACQUISITION_TAGS = {
    "NPC_VENDOR",
    "MOB_DROP",
    "FISHING",
    "CRAFT_NQ_OUTPUT",
    "CRAFT_HQ_OUTPUT",
    "DESYNTH_OUTPUT",
}

USAGE_ONLY_TAGS = {
    "CRAFT_INPUT",
    "RESTRICTED_CRAFT_INPUT",
    "DESYNTH_INPUT",
}


class AcquisitionResolutionError(RuntimeError):
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


def main() -> int:
    if not AUDIT_FILE.exists():
        raise AcquisitionResolutionError(
            f"Missing input: {AUDIT_FILE}"
        )

    audit = pd.read_csv(
        AUDIT_FILE,
        low_memory=False,
    )

    if len(audit) != 575:
        raise AcquisitionResolutionError(
            f"Expected 575 rows, found {len(audit)}"
        )

    rows: list[dict[str, Any]] = []

    for _, row in audit.iterrows():
        itemid = as_int(row.get("itemid"))
        tags = split_tags(row.get("provenance_tags"))
        acquisition_tags = sorted(tags & ACQUISITION_TAGS)
        usage_tags = sorted(tags & USAGE_ONLY_TAGS)

        vendor_item = as_int(row.get("vendor_item"))
        hard_floor = as_int(row.get("has_vendor_price_floor"))
        mob_source_class = clean_text(
            row.get("mob_source_class")
        )

        resolution = ""
        source_status = ""
        policy_hint = ""
        seller_eligible = 0
        buyer_eligible = 0
        manual_review_required = 0

        if "NPC_VENDOR" in tags:
            if hard_floor:
                resolution = "TRUSTED_VENDOR_SOURCE"
                source_status = "RESOLVED"
                policy_hint = "NORMAL_OR_STAPLE_WITH_VENDOR_FLOOR"
                seller_eligible = 1
                buyer_eligible = 0
            else:
                resolution = "VENDOR_SOURCE_PRICING_REVIEW"
                source_status = "SOURCE_FOUND_REVIEW"
                policy_hint = "KEEP_PROTECTED_UNTIL_PRICE_RESOLVED"
                seller_eligible = 0
                buyer_eligible = 0
                manual_review_required = 1

        elif "MOB_DROP" in tags:
            if mob_source_class == "ORDINARY_AVAILABLE":
                resolution = "ORDINARY_MOB_SOURCE"
                source_status = "RESOLVED"
                policy_hint = "NORMAL_OR_STAPLE_REVIEW"
                seller_eligible = 1
                buyer_eligible = 1

            elif mob_source_class in {
                "NOTORIOUS_ONLY",
                "SPECIAL_ONLY",
                "NOTORIOUS_OR_SPECIAL_ONLY",
            }:
                resolution = "SPECIAL_MOB_SOURCE"
                source_status = "RESOLVED_PROTECTED"
                policy_hint = "PROTECTED_OR_DEMAND_ONLY"
                seller_eligible = 0
                buyer_eligible = 0

            else:
                resolution = "MOB_SOURCE_CONTEXT_REVIEW"
                source_status = "SOURCE_FOUND_REVIEW"
                policy_hint = "KEEP_PROTECTED"
                seller_eligible = 0
                buyer_eligible = 0
                manual_review_required = 1

        elif acquisition_tags:
            resolution = "OTHER_ACQUISITION_TAG_REVIEW"
            source_status = "SOURCE_FOUND_REVIEW"
            policy_hint = "POLICY_RULE_REQUIRED"
            manual_review_required = 1

        elif usage_tags:
            resolution = "USAGE_ONLY_NOT_ACQUISITION"
            source_status = "UNKNOWN_ACQUISITION"
            policy_hint = "RETURN_TO_SOURCE_DISCOVERY"
            seller_eligible = 0
            buyer_eligible = 0
            manual_review_required = 0

        else:
            resolution = "NO_ACQUISITION_EVIDENCE"
            source_status = "UNKNOWN_ACQUISITION"
            policy_hint = "RETURN_TO_SOURCE_DISCOVERY"

        rows.append(
            {
                "itemid": itemid,
                "name": clean_text(row.get("name")),
                "item_type": as_int(row.get("item_type")),
                "ah_category": as_int(row.get("ah_category")),
                "stack_size": as_int(row.get("stack_size")),
                "provenance_tags": "|".join(sorted(tags)),
                "acquisition_tags": "|".join(acquisition_tags),
                "usage_only_tags": "|".join(usage_tags),
                "vendor_item": vendor_item,
                "has_vendor_price_floor": hard_floor,
                "mob_source_class": mob_source_class,
                "resolution": resolution,
                "source_status": source_status,
                "policy_hint": policy_hint,
                "seller_eligible": seller_eligible,
                "buyer_eligible": buyer_eligible,
                "manual_review_required": manual_review_required,
                "activation_ready": 0,
                "auto_live_promotion": 0,
            }
        )

    output = pd.DataFrame(rows).sort_values(
        by=[
            "source_status",
            "resolution",
            "item_type",
            "itemid",
        ],
        kind="stable",
    )

    if len(output) != 575:
        raise AcquisitionResolutionError(
            f"Expected 575 output rows, found {len(output)}"
        )

    if int(output["activation_ready"].sum()) != 0:
        raise AcquisitionResolutionError(
            "Acquisition resolution activated items."
        )

    if int(output["auto_live_promotion"].sum()) != 0:
        raise AcquisitionResolutionError(
            "Acquisition resolution attempted live promotion."
        )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    summary = {
        "status": "PASS",
        "total_items": int(len(output)),
        "source_status_counts": {
            str(k): int(v)
            for k, v in (
                output["source_status"]
                .value_counts()
                .to_dict()
                .items()
            )
        },
        "resolution_counts": {
            str(k): int(v)
            for k, v in (
                output["resolution"]
                .value_counts()
                .to_dict()
                .items()
            )
        },
        "usage_only_returned_to_unknown": int(
            (
                output["resolution"]
                == "USAGE_ONLY_NOT_ACQUISITION"
            ).sum()
        ),
        "trusted_vendor_sources": int(
            (
                output["resolution"]
                == "TRUSTED_VENDOR_SOURCE"
            ).sum()
        ),
        "vendor_pricing_review": int(
            (
                output["resolution"]
                == "VENDOR_SOURCE_PRICING_REVIEW"
            ).sum()
        ),
        "ordinary_mob_sources": int(
            (
                output["resolution"]
                == "ORDINARY_MOB_SOURCE"
            ).sum()
        ),
        "special_mob_sources": int(
            (
                output["resolution"]
                == "SPECIAL_MOB_SOURCE"
            ).sum()
        ),
        "mob_source_review": int(
            (
                output["resolution"]
                == "MOB_SOURCE_CONTEXT_REVIEW"
            ).sum()
        ),
        "seller_eligible": int(
            output["seller_eligible"].sum()
        ),
        "buyer_eligible": int(
            output["buyer_eligible"].sum()
        ),
        "manual_review_required": int(
            output["manual_review_required"].sum()
        ),
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
    print("=" * 52)
    print(" Known-Provenance Acquisition Resolution")
    print("=" * 52)
    print(
        f"Total items:                         "
        f"{summary['total_items']:>6}"
    )
    print(
        f"Usage-only returned to unknown:      "
        f"{summary['usage_only_returned_to_unknown']:>6}"
    )
    print(
        f"Trusted vendor sources:              "
        f"{summary['trusted_vendor_sources']:>6}"
    )
    print(
        f"Vendor pricing review:               "
        f"{summary['vendor_pricing_review']:>6}"
    )
    print(
        f"Ordinary mob sources:                "
        f"{summary['ordinary_mob_sources']:>6}"
    )
    print(
        f"Special mob sources:                 "
        f"{summary['special_mob_sources']:>6}"
    )
    print(
        f"Mob source review:                   "
        f"{summary['mob_source_review']:>6}"
    )
    print()
    print(
        f"Seller eligible:                     "
        f"{summary['seller_eligible']:>6}"
    )
    print(
        f"Buyer eligible:                      "
        f"{summary['buyer_eligible']:>6}"
    )
    print(
        f"Manual review required:              "
        f"{summary['manual_review_required']:>6}"
    )
    print("Activation ready: 0")
    print("Auto live promotions: 0")
    print()
    print(f"Resolution: {OUTPUT_FILE}")
    print(f"Summary:    {SUMMARY_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
