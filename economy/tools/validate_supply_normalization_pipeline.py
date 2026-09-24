from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path.home() / "ffxiahbot"
TOOLS = ROOT / "economy" / "tools"
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

SELLER_RUNTIME = GENERATED / "market-sell.csv"
BUYER_RUNTIME = GENERATED / "market-buy.csv"

CANDIDATE = (
    GENERATED
    / "market-sell-stock-normalization-cutover.csv"
)

CENSUS_TOOL = (
    TOOLS
    / "build_active_supply_census.py"
)

PRESSURE_TOOL = (
    TOOLS
    / "build_supply_pressure_report.py"
)

POLICY_TOOL = (
    TOOLS
    / "build_safe_stock_policy.py"
)

CANDIDATE_TOOL = (
    TOOLS
    / "build_stock_normalization_candidate.py"
)

CENSUS_SUMMARY = (
    REPORTS
    / "active-supply-census-summary.json"
)

PRESSURE_SUMMARY = (
    REPORTS
    / "supply-pressure-summary.json"
)

POLICY_SUMMARY = (
    REPORTS
    / "safe-stock-policy-summary.json"
)

CANDIDATE_SUMMARY = (
    REPORTS
    / "stock-normalization-candidate-summary.json"
)

VALIDATION_SUMMARY = (
    REPORTS
    / "supply-normalization-validation-summary.json"
)


class SupplyValidationError(RuntimeError):
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
        raise SupplyValidationError(
            f"Missing summary: {path}"
        )

    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


def run_stage(path: Path) -> None:
    if not path.exists():
        raise SupplyValidationError(
            f"Missing tool: {path}"
        )

    print()
    print("=" * 84)
    print(f" RUNNING {path.name}")
    print("=" * 84)
    print()

    subprocess.run(
        [
            sys.executable,
            str(path),
        ],
        cwd=ROOT,
        check=True,
    )


def main() -> int:
    for path in (
        SELLER_RUNTIME,
        BUYER_RUNTIME,
    ):
        if not path.exists():
            raise SupplyValidationError(
                f"Missing runtime file: {path}"
            )

    seller_before = sha256(
        SELLER_RUNTIME
    )

    buyer_before = sha256(
        BUYER_RUNTIME
    )

    print()
    print("=" * 84)
    print(
        " Phase 2B.5 End-to-End "
        "Supply Normalization Validation"
    )
    print("=" * 84)

    print()
    print("Runtime hashes before:")
    print(f"  seller: {seller_before}")
    print(f"  buyer:  {buyer_before}")

    # Do NOT record a new snapshot here.
    # Pressure analysis reuses the existing
    # observation history.
    for stage in (
        CENSUS_TOOL,
        PRESSURE_TOOL,
        POLICY_TOOL,
        CANDIDATE_TOOL,
    ):
        run_stage(stage)

    seller_after = sha256(
        SELLER_RUNTIME
    )

    buyer_after = sha256(
        BUYER_RUNTIME
    )

    candidate_sha = sha256(
        CANDIDATE
    )

    census = load_json(
        CENSUS_SUMMARY
    )

    pressure = load_json(
        PRESSURE_SUMMARY
    )

    policy = load_json(
        POLICY_SUMMARY
    )

    candidate = load_json(
        CANDIDATE_SUMMARY
    )

    summaries = (
        ("census", census),
        ("pressure", pressure),
        ("policy", policy),
        ("candidate", candidate),
    )

    failures = 0

    for name, summary in summaries:
        if summary.get("status") != "PASS":
            print(
                f"FAIL: {name} status is "
                f"{summary.get('status')}"
            )
            failures += 1

        if summary.get(
            "integrity_failures",
            0,
        ) != 0:
            print(
                f"FAIL: {name} has "
                "integrity failures"
            )
            failures += 1

    # Count consistency.
    census_forms = int(
        census[
            "configured_item_forms"
        ]
    )

    pressure_forms = int(
        pressure[
            "item_forms_evaluated"
        ]
    )

    policy_forms = int(
        policy[
            "item_forms_evaluated"
        ]
    )

    candidate_forms = int(
        candidate[
            "policy_item_forms"
        ]
    )

    if not (
        census_forms
        == pressure_forms
        == policy_forms
        == candidate_forms
    ):
        print(
            "FAIL: item-form counts disagree "
            "across 2B stages."
        )
        failures += 1

    # No target changes currently authorized.
    if int(
        policy[
            "recommended_target_changes"
        ]
    ) != 0:
        print(
            "FAIL: policy recommends target "
            "changes in an unauthorized state."
        )
        failures += 1

    if int(
        candidate[
            "candidate_changed_rows"
        ]
    ) != 0:
        print(
            "FAIL: candidate changed rows."
        )
        failures += 1

    if int(
        candidate[
            "candidate_changed_cells"
        ]
    ) != 0:
        print(
            "FAIL: candidate changed cells."
        )
        failures += 1

    # Runtime/candidate safety.
    seller_unchanged = int(
        seller_before
        == seller_after
    )

    buyer_unchanged = int(
        buyer_before
        == buyer_after
    )

    candidate_matches_runtime = int(
        candidate_sha
        == seller_after
    )

    if not seller_unchanged:
        print(
            "FAIL: seller runtime changed."
        )
        failures += 1

    if not buyer_unchanged:
        print(
            "FAIL: buyer runtime changed."
        )
        failures += 1

    if not candidate_matches_runtime:
        print(
            "FAIL: stock candidate does not "
            "match seller runtime."
        )
        failures += 1

    # Fail closed on every mutation authority.
    authority_checks = (
        (
            "census",
            census,
            (
                "stock_change_authorized",
                "activation_ready",
                "auto_live_promotions",
            ),
        ),
        (
            "pressure",
            pressure,
            (
                "stock_change_authorized",
                "activation_ready",
                "auto_live_promotions",
            ),
        ),
        (
            "policy",
            policy,
            (
                "stock_change_authorized",
                "active_listing_change_authorized",
                "history_stock_influence_ready",
                "activation_ready",
                "auto_live_promotions",
            ),
        ),
        (
            "candidate",
            candidate,
            (
                "deployment_change_authorized",
                "stock_change_authorized",
                "active_listing_change_authorized",
                "activation_ready",
                "auto_live_promotions",
            ),
        ),
    )

    for (
        name,
        summary,
        fields,
    ) in authority_checks:
        for field in fields:
            if summary.get(field, 0) != 0:
                print(
                    f"FAIL: {name} has "
                    f"{field}="
                    f"{summary.get(field)}"
                )
                failures += 1

    summary = {
        "status":
            (
                "PASS"
                if failures == 0
                else "FAIL"
            ),

        "stage":
            "2B.5_END_TO_END_SUPPLY_VALIDATION",

        "configured_item_forms":
            census_forms,

        "active_auction_rows":
            int(
                census[
                    "global_active_auction_rows"
                ]
            ),

        "ahbot_active_rows":
            int(
                census[
                    "global_ahbot_active_rows"
                ]
            ),

        "player_active_rows":
            int(
                census[
                    "global_player_active_rows"
                ]
            ),

        "snapshots":
            int(
                pressure[
                    "snapshots"
                ]
            ),

        "persistence_ready_forms":
            int(
                pressure[
                    "persistence_ready_forms"
                ]
            ),

        "recommended_target_changes":
            int(
                policy[
                    "recommended_target_changes"
                ]
            ),

        "candidate_changed_rows":
            int(
                candidate[
                    "candidate_changed_rows"
                ]
            ),

        "candidate_changed_cells":
            int(
                candidate[
                    "candidate_changed_cells"
                ]
            ),

        "seller_runtime_sha256_before":
            seller_before,

        "seller_runtime_sha256_after":
            seller_after,

        "buyer_runtime_sha256_before":
            buyer_before,

        "buyer_runtime_sha256_after":
            buyer_after,

        "candidate_sha256":
            candidate_sha,

        "seller_runtime_unchanged":
            seller_unchanged,

        "buyer_runtime_unchanged":
            buyer_unchanged,

        "candidate_exact_runtime_match":
            candidate_matches_runtime,

        "integrity_failures":
            failures,

        "deployment_change_authorized":
            0,

        "stock_change_authorized":
            0,

        "active_listing_change_authorized":
            0,

        "history_stock_influence_ready":
            0,

        "activation_ready":
            0,

        "auto_live_promotions":
            0,
    }

    VALIDATION_SUMMARY.write_text(
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
    print(" FINAL 2B.5 VALIDATION")
    print("=" * 84)

    print()
    print(
        f"Configured item forms:           "
        f"{census_forms:>6}"
    )

    print(
        f"Active auction rows:             "
        f"{summary['active_auction_rows']:>6}"
    )

    print(
        f"AHBot active rows:               "
        f"{summary['ahbot_active_rows']:>6}"
    )

    print(
        f"Player active rows:              "
        f"{summary['player_active_rows']:>6}"
    )

    print(
        f"Supply snapshots:                "
        f"{summary['snapshots']:>6}"
    )

    print(
        f"Persistence-ready forms:         "
        f"{summary['persistence_ready_forms']:>6}"
    )

    print()
    print(
        f"Recommended target changes:      "
        f"{summary['recommended_target_changes']:>6}"
    )

    print(
        f"Candidate changed rows:          "
        f"{summary['candidate_changed_rows']:>6}"
    )

    print(
        f"Candidate changed cells:         "
        f"{summary['candidate_changed_cells']:>6}"
    )

    print()
    print(
        f"Seller runtime unchanged:        "
        f"{seller_unchanged:>6}"
    )

    print(
        f"Buyer runtime unchanged:         "
        f"{buyer_unchanged:>6}"
    )

    print(
        f"Candidate exact runtime match:   "
        f"{candidate_matches_runtime:>6}"
    )

    print()
    print(
        f"Integrity failures:              "
        f"{failures:>6}"
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
        "History stock influence ready:       0"
    )

    print(
        "Activation ready:                    0"
    )

    print(
        "Auto live promotions:                0"
    )

    print()
    print(
        f"Summary: {VALIDATION_SUMMARY}"
    )

    print()

    if failures:
        print("FAIL")
        raise SupplyValidationError(
            "2B.5 supply validation failed."
        )

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
