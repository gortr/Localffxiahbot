from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

SOURCE_SELLER = (
    GENERATED
    / "market-sell-pricing-cutover.csv"
)

TRANSITION_AUDIT = (
    REPORTS
    / "safe-seller-transition-audit.csv"
)

TRANSITION_SUMMARY = (
    REPORTS
    / "safe-seller-transition-summary.json"
)

OUTPUT_FILE = (
    GENERATED
    / "market-sell-seller-transition-cutover.csv"
)

AUDIT_FILE = (
    REPORTS
    / "safe-seller-cutover-audit.csv"
)

SUMMARY_FILE = (
    REPORTS
    / "safe-seller-cutover-summary.json"
)


EXPECTED_ROWS = 162

EXPECTED_TARGETS = {
    744: {
        "name": "silver_ingot",
        "single_before": 525,
        "single_after": 351,
        "stack_before": 6300,
        "stack_after": 4201,
    },
    1634: {
        "name": "rhodonite",
        "single_before": 900,
        "single_after": 601,
        "stack_before": 10800,
        "stack_after": 7201,
    },
    8740: {
        "name": "pizza_cutter",
        "single_before": 2394,
        "single_after": 249,
        "stack_before": 25856,
        "stack_after": 2989,
    },
}


class SafeSellerCutoverError(RuntimeError):
    pass


def as_int(value: Any) -> int:
    if value is None:
        return 0

    try:
        if pd.isna(value):
            return 0
    except TypeError:
        pass

    try:
        return int(float(value))
    except (
        TypeError,
        ValueError,
    ):
        return 0


def clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value).strip()

    if text.lower() in {
        "",
        "nan",
        "none",
        "null",
    }:
        return ""

    return text


def sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        while True:
            chunk = handle.read(
                1024 * 1024
            )

            if not chunk:
                break

            digest.update(chunk)

    return digest.hexdigest()


def require_columns(
    frame: pd.DataFrame,
    required: set[str],
    label: str,
) -> None:
    missing = sorted(
        required
        - set(frame.columns)
    )

    if missing:
        raise SafeSellerCutoverError(
            f"{label} missing columns: "
            f"{missing}"
        )


def values_equal(
    left: Any,
    right: Any,
) -> bool:
    if pd.isna(left) and pd.isna(right):
        return True

    if str(left) == str(right):
        return True

    try:
        return float(left) == float(right)
    except (
        TypeError,
        ValueError,
    ):
        return False


def main() -> int:
    required_files = [
        SOURCE_SELLER,
        TRANSITION_AUDIT,
        TRANSITION_SUMMARY,
    ]

    missing = [
        str(path)
        for path in required_files
        if not path.exists()
    ]

    if missing:
        raise SafeSellerCutoverError(
            "Missing required input files: "
            + ", ".join(missing)
        )

    # --------------------------------------------------------
    # Validate 1C.9c authority first.
    # --------------------------------------------------------

    summary = json.loads(
        TRANSITION_SUMMARY.read_text(
            encoding="utf-8"
        )
    )

    expected_summary = {
        "status": "PASS",
        "stage":
            "1C.9c_SAFE_SELLER_TRANSITION_AUDIT",
        "live_seller_rows": 162,
        "safe_transition_items": 3,
        "safe_transition_itemids":
            [744, 1634, 8740],
        "total_safety_failures": 0,
        "history_price_influence_ready": 0,
        "history_stock_influence_ready": 0,
        "deployment_change_authorized": 0,
        "activation_ready": 0,
        "auto_live_promotions": 0,
    }

    mismatches = []

    for key, expected in (
        expected_summary.items()
    ):
        actual = summary.get(key)

        if actual != expected:
            mismatches.append(
                f"{key}: "
                f"expected={expected!r}, "
                f"actual={actual!r}"
            )

    if mismatches:
        raise SafeSellerCutoverError(
            "1C.9c summary validation failed:\n"
            + "\n".join(mismatches)
        )

    # --------------------------------------------------------
    # Load source and audit.
    # --------------------------------------------------------

    source = pd.read_csv(
        SOURCE_SELLER,
        low_memory=False,
    )

    transition = pd.read_csv(
        TRANSITION_AUDIT,
        low_memory=False,
    )

    require_columns(
        source,
        {
            "itemid",
            "name",
            "price_single",
            "price_stacks",
        },
        "source seller",
    )

    require_columns(
        transition,
        {
            "itemid",
            "name",
            "current_single",
            "current_stack",
            "deployment_single",
            "deployment_stack",
            "action",
            "single_buyer_safe",
            "single_floor_safe",
            "stack_buyer_safe",
            "stack_90_safe",
            "stack_hard_floor_safe",
            "stack_bulk_target_safe",
            "non_authorized_unchanged",
            "downward_only",
        },
        "transition audit",
    )

    if len(source) != EXPECTED_ROWS:
        raise SafeSellerCutoverError(
            f"Expected {EXPECTED_ROWS} "
            "source seller rows; found "
            f"{len(source)}."
        )

    if source["itemid"].duplicated().any():
        raise SafeSellerCutoverError(
            "Source seller contains "
            "duplicate itemids."
        )

    if transition["itemid"].duplicated().any():
        raise SafeSellerCutoverError(
            "Transition audit contains "
            "duplicate itemids."
        )

    if len(transition) != EXPECTED_ROWS:
        raise SafeSellerCutoverError(
            f"Expected {EXPECTED_ROWS} "
            "transition audit rows; found "
            f"{len(transition)}."
        )

    source_ids = set(
        source["itemid"]
        .astype(int)
        .tolist()
    )

    transition_ids = set(
        transition["itemid"]
        .astype(int)
        .tolist()
    )

    if source_ids != transition_ids:
        raise SafeSellerCutoverError(
            "Seller source and transition "
            "audit item sets differ."
        )

    # --------------------------------------------------------
    # Verify audit baseline equals seller candidate baseline.
    # --------------------------------------------------------

    source_by_id = source.set_index(
        "itemid"
    )

    transition_by_id = transition.set_index(
        "itemid"
    )

    for itemid in sorted(source_ids):
        source_row = source_by_id.loc[
            itemid
        ]

        audit_row = transition_by_id.loc[
            itemid
        ]

        if (
            as_int(
                source_row[
                    "price_single"
                ]
            )
            != as_int(
                audit_row[
                    "current_single"
                ]
            )
        ):
            raise SafeSellerCutoverError(
                f"{itemid} source/audit "
                "single baseline mismatch."
            )

        if (
            as_int(
                source_row[
                    "price_stacks"
                ]
            )
            != as_int(
                audit_row[
                    "current_stack"
                ]
            )
        ):
            raise SafeSellerCutoverError(
                f"{itemid} source/audit "
                "stack baseline mismatch."
            )

    # --------------------------------------------------------
    # Extract authorized transitions.
    # --------------------------------------------------------

    safe = transition[
        transition[
            "action"
        ]
        == "SAFE_DOWNWARD_NORMALIZATION"
    ].copy()

    safe_ids = set(
        safe["itemid"]
        .astype(int)
        .tolist()
    )

    expected_ids = set(
        EXPECTED_TARGETS
    )

    if safe_ids != expected_ids:
        raise SafeSellerCutoverError(
            "Authorized seller set changed. "
            f"Expected {sorted(expected_ids)}; "
            f"found {sorted(safe_ids)}."
        )

    safety_columns = [
        "single_buyer_safe",
        "single_floor_safe",
        "stack_buyer_safe",
        "stack_90_safe",
        "stack_hard_floor_safe",
        "stack_bulk_target_safe",
        "non_authorized_unchanged",
        "downward_only",
    ]

    for column in safety_columns:
        failures = int(
            (
                safe[column]
                .astype(int)
                != 1
            ).sum()
        )

        if failures:
            raise SafeSellerCutoverError(
                f"Authorized transitions have "
                f"{failures} failures in "
                f"{column}."
            )

    # --------------------------------------------------------
    # Verify frozen expected targets.
    # --------------------------------------------------------

    for itemid, expected in (
        EXPECTED_TARGETS.items()
    ):
        source_row = source_by_id.loc[
            itemid
        ]

        audit_row = transition_by_id.loc[
            itemid
        ]

        actual_name = clean_text(
            source_row["name"]
        )

        checks = {
            "name":
                actual_name,
            "single_before":
                as_int(
                    source_row[
                        "price_single"
                    ]
                ),
            "single_after":
                as_int(
                    audit_row[
                        "deployment_single"
                    ]
                ),
            "stack_before":
                as_int(
                    source_row[
                        "price_stacks"
                    ]
                ),
            "stack_after":
                as_int(
                    audit_row[
                        "deployment_stack"
                    ]
                ),
        }

        if checks != expected:
            raise SafeSellerCutoverError(
                f"{itemid} frozen target "
                "validation failed: "
                f"expected={expected}, "
                f"actual={checks}"
            )

    # --------------------------------------------------------
    # Build candidate by copying source and touching ONLY
    # the two price fields for authorized rows.
    # --------------------------------------------------------

    candidate = source.copy()

    candidate_index = candidate.set_index(
        "itemid"
    )

    for itemid in sorted(expected_ids):
        audit_row = transition_by_id.loc[
            itemid
        ]

        candidate_index.loc[
            itemid,
            "price_single",
        ] = as_int(
            audit_row[
                "deployment_single"
            ]
        )

        candidate_index.loc[
            itemid,
            "price_stacks",
        ] = as_int(
            audit_row[
                "deployment_stack"
            ]
        )

    candidate = (
        candidate_index
        .reset_index()
    )

    # Preserve original column order.
    candidate = candidate[
        source.columns
    ]

    # --------------------------------------------------------
    # Structural validation.
    # --------------------------------------------------------

    if len(candidate) != len(source):
        raise SafeSellerCutoverError(
            "Candidate seller row count changed."
        )

    candidate_ids = set(
        candidate["itemid"]
        .astype(int)
        .tolist()
    )

    if candidate_ids != source_ids:
        raise SafeSellerCutoverError(
            "Candidate seller item set changed."
        )

    if candidate["itemid"].duplicated().any():
        raise SafeSellerCutoverError(
            "Candidate seller contains "
            "duplicate itemids."
        )

    # --------------------------------------------------------
    # Cell-level diff audit.
    # --------------------------------------------------------

    source_i = source.set_index(
        "itemid"
    )

    candidate_i = candidate.set_index(
        "itemid"
    )

    audit_rows = []
    changed_ids = set()
    changed_cells = 0
    unauthorized_cells = []

    for itemid in sorted(source_ids):
        before = source_i.loc[
            itemid
        ]

        after = candidate_i.loc[
            itemid
        ]

        row_changes = []

        for column in source.columns:
            if column == "itemid":
                continue

            left = before[column]
            right = after[column]

            if values_equal(
                left,
                right,
            ):
                continue

            changed_cells += 1
            changed_ids.add(
                int(itemid)
            )

            row_changes.append(
                column
            )

            if (
                int(itemid)
                not in expected_ids
                or column
                not in {
                    "price_single",
                    "price_stacks",
                }
            ):
                unauthorized_cells.append({
                    "itemid":
                        int(itemid),
                    "column":
                        column,
                    "before":
                        left,
                    "after":
                        right,
                })

        audit_rows.append({
            "itemid":
                int(itemid),

            "name":
                clean_text(
                    after["name"]
                ),

            "changed":
                int(
                    bool(row_changes)
                ),

            "changed_columns":
                ",".join(
                    row_changes
                ),

            "before_single":
                as_int(
                    before[
                        "price_single"
                    ]
                ),

            "after_single":
                as_int(
                    after[
                        "price_single"
                    ]
                ),

            "before_stack":
                as_int(
                    before[
                        "price_stacks"
                    ]
                ),

            "after_stack":
                as_int(
                    after[
                        "price_stacks"
                    ]
                ),

            "authorized":
                int(
                    int(itemid)
                    in expected_ids
                ),

            "downward_single":
                int(
                    as_int(
                        after[
                            "price_single"
                        ]
                    )
                    <= as_int(
                        before[
                            "price_single"
                        ]
                    )
                ),

            "downward_stack":
                int(
                    as_int(
                        after[
                            "price_stacks"
                        ]
                    )
                    <= as_int(
                        before[
                            "price_stacks"
                        ]
                    )
                ),

            "activation_ready":
                0,

            "auto_live_promotion":
                0,
        })

    if unauthorized_cells:
        raise SafeSellerCutoverError(
            "Unauthorized candidate cell "
            "changes detected: "
            f"{unauthorized_cells}"
        )

    if changed_ids != expected_ids:
        raise SafeSellerCutoverError(
            "Changed seller set mismatch. "
            f"Expected {sorted(expected_ids)}; "
            f"found {sorted(changed_ids)}."
        )

    if changed_cells != 6:
        raise SafeSellerCutoverError(
            "Expected exactly 6 changed cells "
            "(3 sellers × 2 price fields); "
            f"found {changed_cells}."
        )

    # --------------------------------------------------------
    # Final exact target validation.
    # --------------------------------------------------------

    candidate_by_id = candidate.set_index(
        "itemid"
    )

    for itemid, expected in (
        EXPECTED_TARGETS.items()
    ):
        row = candidate_by_id.loc[
            itemid
        ]

        if (
            as_int(
                row["price_single"]
            )
            != expected[
                "single_after"
            ]
        ):
            raise SafeSellerCutoverError(
                f"{itemid} candidate single "
                "price mismatch."
            )

        if (
            as_int(
                row["price_stacks"]
            )
            != expected[
                "stack_after"
            ]
        ):
            raise SafeSellerCutoverError(
                f"{itemid} candidate stack "
                "price mismatch."
            )

    # --------------------------------------------------------
    # Write candidate + audit.
    # --------------------------------------------------------

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    REPORTS.mkdir(
        parents=True,
        exist_ok=True,
    )

    candidate.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    audit = pd.DataFrame(
        audit_rows
    )

    audit.to_csv(
        AUDIT_FILE,
        index=False,
    )

    source_hash = sha256(
        SOURCE_SELLER
    )

    candidate_hash = sha256(
        OUTPUT_FILE
    )

    summary_out = {
        "status":
            "PASS",

        "stage":
            "1C.9d_SAFE_SELLER_CUTOVER",

        "source_seller_rows":
            int(len(source)),

        "cutover_seller_rows":
            int(len(candidate)),

        "seller_additions":
            0,

        "seller_removals":
            0,

        "seller_changed_rows":
            int(len(changed_ids)),

        "seller_changed_cells":
            int(changed_cells),

        "seller_changed_itemids":
            sorted(
                changed_ids
            ),

        "source_sha256":
            source_hash,

        "candidate_sha256":
            candidate_hash,

        "candidate_differs_from_source":
            int(
                source_hash
                != candidate_hash
            ),

        "history_price_influence_ready":
            0,

        "history_stock_influence_ready":
            0,

        "deployment_change_authorized":
            0,

        "activation_ready":
            0,

        "auto_live_promotions":
            0,

        "candidate_ready":
            1,
    }

    SUMMARY_FILE.write_text(
        json.dumps(
            summary_out,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    # --------------------------------------------------------
    # Console report.
    # --------------------------------------------------------

    print()
    print("=" * 94)
    print(
        " Phase 1C.9d Safe Seller Cutover"
    )
    print("=" * 94)

    print()
    print(
        f"Source seller rows:                  "
        f"{len(source):>6}"
    )

    print(
        f"Cutover seller rows:                 "
        f"{len(candidate):>6}"
    )

    print(
        f"Seller additions:                    "
        f"{0:>6}"
    )

    print(
        f"Seller removals:                     "
        f"{0:>6}"
    )

    print(
        f"Changed seller rows:                 "
        f"{len(changed_ids):>6}"
    )

    print(
        f"Changed cells:                       "
        f"{changed_cells:>6}"
    )

    print()
    print("Seller transitions:")
    print()

    changed_audit = audit[
        audit["changed"] == 1
    ]

    print(
        changed_audit[
            [
                "itemid",
                "name",
                "before_single",
                "after_single",
                "before_stack",
                "after_stack",
                "changed_columns",
            ]
        ]
        .sort_values(
            "itemid"
        )
        .to_string(
            index=False
        )
    )

    print()
    print(
        f"Source SHA256:    {source_hash}"
    )

    print(
        f"Candidate SHA256: {candidate_hash}"
    )

    print()
    print(
        "Candidate ready:                       1"
    )
    print(
        "Deployment changes authorized:          0"
    )
    print(
        "Activation ready:                       0"
    )
    print(
        "Auto live promotions:                   0"
    )

    print()
    print(
        f"Candidate: {OUTPUT_FILE}"
    )

    print(
        f"Audit:     {AUDIT_FILE}"
    )

    print(
        f"Summary:   {SUMMARY_FILE}"
    )

    print()
    print("PASS")

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
