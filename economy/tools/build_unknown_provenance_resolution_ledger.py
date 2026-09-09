from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

LEDGER_V1 = ROOT / "economy" / "reports" / "unknown-provenance-resolution-ledger.csv"
REPO_AUDIT = ROOT / "economy" / "reports" / "unknown-repository-source-audit.csv"

OUTPUT_FILE = ROOT / "economy" / "reports" / "unknown-provenance-resolution-ledger-v2.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "unknown-provenance-resolution-ledger-v2-summary.json"


ORDINARY_REPOSITORY_FAMILIES = {
    "SHOP",
    "HELM",
    "CHOCOBO_DIGGING",
    "MOG_GARDEN",
}

PROTECTED_REPOSITORY_FAMILIES = {
    "QUEST",
    "MISSION",
    "BATTLEFIELD",
    "RECORDS_OF_EMINENCE",
    "ABYSSEA",
    "ASSAULT_NYZUL",
    "DYNAMIS",
    "CAMPAIGN",
    "BESIEGED",
    "SALVAGE",
    "LEGION",
    "DELVE",
}

REVIEW_REPOSITORY_FAMILIES = {
    "TREASURE",
    "OTHER_ACQUISITION_CONTEXT",
}


class LedgerV2Error(RuntimeError):
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


def split_pipe(value: Any) -> set[str]:
    text = clean_text(value)
    if not text:
        return set()
    return {part for part in text.split("|") if part}


def main() -> int:
    for path in [LEDGER_V1, REPO_AUDIT]:
        if not path.exists():
            raise LedgerV2Error(f"Missing required input: {path}")

    ledger = pd.read_csv(LEDGER_V1, low_memory=False)
    repo = pd.read_csv(REPO_AUDIT, low_memory=False)

    if len(ledger) != 5011:
        raise LedgerV2Error(
            f"Expected 5011 ledger rows, found {len(ledger)}"
        )

    repo_by_item = {
        as_int(row.get("itemid")): row.to_dict()
        for _, row in repo.iterrows()
    }

    rows: list[dict[str, Any]] = []

    for _, row in ledger.iterrows():
        out = row.to_dict()

        itemid = as_int(row.get("itemid"))
        prior_status = clean_text(row.get("source_status"))
        prior_resolution = clean_text(row.get("resolution"))
        repo_row = repo_by_item.get(itemid, {})

        repo_families = split_pipe(
            repo_row.get("repository_source_families")
        )
        repo_confidence = clean_text(
            repo_row.get("best_confidence")
        )
        repo_reference_count = as_int(
            repo_row.get("repository_reference_count")
        )

        accepted_repo = (
            repo_reference_count > 0
            and repo_confidence in {"HIGH", "MEDIUM"}
        )

        ordinary = sorted(
            repo_families & ORDINARY_REPOSITORY_FAMILIES
        )
        protected = sorted(
            repo_families & PROTECTED_REPOSITORY_FAMILIES
        )
        review = sorted(
            repo_families & REVIEW_REPOSITORY_FAMILIES
        )

        new_status = prior_status
        new_resolution = prior_resolution
        class_hint = clean_text(
            row.get("recommended_class_hint")
        )
        repository_policy_reason = ""

        if prior_status.startswith("UNRESOLVED"):
            if accepted_repo:
                if ordinary and protected:
                    new_status = "SOURCE_FOUND_REVIEW"
                    new_resolution = "MIXED_REPOSITORY_SOURCE_FOUND"
                    class_hint = "SOURCE_CONTEXT_REVIEW"
                    repository_policy_reason = (
                        "ORDINARY_AND_PROTECTED_SOURCE_FAMILIES"
                    )
                elif ordinary:
                    new_status = "RESOLVED"
                    new_resolution = "ORDINARY_REPOSITORY_SOURCE_FOUND"
                    class_hint = "NORMAL_OR_STAPLE_REVIEW"
                    repository_policy_reason = (
                        "ORDINARY_ACQUISITION_FAMILY"
                    )
                elif protected:
                    new_status = "RESOLVED_PROTECTED"
                    new_resolution = "PROTECTED_REPOSITORY_SOURCE_FOUND"
                    class_hint = "PROTECTED_OR_DEMAND_ONLY"
                    repository_policy_reason = (
                        "SPECIAL_OR_REWARD_ACQUISITION_FAMILY"
                    )
                elif review:
                    new_status = "SOURCE_FOUND_REVIEW"
                    new_resolution = "REPOSITORY_SOURCE_FOUND_REVIEW"
                    class_hint = "SOURCE_CONTEXT_REVIEW"
                    repository_policy_reason = (
                        "TREASURE_OR_AMBIGUOUS_ACQUISITION_CONTEXT"
                    )
                else:
                    new_status = "SOURCE_FOUND_REVIEW"
                    new_resolution = "REPOSITORY_SOURCE_FOUND_REVIEW"
                    class_hint = "SOURCE_CONTEXT_REVIEW"
                    repository_policy_reason = (
                        "UNMAPPED_ACQUISITION_FAMILY"
                    )
            elif repo_reference_count > 0:
                new_status = "UNRESOLVED"
                new_resolution = "REPOSITORY_REFERENCE_ONLY"
                class_hint = "KEEP_PROTECTED"
                repository_policy_reason = (
                    "LOW_CONFIDENCE_REPOSITORY_REFERENCE"
                )

        out.update(
            {
                "prior_resolution": prior_resolution,
                "prior_source_status": prior_status,
                "repository_source_families":
                    "|".join(sorted(repo_families)),
                "repository_confidence": repo_confidence,
                "repository_reference_count": repo_reference_count,
                "repository_ordinary_families": "|".join(ordinary),
                "repository_protected_families": "|".join(protected),
                "repository_review_families": "|".join(review),
                "repository_policy_reason": repository_policy_reason,
                "resolution": new_resolution,
                "source_status": new_status,
                "recommended_class_hint": class_hint,
                "activation_ready": 0,
                "auto_live_promotion": 0,
            }
        )

        rows.append(out)

    output = pd.DataFrame(rows).sort_values(
        by=[
            "source_status",
            "resolution",
            "item_type",
            "itemid",
        ],
        kind="stable",
    )

    if len(output) != 5011:
        raise LedgerV2Error(
            f"Expected 5011 output rows, found {len(output)}"
        )

    if int(output["activation_ready"].sum()) != 0:
        raise LedgerV2Error("Ledger v2 activated items.")

    if int(output["auto_live_promotion"].sum()) != 0:
        raise LedgerV2Error(
            "Ledger v2 attempted live promotion."
        )

    output.to_csv(OUTPUT_FILE, index=False)

    resolved_or_source_found = int(
        output["source_status"]
        .isin(
            {
                "RESOLVED",
                "RESOLVED_PROTECTED",
                "SOURCE_FOUND_REVIEW",
            }
        )
        .sum()
    )

    still_unresolved = int(
        output["source_status"]
        .str.startswith("UNRESOLVED")
        .sum()
    )

    summary = {
        "status": "PASS",
        "total_items": 5011,
        "resolved_or_source_found": resolved_or_source_found,
        "still_unresolved": still_unresolved,
        "newly_resolved_by_repository": int(
            (
                output["prior_source_status"]
                .str.startswith("UNRESOLVED")
                & output["source_status"]
                .isin(
                    {
                        "RESOLVED",
                        "RESOLVED_PROTECTED",
                        "SOURCE_FOUND_REVIEW",
                    }
                )
            ).sum()
        ),
        "low_confidence_repository_only": int(
            (
                output["resolution"]
                == "REPOSITORY_REFERENCE_ONLY"
            ).sum()
        ),
        "resolution_counts": {
            str(k): int(v)
            for k, v in (
                output["resolution"]
                .value_counts()
                .to_dict()
                .items()
            )
        },
        "source_status_counts": {
            str(k): int(v)
            for k, v in (
                output["source_status"]
                .value_counts()
                .to_dict()
                .items()
            )
        },
        "activation_ready": 0,
        "auto_live_promotions": 0,
    }

    SUMMARY_FILE.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 48)
    print(" Unknown Provenance Resolution Ledger v2")
    print("=" * 48)
    print(
        f"Total items:                    "
        f"{summary['total_items']:>6}"
    )
    print(
        f"Resolved/source found:          "
        f"{summary['resolved_or_source_found']:>6}"
    )
    print(
        f"New from repository scan:       "
        f"{summary['newly_resolved_by_repository']:>6}"
    )
    print(
        f"Still unresolved:               "
        f"{summary['still_unresolved']:>6}"
    )
    print(
        f"Low-confidence repo only:       "
        f"{summary['low_confidence_repository_only']:>6}"
    )
    print()
    print("Resolution:")
    for label, count in sorted(
        summary["resolution_counts"].items(),
        key=lambda pair: (-pair[1], pair[0]),
    ):
        print(
            f"  {label:<38} "
            f"{count:>6}"
        )
    print()
    print("Activation ready: 0")
    print("Auto live promotions: 0")
    print()
    print(f"Ledger:  {OUTPUT_FILE}")
    print(f"Summary: {SUMMARY_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
