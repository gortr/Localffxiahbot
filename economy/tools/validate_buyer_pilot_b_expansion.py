from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

SELLER_RUNTIME = GENERATED / "market-sell.csv"
BUYER_RUNTIME = GENERATED / "market-buy.csv"

CANDIDATE = (
    GENERATED
    / "market-buy-pilot-b-candidate.csv"
)

CANDIDATE_SUMMARY = (
    REPORTS
    / "buyer-pilot-b-candidate-summary.json"
)

SAFE_POLICY = (
    GENERATED
    / "buyer-pilot-b-safe-ceiling-policy.csv"
)

SAFE_POLICY_SUMMARY = (
    REPORTS
    / "buyer-pilot-b-safe-ceiling-policy-summary.json"
)

VALIDATION_CSV = (
    REPORTS
    / "buyer-pilot-b-expansion-validation.csv"
)

SUMMARY_JSON = (
    REPORTS
    / "buyer-pilot-b-expansion-validation-summary.json"
)

MANAGER_FILE = (
    ROOT
    / "ffxiahbot"
    / "auction"
    / "manager.py"
)


EXPECTED_COLUMNS = [
    "itemid",
    "name",
    "sell_single",
    "buy_single",
    "price_single",
    "stock_single",
    "buy_rate_single",
    "sell_rate_single",
    "sell_stacks",
    "buy_stacks",
    "price_stacks",
    "stock_stacks",
    "buy_rate_stacks",
    "sell_rate_stacks",
]

PILOT_IDS = {
    5644,
    5653,
    5655,
}

DEFERRED_IDS = {
    5327,
    9196,
}

EXPECTED_LIVE_ROWS = 152
EXPECTED_CANDIDATE_ROWS = 155
EXPECTED_SELLER_ROWS = 173
EXPECTED_RATE = 0.015


class ValidationError(RuntimeError):
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
        raise ValidationError(
            f"Missing JSON input: {path}"
        )

    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


def as_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0

    return int(float(value))


def as_float(value: Any) -> float:
    if value is None or pd.isna(value):
        return 0.0

    return float(value)


def main() -> int:
    required = [
        SELLER_RUNTIME,
        BUYER_RUNTIME,
        CANDIDATE,
        CANDIDATE_SUMMARY,
        SAFE_POLICY,
        SAFE_POLICY_SUMMARY,
        MANAGER_FILE,
    ]

    for path in required:
        if not path.exists():
            raise ValidationError(
                f"Missing required input: {path}"
            )

    candidate_summary = load_json(
        CANDIDATE_SUMMARY
    )

    policy_summary = load_json(
        SAFE_POLICY_SUMMARY
    )

    if (
        candidate_summary.get("status")
        != "PASS"
    ):
        raise ValidationError(
            "3C.3 candidate is not PASS."
        )

    if (
        policy_summary.get("status")
        != "PASS"
    ):
        raise ValidationError(
            "3C.2 safe ceiling policy is not PASS."
        )

    buyer_hash = sha256(
        BUYER_RUNTIME
    )

    candidate_hash = sha256(
        CANDIDATE
    )

    seller_hash = sha256(
        SELLER_RUNTIME
    )

    if (
        buyer_hash
        != candidate_summary.get(
            "live_sha256"
        )
    ):
        raise ValidationError(
            "Live buyer runtime changed "
            "since 3C.3."
        )

    if (
        candidate_hash
        != candidate_summary.get(
            "candidate_sha256"
        )
    ):
        raise ValidationError(
            "Candidate hash changed "
            "since 3C.3."
        )

    buyer = pd.read_csv(
        BUYER_RUNTIME,
        low_memory=False,
    )

    seller = pd.read_csv(
        SELLER_RUNTIME,
        low_memory=False,
    )

    candidate = pd.read_csv(
        CANDIDATE,
        low_memory=False,
    )

    policy = pd.read_csv(
        SAFE_POLICY,
        low_memory=False,
    )

    if list(buyer.columns) != EXPECTED_COLUMNS:
        raise ValidationError(
            "Unexpected buyer runtime schema."
        )

    if list(candidate.columns) != EXPECTED_COLUMNS:
        raise ValidationError(
            "Unexpected candidate schema."
        )

    if len(buyer) != EXPECTED_LIVE_ROWS:
        raise ValidationError(
            f"Expected {EXPECTED_LIVE_ROWS} "
            f"live buyer rows; got {len(buyer)}."
        )

    if len(candidate) != EXPECTED_CANDIDATE_ROWS:
        raise ValidationError(
            f"Expected {EXPECTED_CANDIDATE_ROWS} "
            f"candidate rows; got {len(candidate)}."
        )

    if len(seller) != EXPECTED_SELLER_ROWS:
        raise ValidationError(
            f"Expected {EXPECTED_SELLER_ROWS} "
            f"seller rows; got {len(seller)}."
        )

    for label, frame in [
        ("buyer", buyer),
        ("candidate", candidate),
    ]:
        if frame.duplicated(
            subset=["itemid"]
        ).any():
            raise ValidationError(
                f"{label} contains duplicate itemids."
            )

    live_ids = set(
        buyer["itemid"].astype(int)
    )

    candidate_ids = set(
        candidate["itemid"].astype(int)
    )

    added_ids = (
        candidate_ids
        - live_ids
    )

    removed_ids = (
        live_ids
        - candidate_ids
    )

    if added_ids != PILOT_IDS:
        raise ValidationError(
            "Unexpected Pilot B additions: "
            f"{sorted(added_ids)}"
        )

    if removed_ids:
        raise ValidationError(
            "Buyer candidate removed live rows: "
            f"{sorted(removed_ids)}"
        )

    if candidate_ids & DEFERRED_IDS:
        raise ValidationError(
            "Deferred candidates entered "
            "the Pilot B runtime candidate."
        )

    old = (
        buyer
        .sort_values("itemid")
        .set_index("itemid")
    )

    existing = (
        candidate[
            candidate["itemid"]
            .astype(int)
            .isin(live_ids)
        ]
        .sort_values("itemid")
        .set_index("itemid")
    )

    if not old.equals(existing):
        raise ValidationError(
            "Existing 152 buyer rows changed."
        )

    additions = candidate[
        candidate["itemid"]
        .astype(int)
        .isin(PILOT_IDS)
    ].copy()

    policy_pilot = policy[
        policy["itemid"]
        .astype(int)
        .isin(PILOT_IDS)
    ].copy()

    policy_by_id = (
        policy_pilot
        .set_index("itemid")
    )

    validation_rows = []

    failures = []

    for _, row in additions.iterrows():
        iid = as_int(
            row["itemid"]
        )

        if iid not in policy_by_id.index:
            failures.append(
                f"{iid}: missing safe policy"
            )
            continue

        p = policy_by_id.loc[iid]

        checks = {
            "buyer_only_single":
                as_int(
                    row["sell_single"]
                ) == 0
                and as_int(
                    row["buy_single"]
                ) == 1,

            "buyer_only_stack":
                as_int(
                    row["sell_stacks"]
                ) == 0
                and as_int(
                    row["buy_stacks"]
                ) == 1,

            "zero_stock":
                as_int(
                    row["stock_single"]
                ) == 0
                and as_int(
                    row["stock_stacks"]
                ) == 0,

            "single_rate":
                abs(
                    as_float(
                        row["buy_rate_single"]
                    )
                    - EXPECTED_RATE
                )
                < 1e-12,

            "stack_rate":
                abs(
                    as_float(
                        row["buy_rate_stacks"]
                    )
                    - EXPECTED_RATE
                )
                < 1e-12,

            "single_price_matches_policy":
                as_int(
                    row["price_single"]
                )
                == as_int(
                    p["safe_single_bid"]
                ),

            "stack_price_matches_policy":
                as_int(
                    row["price_stacks"]
                )
                == as_int(
                    p["safe_stack_bid"]
                ),

            "policy_price_ready":
                as_int(
                    p["pilot_price_ready"]
                ) == 1,

            "no_floor_conflict":
                as_int(
                    p[
                        "economic_floor_conflict"
                    ]
                ) == 0,

            "fully_repeatable":
                as_int(
                    p[
                        "all_inputs_repeatable"
                    ]
                ) == 1,

            "repeatable_safe_disposition":
                str(
                    p["disposition"]
                )
                ==
                "PILOT_B_PRICE_READY_REPEATABLE_SAFE",
        }

        row_pass = all(
            checks.values()
        )

        if not row_pass:
            failed_checks = [
                name
                for name, passed
                in checks.items()
                if not passed
            ]

            failures.append(
                f"{iid}: "
                + ",".join(
                    failed_checks
                )
            )

        validation_rows.append({
            "itemid":
                iid,

            "name":
                row["name"],

            **{
                key:
                    int(value)
                for key, value
                in checks.items()
            },

            "validation_pass":
                int(row_pass),
        })

    # Hardened buyer manager must be present
    # before the Pilot B service may be restarted.
    manager_text = MANAGER_FILE.read_text(
        encoding="utf-8",
    )

    rate_gate_present = int(
        "rate_allowed_this_cycle"
        in manager_text
        and
        "purchase_key"
        in manager_text
    )

    if not rate_gate_present:
        failures.append(
            "Buyer rate-gate hardening "
            "not detected in manager.py."
        )

    validation = pd.DataFrame(
        validation_rows
    ).sort_values("itemid")

    validation.to_csv(
        VALIDATION_CSV,
        index=False,
    )

    summary = {
        "status":
            "PASS"
            if not failures
            else "FAIL",

        "live_buyer_rows":
            len(buyer),

        "candidate_buyer_rows":
            len(candidate),

        "seller_runtime_rows":
            len(seller),

        "pilot_additions":
            len(additions),

        "pilot_itemids":
            sorted(PILOT_IDS),

        "removed_items":
            len(removed_ids),

        "existing_rows_identical":
            1,

        "rate_gate_hardening_present":
            rate_gate_present,

        "buyer_runtime_sha256":
            buyer_hash,

        "seller_runtime_sha256":
            seller_hash,

        "candidate_sha256":
            candidate_hash,

        "expected_candidate_sha256":
            candidate_summary.get(
                "candidate_sha256"
            ),

        "runtime_mutation_authorized":
            0,

        "database_mutation_authorized":
            0,

        "activation_ready":
            0,

        "auto_live_promotion":
            0,

        "integrity_failures":
            len(failures),

        "failures":
            failures,
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

    print("=" * 104)
    print(
        " Phase 3C.4 Buyer Pilot B "
        "End-to-End Candidate Validation"
    )
    print("=" * 104)
    print()

    print(
        validation.to_string(
            index=False
        )
    )

    print()
    print(
        "Live buyer rows:             ",
        len(buyer),
    )

    print(
        "Candidate buyer rows:        ",
        len(candidate),
    )

    print(
        "Pilot additions:             ",
        len(additions),
    )

    print(
        "Existing rows unchanged:     ",
        1,
    )

    print(
        "Removed rows:                ",
        len(removed_ids),
    )

    print(
        "Buyer rate hardening present:",
        rate_gate_present,
    )

    print(
        "Integrity failures:          ",
        len(failures),
    )

    print()
    print(
        "Runtime mutation authorized:  0"
    )

    print(
        "Database mutation authorized: 0"
    )

    print(
        "Activation ready:             0"
    )

    print(
        "Auto live promotion:          0"
    )

    print()
    print(
        "Buyer runtime SHA256:"
    )
    print(buyer_hash)

    print()
    print(
        "Candidate SHA256:"
    )
    print(candidate_hash)

    print()
    print(
        "Validation:",
        VALIDATION_CSV,
    )

    print(
        "Summary:   ",
        SUMMARY_JSON,
    )

    print()
    print(summary["status"])

    if failures:
        raise SystemExit(1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
