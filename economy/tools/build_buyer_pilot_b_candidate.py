from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

LIVE_FILE = (
    ROOT
    / "economy/generated/market-buy.csv"
)

POLICY_FILE = (
    ROOT
    / "economy/generated/"
      "buyer-pilot-b-safe-ceiling-policy.csv"
)

CANDIDATE_FILE = (
    ROOT
    / "economy/generated/"
      "market-buy-pilot-b-candidate.csv"
)

AUDIT_FILE = (
    ROOT
    / "economy/reports/"
      "buyer-pilot-b-candidate-audit.csv"
)

SUMMARY_FILE = (
    ROOT
    / "economy/reports/"
      "buyer-pilot-b-candidate-summary.json"
)


# Deliberately tiny first buyer pilot.
PILOT_IDS = {
    5653,  # Cherry Muffin
    5655,  # Coffeecake Muffin
    5644,  # Jack-o'-Pie
}

EXPECTED_LIVE_ROWS = 152
EXPECTED_CANDIDATE_ROWS = 155

BUY_RATE = 0.015
TICKS_PER_DAY = 86400 / 300


class PilotError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as fh:
        for chunk in iter(
            lambda: fh.read(1024 * 1024),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


def as_int(value) -> int:
    if value is None or pd.isna(value):
        return 0

    return int(float(value))


def main() -> None:
    live = pd.read_csv(
        LIVE_FILE,
        low_memory=False,
    )

    policy = pd.read_csv(
        POLICY_FILE,
        low_memory=False,
    )

    if len(live) != EXPECTED_LIVE_ROWS:
        raise PilotError(
            "Expected "
            f"{EXPECTED_LIVE_ROWS} live buyer rows; "
            f"found {len(live)}."
        )

    if live["itemid"].nunique() != len(live):
        raise PilotError(
            "Live buyer runtime contains "
            "duplicate itemids."
        )

    already_live = set(
        live["itemid"].astype(int)
    ) & PILOT_IDS

    if already_live:
        raise PilotError(
            "Pilot IDs unexpectedly already live: "
            f"{sorted(already_live)}"
        )

    selected = policy[
        policy["itemid"]
        .astype(int)
        .isin(PILOT_IDS)
    ].copy()

    if len(selected) != 3:
        raise PilotError(
            "Expected exactly 3 Pilot B rows; "
            f"found {len(selected)}."
        )

    if set(
        selected["itemid"].astype(int)
    ) != PILOT_IDS:
        raise PilotError(
            "Pilot selection does not match "
            "expected IDs."
        )

    failures = []

    for _, row in selected.iterrows():
        iid = as_int(row["itemid"])

        if as_int(
            row["pilot_price_ready"]
        ) != 1:
            failures.append(
                f"{iid}: not price ready"
            )

        if (
            row["disposition"]
            !=
            "PILOT_B_PRICE_READY_REPEATABLE_SAFE"
        ):
            failures.append(
                f"{iid}: unexpected disposition "
                f"{row['disposition']}"
            )

        if as_int(
            row["all_inputs_repeatable"]
        ) != 1:
            failures.append(
                f"{iid}: expected fully "
                "repeatable recipe"
            )

        if as_int(
            row["economic_floor_conflict"]
        ) != 0:
            failures.append(
                f"{iid}: floor conflict"
            )

        if as_int(
            row["bid_above_economic_cost"]
        ) != 0:
            failures.append(
                f"{iid}: bid exceeds "
                "economic cost"
            )

    if failures:
        raise PilotError(
            "; ".join(failures)
        )

    rows = []

    for _, p in selected.iterrows():
        rows.append({
            "itemid":
                as_int(p["itemid"]),

            "name":
                p["name"],

            # Buyer-only row.
            "sell_single":
                0,

            "buy_single":
                1,

            "price_single":
                as_int(
                    p["safe_single_bid"]
                ),

            "stock_single":
                0,

            "buy_rate_single":
                BUY_RATE,

            "sell_rate_single":
                0.0,

            "sell_stacks":
                0,

            "buy_stacks":
                1,

            "price_stacks":
                as_int(
                    p["safe_stack_bid"]
                ),

            "stock_stacks":
                0,

            "buy_rate_stacks":
                BUY_RATE,

            "sell_rate_stacks":
                0.0,
        })

    additions = pd.DataFrame(
        rows,
        columns=live.columns,
    )

    candidate = pd.concat(
        [
            live.copy(),
            additions,
        ],
        ignore_index=True,
    )

    candidate = candidate.sort_values(
        "itemid",
        kind="stable",
    ).reset_index(drop=True)

    if len(candidate) != EXPECTED_CANDIDATE_ROWS:
        raise PilotError(
            "Expected "
            f"{EXPECTED_CANDIDATE_ROWS} candidate rows; "
            f"found {len(candidate)}."
        )

    if candidate["itemid"].nunique() != len(
        candidate
    ):
        raise PilotError(
            "Candidate contains duplicate itemids."
        )

    # -----------------------------------------------
    # Existing rows must be byte-for-value identical
    # after reindexing by itemid.
    # -----------------------------------------------

    compare_cols = list(live.columns)

    old = (
        live
        .sort_values("itemid")
        .set_index("itemid")
    )

    new_existing = (
        candidate[
            candidate["itemid"].isin(
                live["itemid"]
            )
        ]
        .sort_values("itemid")
        .set_index("itemid")
    )

    existing_identical = int(
        old[[
            c
            for c in compare_cols
            if c != "itemid"
        ]].equals(
            new_existing[[
                c
                for c in compare_cols
                if c != "itemid"
            ]]
        )
    )

    if not existing_identical:
        raise PilotError(
            "Existing buyer rows changed."
        )

    # -----------------------------------------------
    # Faucet exposure
    #
    # With the hardened manager, there is one rate
    # roll per item/form/cycle.
    # -----------------------------------------------

    expected_successes_per_form_day = (
        TICKS_PER_DAY * BUY_RATE
    )

    audit_rows = []

    for _, row in additions.iterrows():
        iid = as_int(row["itemid"])

        single = as_int(
            row["price_single"]
        )

        stack = as_int(
            row["price_stacks"]
        )

        expected_daily_single = (
            single
            * expected_successes_per_form_day
        )

        expected_daily_stack = (
            stack
            * expected_successes_per_form_day
        )

        expected_daily_total = (
            expected_daily_single
            + expected_daily_stack
        )

        audit_rows.append({
            "itemid":
                iid,

            "name":
                row["name"],

            "price_single":
                single,

            "price_stacks":
                stack,

            "buy_rate_single":
                BUY_RATE,

            "buy_rate_stacks":
                BUY_RATE,

            "ticks_per_day":
                TICKS_PER_DAY,

            "expected_successes_per_form_day":
                expected_successes_per_form_day,

            "expected_daily_single_gil":
                expected_daily_single,

            "expected_daily_stack_gil":
                expected_daily_stack,

            "expected_daily_total_gil":
                expected_daily_total,

            "rate_semantic":
                "ONE_ROLL_PER_ITEM_FORM_PER_CYCLE",

            "pilot_only":
                1,

            "runtime_mutation_authorized":
                0,

            "activation_ready":
                0,

            "auto_live_promotion":
                0,
        })

    audit = pd.DataFrame(
        audit_rows
    ).sort_values("itemid")

    total_expected_daily = float(
        audit[
            "expected_daily_total_gil"
        ].sum()
    )

    candidate.to_csv(
        CANDIDATE_FILE,
        index=False,
    )

    audit.to_csv(
        AUDIT_FILE,
        index=False,
    )

    live_sha = sha256(
        LIVE_FILE
    )

    candidate_sha = sha256(
        CANDIDATE_FILE
    )

    summary = {
        "status":
            "PASS",

        "live_rows":
            len(live),

        "pilot_additions":
            len(additions),

        "candidate_rows":
            len(candidate),

        "pilot_itemids":
            sorted(PILOT_IDS),

        "existing_rows_identical":
            existing_identical,

        "buy_rate":
            BUY_RATE,

        "ticks_per_day":
            TICKS_PER_DAY,

        "expected_successes_per_form_day":
            expected_successes_per_form_day,

        "expected_pilot_daily_gil_if_continuously_supplied":
            total_expected_daily,

        "live_sha256":
            live_sha,

        "candidate_sha256":
            candidate_sha,

        "runtime_mutation_authorized":
            0,

        "database_mutation_authorized":
            0,

        "activation_ready":
            0,

        "auto_live_promotion":
            0,
    }

    SUMMARY_FILE.write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    print("=" * 104)
    print(
        " Phase 3C.3 Buyer Pilot B Candidate"
    )
    print("=" * 104)
    print()

    print(
        additions[
            [
                "itemid",
                "name",
                "buy_single",
                "price_single",
                "buy_rate_single",
                "buy_stacks",
                "price_stacks",
                "buy_rate_stacks",
            ]
        ]
        .sort_values("itemid")
        .to_string(index=False)
    )

    print()
    print("Live buyer rows:       ", len(live))
    print(
        "Pilot additions:       ",
        len(additions),
    )
    print(
        "Candidate buyer rows:  ",
        len(candidate),
    )

    print(
        "Existing rows unchanged:",
        existing_identical,
    )

    print()
    print(
        "Expected successful buys/form/day:",
        f"{expected_successes_per_form_day:.2f}",
    )

    print(
        "Expected Pilot B gil/day under "
        "continuous eligible supply:",
        f"{total_expected_daily:,.2f}",
    )

    print()
    print("Live SHA256:")
    print(live_sha)

    print()
    print("Candidate SHA256:")
    print(candidate_sha)

    print()
    print("Runtime mutation authorized:  0")
    print("Database mutation authorized: 0")
    print("Activation ready:              0")
    print("Auto live promotion:           0")

    print()
    print("Candidate:", CANDIDATE_FILE)
    print("Audit:    ", AUDIT_FILE)
    print("Summary:  ", SUMMARY_FILE)
    print()
    print("PASS")


if __name__ == "__main__":
    main()
