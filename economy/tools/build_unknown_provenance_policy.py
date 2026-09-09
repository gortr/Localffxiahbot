from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

LEDGER_FILE = ROOT / "economy" / "reports" / "unknown-provenance-resolution-ledger-v2.csv"
KNOWN_FILE = ROOT / "economy" / "reports" / "known-provenance-acquisition-resolution.csv"

OUTPUT_FILE = ROOT / "economy" / "generated" / "unknown-provenance-policy.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "unknown-provenance-policy-summary.json"


class UnknownPolicyError(RuntimeError):
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


def main() -> int:
    for path in [
        LEDGER_FILE,
        KNOWN_FILE,
    ]:
        if not path.exists():
            raise UnknownPolicyError(
                f"Missing required input: {path}"
            )

    ledger = pd.read_csv(
        LEDGER_FILE,
        low_memory=False,
    )

    known = pd.read_csv(
        KNOWN_FILE,
        low_memory=False,
    )

    if len(ledger) != 5011:
        raise UnknownPolicyError(
            f"Expected 5011 ledger rows, found {len(ledger)}"
        )

    known_by_item = {
        as_int(row.get("itemid")):
            row.to_dict()
        for _, row in known.iterrows()
    }

    rows: list[dict[str, Any]] = []

    for _, row in ledger.iterrows():
        itemid = as_int(
            row.get("itemid")
        )

        source_status = clean_text(
            row.get("source_status")
        )

        resolution = clean_text(
            row.get("resolution")
        )

        known_row = known_by_item.get(
            itemid
        )

        policy_family = ""
        acquisition_state = ""
        source_gate_open = 0
        synthetic_supply_source_allowed = 0
        pricing_gate_required = 0
        seller_source_eligible = 0
        buyer_source_eligible = 0
        keep_protected = 1
        policy_reason = ""

        # Overlay the corrected acquisition-vs-usage audit first.
        if known_row is not None:
            known_resolution = clean_text(
                known_row.get("resolution")
            )

            if known_resolution == "ORDINARY_MOB_SOURCE":
                policy_family = "ORDINARY_SOURCE"
                acquisition_state = "RESOLVED"
                source_gate_open = 1
                synthetic_supply_source_allowed = 1
                seller_source_eligible = 1
                buyer_source_eligible = 1
                keep_protected = 0
                policy_reason = "ORDINARY_MOB_ACQUISITION"

            elif known_resolution == "VENDOR_SOURCE_PRICING_REVIEW":
                policy_family = "VENDOR_PRICING_BLOCK"
                acquisition_state = "RESOLVED"
                source_gate_open = 1
                synthetic_supply_source_allowed = 0
                pricing_gate_required = 1
                seller_source_eligible = 0
                buyer_source_eligible = 0
                keep_protected = 1
                policy_reason = "NPC_VENDOR_SOURCE_PRICE_UNRESOLVED"

            elif known_resolution == "TRUSTED_VENDOR_SOURCE":
                policy_family = "ORDINARY_VENDOR_SOURCE"
                acquisition_state = "RESOLVED"
                source_gate_open = 1
                synthetic_supply_source_allowed = 1
                pricing_gate_required = 1
                seller_source_eligible = 1
                buyer_source_eligible = 0
                keep_protected = 0
                policy_reason = "TRUSTED_NPC_VENDOR_SOURCE"

            elif known_resolution in {
                "SPECIAL_MOB_SOURCE",
            }:
                policy_family = "PROTECTED_SOURCE"
                acquisition_state = "RESOLVED_PROTECTED"
                source_gate_open = 1
                synthetic_supply_source_allowed = 0
                seller_source_eligible = 0
                buyer_source_eligible = 0
                keep_protected = 1
                policy_reason = "SPECIAL_MOB_ACQUISITION"

            elif known_resolution in {
                "MOB_SOURCE_CONTEXT_REVIEW",
                "OTHER_ACQUISITION_TAG_REVIEW",
            }:
                policy_family = "SOURCE_REVIEW"
                acquisition_state = "SOURCE_FOUND_REVIEW"
                source_gate_open = 0
                synthetic_supply_source_allowed = 0
                seller_source_eligible = 0
                buyer_source_eligible = 0
                keep_protected = 1
                policy_reason = "SOURCE_CONTEXT_REVIEW_REQUIRED"

            elif known_resolution in {
                "USAGE_ONLY_NOT_ACQUISITION",
                "NO_ACQUISITION_EVIDENCE",
            }:
                policy_family = "UNKNOWN_ACQUISITION"
                acquisition_state = "UNKNOWN"
                source_gate_open = 0
                synthetic_supply_source_allowed = 0
                seller_source_eligible = 0
                buyer_source_eligible = 0
                keep_protected = 1
                policy_reason = "USAGE_METADATA_IS_NOT_ACQUISITION"

            else:
                raise UnknownPolicyError(
                    f"Unhandled corrected acquisition resolution "
                    f"{known_resolution!r} for item {itemid}"
                )

        elif source_status == "RESOLVED":
            policy_family = "ORDINARY_SOURCE"
            acquisition_state = "RESOLVED"
            source_gate_open = 1
            synthetic_supply_source_allowed = 1
            seller_source_eligible = 1
            buyer_source_eligible = 1
            keep_protected = 0
            policy_reason = (
                clean_text(
                    row.get("repository_policy_reason")
                )
                or resolution
            )

        elif source_status == "RESOLVED_PROTECTED":
            policy_family = "PROTECTED_SOURCE"
            acquisition_state = "RESOLVED_PROTECTED"
            source_gate_open = 1
            synthetic_supply_source_allowed = 0
            seller_source_eligible = 0
            buyer_source_eligible = 0
            keep_protected = 1
            policy_reason = (
                clean_text(
                    row.get("repository_policy_reason")
                )
                or resolution
            )

        elif source_status == "SOURCE_FOUND_REVIEW":
            policy_family = "SOURCE_REVIEW"
            acquisition_state = "SOURCE_FOUND_REVIEW"
            source_gate_open = 0
            synthetic_supply_source_allowed = 0
            seller_source_eligible = 0
            buyer_source_eligible = 0
            keep_protected = 1
            policy_reason = (
                clean_text(
                    row.get("repository_policy_reason")
                )
                or resolution
            )

        elif source_status.startswith("UNRESOLVED"):
            policy_family = (
                "INTERNAL_REVIEW"
                if source_status == "UNRESOLVED_INTERNAL"
                else "UNKNOWN_ACQUISITION"
            )
            acquisition_state = "UNKNOWN"
            source_gate_open = 0
            synthetic_supply_source_allowed = 0
            seller_source_eligible = 0
            buyer_source_eligible = 0
            keep_protected = 1
            policy_reason = (
                "INTERNAL_OR_UNUSED_CANDIDATE"
                if source_status == "UNRESOLVED_INTERNAL"
                else "ACQUISITION_SOURCE_NOT_ESTABLISHED"
            )

        else:
            raise UnknownPolicyError(
                f"Unhandled source status {source_status!r} "
                f"for item {itemid}"
            )

        rows.append(
            {
                "itemid": itemid,
                "name": clean_text(
                    row.get("name")
                ),
                "item_type": as_int(
                    row.get("item_type")
                ),
                "ah_category": as_int(
                    row.get("ah_category")
                ),
                "stack_size": as_int(
                    row.get("stack_size")
                ),
                "policy_family": policy_family,
                "acquisition_state": acquisition_state,
                "source_gate_open": source_gate_open,
                "synthetic_supply_source_allowed":
                    synthetic_supply_source_allowed,
                "pricing_gate_required":
                    pricing_gate_required,
                "seller_source_eligible":
                    seller_source_eligible,
                "buyer_source_eligible":
                    buyer_source_eligible,
                "keep_protected": keep_protected,
                "policy_reason": policy_reason,
                "prior_resolution": resolution,
                "source_types": clean_text(
                    row.get("source_types")
                ),
                "repository_source_families": clean_text(
                    row.get("repository_source_families")
                ),
                "activation_ready": 0,
                "auto_live_promotion": 0,
            }
        )

    output = pd.DataFrame(
        rows
    ).sort_values(
        by=[
            "policy_family",
            "item_type",
            "itemid",
        ],
        kind="stable",
    )

    if len(output) != 5011:
        raise UnknownPolicyError(
            f"Expected 5011 policy rows, found {len(output)}"
        )

    if int(
        output["activation_ready"].sum()
    ) != 0:
        raise UnknownPolicyError(
            "Unknown provenance policy activated items."
        )

    if int(
        output["auto_live_promotion"].sum()
    ) != 0:
        raise UnknownPolicyError(
            "Unknown provenance policy attempted live promotion."
        )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    summary = {
        "status": "PASS",
        "total_items": int(len(output)),
        "policy_family_counts": {
            str(k): int(v)
            for k, v in (
                output["policy_family"]
                .value_counts()
                .to_dict()
                .items()
            )
        },
        "acquisition_state_counts": {
            str(k): int(v)
            for k, v in (
                output["acquisition_state"]
                .value_counts()
                .to_dict()
                .items()
            )
        },
        "source_gate_open": int(
            output["source_gate_open"].sum()
        ),
        "synthetic_supply_source_allowed": int(
            output[
                "synthetic_supply_source_allowed"
            ].sum()
        ),
        "pricing_gate_required": int(
            output["pricing_gate_required"].sum()
        ),
        "seller_source_eligible": int(
            output["seller_source_eligible"].sum()
        ),
        "buyer_source_eligible": int(
            output["buyer_source_eligible"].sum()
        ),
        "protected_by_source_policy": int(
            output["keep_protected"].sum()
        ),
        "manual_review_required_for_ahbot": 0,
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
    print(" Final Unknown-Provenance Policy")
    print("=" * 52)
    print(
        f"Total items:                         "
        f"{summary['total_items']:>6}"
    )
    print(
        f"Source gate open:                    "
        f"{summary['source_gate_open']:>6}"
    )
    print(
        f"Synthetic source allowed:            "
        f"{summary['synthetic_supply_source_allowed']:>6}"
    )
    print(
        f"Pricing gate required:               "
        f"{summary['pricing_gate_required']:>6}"
    )
    print(
        f"Protected by source policy:          "
        f"{summary['protected_by_source_policy']:>6}"
    )
    print(
        "Manual review required for AHBot:         0"
    )
    print(
        "Activation ready:                         0"
    )
    print(
        "Auto live promotions:                     0"
    )
    print()
    print("Policy families:")
    for label, count in sorted(
        summary["policy_family_counts"].items(),
        key=lambda pair: (-pair[1], pair[0]),
    ):
        print(
            f"  {label:<30} "
            f"{count:>6}"
        )
    print()
    print(f"Generated: {OUTPUT_FILE}")
    print(f"Summary:   {SUMMARY_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
