from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

REPORTS = ROOT / "economy" / "reports"
GENERATED = ROOT / "economy" / "generated"

AUDIT_FILE = REPORTS / "candidate-market-audit.csv"
POLICY_FILE = GENERATED / "candidate-market-policy.csv"
PROVENANCE_FILE = GENERATED / "item-provenance.csv"
MOB_FILE = GENERATED / "mob-source-classification.csv"

OUTPUT_FILE = REPORTS / "priority1-source-audit.csv"
SUMMARY_FILE = REPORTS / "priority1-source-audit-summary.json"


class PriorityOneAuditError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip()

    if text.lower() == "nan":
        return ""

    return text


def as_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0

    return int(value)


def optional_columns(
    frame: pd.DataFrame,
    columns: list[str],
) -> pd.DataFrame:
    out = frame.copy()

    for column in columns:
        if column not in out.columns:
            out[column] = ""

    return out


def main() -> int:
    for path in [
        AUDIT_FILE,
        POLICY_FILE,
        PROVENANCE_FILE,
        MOB_FILE,
    ]:
        if not path.exists():
            raise PriorityOneAuditError(
                f"Missing required input: {path}"
            )

    audit = pd.read_csv(
        AUDIT_FILE,
        low_memory=False,
    )

    policy = pd.read_csv(
        POLICY_FILE,
        low_memory=False,
    )

    provenance = pd.read_csv(
        PROVENANCE_FILE,
        low_memory=False,
    )

    mob = pd.read_csv(
        MOB_FILE,
        low_memory=False,
    )

    required_audit = {
        "audit_priority",
        "audit_bucket",
        "itemid",
        "name",
        "candidate_class",
        "candidate_reason",
    }

    missing = sorted(
        required_audit
        - set(
            audit.columns
        )
    )

    if missing:
        raise PriorityOneAuditError(
            "candidate-market-audit.csv missing: "
            + ", ".join(
                missing
            )
        )

    q = audit[
        (audit["audit_priority"] == 1)
        &
        (
            audit["audit_bucket"]
            != "RESTRICTED_CRAFT"
        )
    ].copy()

    if len(q) != 32:
        raise PriorityOneAuditError(
            "Expected 32 non-restricted Priority-1 items, "
            f"found {len(q)}"
        )

    policy = optional_columns(
        policy,
        [
            "has_vendor_price_floor",
            "vendor_item",
            "pricing_ready",
            "stack_size",
            "rare",
            "can_equip",
            "base_sell",
        ],
    )

    provenance = optional_columns(
        provenance,
        [
            "provenance_tags",
            "fishing",
            "fishing_legendary",
            "fishing_required_keyitem",
            "fishing_quest_only",
            "fishing_disabled",
            "craft_input",
            "craft_nq_output",
            "craft_hq_output",
            "vendor_item",
        ],
    )

    mob = optional_columns(
        mob,
        [
            "mob_source_class",
            "has_ordinary_source",
            "has_notorious_source",
            "has_special_source",
            "ordinary_source_count",
            "notorious_source_count",
            "special_source_count",
            "source_origins",
            "zone_ids",
            "mob_names",
            "evidence_reasons",
        ],
    )

    merged = q.merge(
        policy[
            [
                "itemid",
                "has_vendor_price_floor",
                "vendor_item",
                "pricing_ready",
                "stack_size",
                "rare",
                "can_equip",
                "base_sell",
            ]
        ],
        on="itemid",
        how="left",
        validate="one_to_one",
        suffixes=(
            "",
            "_policy",
        ),
    ).merge(
        provenance[
            [
                "itemid",
                "provenance_tags",
                "fishing",
                "fishing_legendary",
                "fishing_required_keyitem",
                "fishing_quest_only",
                "fishing_disabled",
                "craft_input",
                "craft_nq_output",
                "craft_hq_output",
                "vendor_item",
            ]
        ].rename(
            columns={
                "vendor_item":
                    "provenance_vendor_item",
            }
        ),
        on="itemid",
        how="left",
        validate="one_to_one",
    ).merge(
        mob[
            [
                "itemid",
                "mob_source_class",
                "has_ordinary_source",
                "has_notorious_source",
                "has_special_source",
                "ordinary_source_count",
                "notorious_source_count",
                "special_source_count",
                "source_origins",
                "zone_ids",
                "mob_names",
                "evidence_reasons",
            ]
        ],
        on="itemid",
        how="left",
        validate="one_to_one",
    )

    rows: list[dict[str, Any]] = []

    for _, row in merged.iterrows():
        bucket = clean_text(
            row.get(
                "audit_bucket"
            )
        )

        if bucket == "PROTECTED_FISHING":
            recommendation = (
                "KEEP_PROTECTED"
            )

            if as_int(
                row.get(
                    "fishing_disabled"
                )
            ):
                reason = "DISABLED_FISH"

            elif as_int(
                row.get(
                    "fishing_quest_only"
                )
            ):
                reason = "QUEST_ONLY_FISH"

            elif as_int(
                row.get(
                    "fishing_required_keyitem"
                )
            ) > 0:
                reason = "KEYITEM_GATED_FISH"

            elif as_int(
                row.get(
                    "fishing_legendary"
                )
            ):
                reason = "LEGENDARY_FISH"

            else:
                reason = (
                    "PROTECTED_FISHING_REVIEW"
                )

            external_verification_required = 0

        elif bucket in {
            "NOTORIOUS_SOURCE_AUDIT",
            "SPECIAL_SOURCE_AUDIT",
        }:
            recommendation = (
                "SOURCE_CORRECTION_REVIEW"
            )

            reason = (
                "LSB_MOB_PROVENANCE_INCOMPLETE"
            )

            external_verification_required = 1

        else:
            raise PriorityOneAuditError(
                "Unexpected Priority-1 bucket: "
                f"{bucket}"
            )

        rows.append(
            {
                "itemid":
                    as_int(
                        row.get(
                            "itemid"
                        )
                    ),

                "name":
                    clean_text(
                        row.get(
                            "name"
                        )
                    ),

                "audit_bucket":
                    bucket,

                "candidate_class":
                    clean_text(
                        row.get(
                            "candidate_class"
                        )
                    ),

                "candidate_reason":
                    clean_text(
                        row.get(
                            "candidate_reason"
                        )
                    ),

                "recommended_action":
                    recommendation,

                "recommended_reason":
                    reason,

                "external_verification_required":
                    external_verification_required,

                "pricing_ready":
                    as_int(
                        row.get(
                            "pricing_ready"
                        )
                    ),

                "vendor_item":
                    as_int(
                        row.get(
                            "vendor_item"
                        )
                    ),

                "has_vendor_price_floor":
                    as_int(
                        row.get(
                            "has_vendor_price_floor"
                        )
                    ),

                "stack_size":
                    as_int(
                        row.get(
                            "stack_size"
                        )
                    ),

                "rare":
                    as_int(
                        row.get(
                            "rare"
                        )
                    ),

                "can_equip":
                    as_int(
                        row.get(
                            "can_equip"
                        )
                    ),

                "base_sell":
                    as_int(
                        row.get(
                            "base_sell"
                        )
                    ),

                "provenance_tags":
                    clean_text(
                        row.get(
                            "provenance_tags"
                        )
                    ),

                "fishing":
                    as_int(
                        row.get(
                            "fishing"
                        )
                    ),

                "fishing_legendary":
                    as_int(
                        row.get(
                            "fishing_legendary"
                        )
                    ),

                "fishing_required_keyitem":
                    as_int(
                        row.get(
                            "fishing_required_keyitem"
                        )
                    ),

                "fishing_quest_only":
                    as_int(
                        row.get(
                            "fishing_quest_only"
                        )
                    ),

                "fishing_disabled":
                    as_int(
                        row.get(
                            "fishing_disabled"
                        )
                    ),

                "craft_input":
                    as_int(
                        row.get(
                            "craft_input"
                        )
                    ),

                "craft_nq_output":
                    as_int(
                        row.get(
                            "craft_nq_output"
                        )
                    ),

                "craft_hq_output":
                    as_int(
                        row.get(
                            "craft_hq_output"
                        )
                    ),

                "mob_source_class":
                    clean_text(
                        row.get(
                            "mob_source_class"
                        )
                    ),

                "ordinary_source_count":
                    as_int(
                        row.get(
                            "ordinary_source_count"
                        )
                    ),

                "notorious_source_count":
                    as_int(
                        row.get(
                            "notorious_source_count"
                        )
                    ),

                "special_source_count":
                    as_int(
                        row.get(
                            "special_source_count"
                        )
                    ),

                "source_origins":
                    clean_text(
                        row.get(
                            "source_origins"
                        )
                    ),

                "zone_ids":
                    clean_text(
                        row.get(
                            "zone_ids"
                        )
                    ),

                "mob_names":
                    clean_text(
                        row.get(
                            "mob_names"
                        )
                    ),

                "evidence_reasons":
                    clean_text(
                        row.get(
                            "evidence_reasons"
                        )
                    ),

                "auto_live_promotion":
                    0,
            }
        )

    output = pd.DataFrame(
        rows
    ).sort_values(
        by=[
            "audit_bucket",
            "itemid",
        ],
        kind="stable",
    )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    if int(
        output[
            "auto_live_promotion"
        ].sum()
    ) != 0:
        raise PriorityOneAuditError(
            "Priority-1 audit attempted live promotion."
        )

    bucket_counts = (
        output[
            "audit_bucket"
        ]
        .value_counts()
        .to_dict()
    )

    recommendation_counts = (
        output[
            "recommended_action"
        ]
        .value_counts()
        .to_dict()
    )

    summary = {
        "status":
            "PASS",

        "total_items":
            int(
                len(
                    output
                )
            ),

        "protected_fishing_items":
            int(
                (
                    output[
                        "audit_bucket"
                    ]
                    == "PROTECTED_FISHING"
                ).sum()
            ),

        "source_correction_items":
            int(
                output[
                    "external_verification_required"
                ].sum()
            ),

        "audit_buckets":
            {
                str(k):
                    int(v)
                for k, v
                in bucket_counts.items()
            },

        "recommended_actions":
            {
                str(k):
                    int(v)
                for k, v
                in recommendation_counts.items()
            },

        "auto_live_promotions":
            0,
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
    print(
        "======================================"
    )
    print(
        " Priority-1 Source/Fishing Audit"
    )
    print(
        "======================================"
    )

    print(
        f"Total items:                  "
        f"{summary['total_items']:>6}"
    )

    print(
        f"Protected fishing:            "
        f"{summary['protected_fishing_items']:>6}"
    )

    print(
        f"Source corrections to verify: "
        f"{summary['source_correction_items']:>6}"
    )

    print()
    print(
        "Audit buckets:"
    )

    for bucket, count in sorted(
        summary[
            "audit_buckets"
        ].items()
    ):
        print(
            f"  {bucket:<30} "
            f"{count:>6}"
        )

    print()
    print(
        "Recommended actions:"
    )

    for action, count in sorted(
        summary[
            "recommended_actions"
        ].items()
    ):
        print(
            f"  {action:<30} "
            f"{count:>6}"
        )

    print()
    print(
        "Auto live promotions: 0"
    )

    print()
    print(
        f"Generated: {OUTPUT_FILE}"
    )

    print(
        f"Summary:   {SUMMARY_FILE}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
