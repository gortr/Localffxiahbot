from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

SELLER_RUNTIME = GENERATED / "market-sell.csv"

POLICY_CSV = REPORTS / "safe-stock-policy.csv"
POLICY_SUMMARY = REPORTS / "safe-stock-policy-summary.json"

CANDIDATE_CSV = (
    GENERATED
    / "market-sell-stock-normalization-cutover.csv"
)

AUDIT_CSV = (
    REPORTS
    / "stock-normalization-candidate-audit.csv"
)

SUMMARY_JSON = (
    REPORTS
    / "stock-normalization-candidate-summary.json"
)


class StockNormalizationCandidateError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise StockNormalizationCandidateError(
            f"Missing summary: {path}"
        )

    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


def as_int(value: Any) -> int:
    if value is None:
        return 0

    try:
        if pd.isna(value):
            return 0
    except TypeError:
        pass

    return int(value)


def main() -> int:
    if not SELLER_RUNTIME.exists():
        raise StockNormalizationCandidateError(
            f"Missing seller runtime: {SELLER_RUNTIME}"
        )

    if not POLICY_CSV.exists():
        raise StockNormalizationCandidateError(
            f"Missing stock policy CSV: {POLICY_CSV}"
        )

    policy_summary = load_json(
        POLICY_SUMMARY
    )

    if policy_summary.get("status") != "PASS":
        raise StockNormalizationCandidateError(
            "2B.3 policy is not PASS."
        )

    if policy_summary.get(
        "integrity_failures",
        0,
    ) != 0:
        raise StockNormalizationCandidateError(
            "2B.3 policy contains integrity failures."
        )

    for field in (
        "stock_change_authorized",
        "active_listing_change_authorized",
        "history_stock_influence_ready",
        "activation_ready",
        "auto_live_promotions",
    ):
        if policy_summary.get(field, 0) != 0:
            raise StockNormalizationCandidateError(
                f"Unsafe policy state: "
                f"{field}={policy_summary.get(field)}"
            )

    runtime_sha_before = sha256(
        SELLER_RUNTIME
    )

    runtime = pd.read_csv(
        SELLER_RUNTIME
    )

    policy = pd.read_csv(
        POLICY_CSV
    )

    expected_forms = int(
        policy_summary[
            "item_forms_evaluated"
        ]
    )

    if len(policy) != expected_forms:
        raise StockNormalizationCandidateError(
            "Policy row count disagrees "
            "with policy summary."
        )

    duplicate_policy = int(
        policy.duplicated(
            subset=[
                "itemid",
                "stack",
            ]
        ).sum()
    )

    if duplicate_policy:
        raise StockNormalizationCandidateError(
            f"Duplicate policy forms: "
            f"{duplicate_policy}"
        )

    recommended_changes = int(
        (
            policy[
                "recommended_target_delta"
            ]
            != 0
        ).sum()
    )

    if recommended_changes != int(
        policy_summary[
            "recommended_target_changes"
        ]
    ):
        raise StockNormalizationCandidateError(
            "Policy change count disagrees "
            "with policy summary."
        )

    # Phase 2B.4 is fail-closed.
    # We may construct a candidate only while no
    # stock changes have been authorized.
    if recommended_changes != 0:
        raise StockNormalizationCandidateError(
            "Policy currently recommends target "
            "changes, but deployment authorization "
            "is still zero. Candidate generation "
            "stopped fail-closed."
        )

    # With zero approved target changes, the correct
    # candidate is an exact byte-for-byte copy.
    shutil.copyfile(
        SELLER_RUNTIME,
        CANDIDATE_CSV,
    )

    candidate_sha = sha256(
        CANDIDATE_CSV
    )

    runtime_sha_after = sha256(
        SELLER_RUNTIME
    )

    candidate = pd.read_csv(
        CANDIDATE_CSV
    )

    if len(runtime) != len(candidate):
        raise StockNormalizationCandidateError(
            "Candidate row count differs "
            "from seller runtime."
        )

    if list(runtime.columns) != list(
        candidate.columns
    ):
        raise StockNormalizationCandidateError(
            "Candidate columns differ "
            "from seller runtime."
        )

    audit_rows = []

    changed_rows = 0
    changed_cells = 0

    for index in range(len(runtime)):
        before = runtime.iloc[index]
        after = candidate.iloc[index]

        row_changes = []

        for column in runtime.columns:
            before_value = before[column]
            after_value = after[column]

            equal = (
                (
                    pd.isna(before_value)
                    and pd.isna(after_value)
                )
                or before_value == after_value
            )

            if not equal:
                row_changes.append(column)

        if row_changes:
            changed_rows += 1
            changed_cells += len(
                row_changes
            )

            audit_rows.append({
                "row_index":
                    index,

                "itemid":
                    as_int(
                        before.get("itemid")
                    ),

                "name":
                    str(
                        before.get("name", "")
                    ),

                "changed_columns":
                    ";".join(
                        row_changes
                    ),
            })

    audit = pd.DataFrame(
        audit_rows,
        columns=[
            "row_index",
            "itemid",
            "name",
            "changed_columns",
        ],
    )

    audit.to_csv(
        AUDIT_CSV,
        index=False,
    )

    runtime_unchanged = int(
        runtime_sha_before
        == runtime_sha_after
    )

    candidate_exact_match = int(
        runtime_sha_before
        == candidate_sha
    )

    integrity_failures = 0

    if changed_rows != 0:
        integrity_failures += 1

    if changed_cells != 0:
        integrity_failures += 1

    if not runtime_unchanged:
        integrity_failures += 1

    if not candidate_exact_match:
        integrity_failures += 1

    summary = {
        "status":
            (
                "PASS"
                if integrity_failures == 0
                else "FAIL"
            ),

        "stage":
            "2B.4_STOCK_NORMALIZATION_CANDIDATE",

        "runtime_rows":
            int(len(runtime)),

        "candidate_rows":
            int(len(candidate)),

        "policy_item_forms":
            int(len(policy)),

        "recommended_target_changes":
            recommended_changes,

        "candidate_changed_rows":
            changed_rows,

        "candidate_changed_cells":
            changed_cells,

        "runtime_sha256_before":
            runtime_sha_before,

        "runtime_sha256_after":
            runtime_sha_after,

        "candidate_sha256":
            candidate_sha,

        "runtime_unchanged":
            runtime_unchanged,

        "candidate_exact_runtime_match":
            candidate_exact_match,

        "candidate_ready":
            int(
                integrity_failures == 0
            ),

        "deployment_change_authorized":
            0,

        "stock_change_authorized":
            0,

        "active_listing_change_authorized":
            0,

        "activation_ready":
            0,

        "auto_live_promotions":
            0,

        "integrity_failures":
            integrity_failures,

        "generated_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),
    }

    SUMMARY_JSON.write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 84)
    print(
        " Phase 2B.4 Candidate Stock "
        "Normalization"
    )
    print("=" * 84)

    print()
    print(
        f"Runtime seller rows:             "
        f"{len(runtime):>6}"
    )

    print(
        f"Candidate seller rows:           "
        f"{len(candidate):>6}"
    )

    print(
        f"Policy item forms:               "
        f"{len(policy):>6}"
    )

    print()
    print(
        f"Recommended target changes:      "
        f"{recommended_changes:>6}"
    )

    print(
        f"Candidate changed rows:          "
        f"{changed_rows:>6}"
    )

    print(
        f"Candidate changed cells:         "
        f"{changed_cells:>6}"
    )

    print()
    print(
        f"Runtime unchanged:               "
        f"{runtime_unchanged:>6}"
    )

    print(
        f"Candidate exact runtime match:   "
        f"{candidate_exact_match:>6}"
    )

    print()
    print(
        f"Integrity failures:              "
        f"{integrity_failures:>6}"
    )

    print()
    print(
        "Deployment change authorized:       0"
    )

    print(
        "Stock change authorized:            0"
    )

    print(
        "Active listing change authorized:   0"
    )

    print(
        "Activation ready:                   0"
    )

    print(
        "Auto live promotions:               0"
    )

    print()
    print(
        f"Candidate: {CANDIDATE_CSV}"
    )

    print(
        f"Audit:     {AUDIT_CSV}"
    )

    print(
        f"Summary:   {SUMMARY_JSON}"
    )

    print()

    if integrity_failures:
        print("FAIL")

        raise StockNormalizationCandidateError(
            "2B.4 candidate failed validation."
        )

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
