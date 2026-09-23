from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path.home() / "ffxiahbot"
TOOLS = ROOT / "economy" / "tools"
REPORTS = ROOT / "economy" / "reports"
GENERATED = ROOT / "economy" / "generated"

STAGES = (
    TOOLS / "build_market_history_classifier.py",
    TOOLS / "build_market_history_evidence.py",
    TOOLS / "build_market_history_intelligence.py",
    TOOLS / "build_market_history_confidence.py",
    TOOLS / "build_market_history_report.py",
)

CLASSIFIER_SUMMARY = REPORTS / "market-history-classifier-summary.json"
EXTRACTION_SUMMARY = REPORTS / "market-history-extraction-summary.json"
INTELLIGENCE_SUMMARY = REPORTS / "market-history-intelligence-summary.json"
CONFIDENCE_SUMMARY = REPORTS / "market-history-confidence-summary.json"
REPORT_SUMMARY = REPORTS / "market-history-report-summary.json"

SELLER_RUNTIME = GENERATED / "market-sell.csv"
BUYER_RUNTIME = GENERATED / "market-buy.csv"

VALIDATION_SUMMARY = REPORTS / "market-history-validation-summary.json"


class MarketHistoryValidationError(RuntimeError):
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
        raise MarketHistoryValidationError(
            f"Missing summary: {path}"
        )

    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


def require_zero(
    summary_name: str,
    summary: dict[str, Any],
    field: str,
) -> None:
    value = summary.get(field)

    if value != 0:
        raise MarketHistoryValidationError(
            f"{summary_name}: expected "
            f"{field}=0, found {value}"
        )


def run_stage(path: Path) -> None:
    if not path.exists():
        raise MarketHistoryValidationError(
            f"Missing stage tool: {path}"
        )

    print()
    print("=" * 84)
    print(f" RUNNING {path.name}")
    print("=" * 84)
    print()

    subprocess.run(
        [
            "uv",
            "run",
            "python",
            str(path),
        ],
        cwd=ROOT,
        check=True,
    )


def main() -> int:
    if not SELLER_RUNTIME.exists():
        raise MarketHistoryValidationError(
            f"Missing seller runtime: {SELLER_RUNTIME}"
        )

    if not BUYER_RUNTIME.exists():
        raise MarketHistoryValidationError(
            f"Missing buyer runtime: {BUYER_RUNTIME}"
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
        " Phase 2A.7 End-to-End "
        "Market History Validation"
    )
    print("=" * 84)

    print()
    print("Runtime hashes before:")
    print(
        f"  seller: {seller_before}"
    )
    print(
        f"  buyer:  {buyer_before}"
    )

    for stage in STAGES:
        run_stage(stage)

    seller_after = sha256(
        SELLER_RUNTIME
    )

    buyer_after = sha256(
        BUYER_RUNTIME
    )

    classifier = load_json(
        CLASSIFIER_SUMMARY
    )

    extraction = load_json(
        EXTRACTION_SUMMARY
    )

    intelligence = load_json(
        INTELLIGENCE_SUMMARY
    )

    confidence = load_json(
        CONFIDENCE_SUMMARY
    )

    report = load_json(
        REPORT_SUMMARY
    )

    summaries = (
        ("classifier", classifier),
        ("extraction", extraction),
        ("intelligence", intelligence),
        ("confidence", confidence),
        ("report", report),
    )

    integrity_failures = 0

    for name, summary in summaries:
        if summary.get("status") != "PASS":
            print(
                f"FAIL: {name} status "
                f"is {summary.get('status')}"
            )
            integrity_failures += 1

        if summary.get(
            "integrity_failures",
            0,
        ) != 0:
            print(
                f"FAIL: {name} has "
                "integrity failures"
            )
            integrity_failures += 1

        for field in (
            "history_price_influence_ready",
            "history_stock_influence_ready",
            "activation_ready",
            "auto_live_promotions",
        ):
            try:
                require_zero(
                    name,
                    summary,
                    field,
                )
            except MarketHistoryValidationError as exc:
                print(f"FAIL: {exc}")
                integrity_failures += 1

    completed = int(
        classifier[
            "completed_transactions"
        ]
    )

    synthetic = int(
        classifier[
            "synthetic_history_rows"
        ]
    )

    player_involved = int(
        classifier[
            "player_involved_transactions"
        ]
    )

    extracted_player = int(
        extraction[
            "player_involved_transactions"
        ]
    )

    excluded_synthetic = int(
        extraction[
            "synthetic_transactions_excluded"
        ]
    )

    report_completed = int(
        report[
            "completed_transactions"
        ]
    )

    report_player = int(
        report[
            "player_involved_transactions"
        ]
    )

    report_synthetic = int(
        report[
            "synthetic_transactions_excluded"
        ]
    )

    if completed != synthetic + player_involved:
        print(
            "FAIL: completed transactions "
            "do not equal synthetic + "
            "player-involved."
        )
        integrity_failures += 1

    if player_involved != extracted_player:
        print(
            "FAIL: classifier player-involved "
            "count disagrees with extraction."
        )
        integrity_failures += 1

    if synthetic != excluded_synthetic:
        print(
            "FAIL: synthetic classifier count "
            "disagrees with extraction exclusions."
        )
        integrity_failures += 1

    if report_completed != completed:
        print(
            "FAIL: final report completed count "
            "disagrees with classifier."
        )
        integrity_failures += 1

    if report_player != player_involved:
        print(
            "FAIL: final report player count "
            "disagrees with classifier."
        )
        integrity_failures += 1

    if report_synthetic != synthetic:
        print(
            "FAIL: final report synthetic count "
            "disagrees with classifier."
        )
        integrity_failures += 1

    if extraction.get(
        "synthetic_history_leaks",
        -1,
    ) != 0:
        print(
            "FAIL: synthetic history leaked "
            "into evidence."
        )
        integrity_failures += 1

    if seller_before != seller_after:
        print(
            "FAIL: market-sell.csv changed "
            "during history validation."
        )
        integrity_failures += 1

    if buyer_before != buyer_after:
        print(
            "FAIL: market-buy.csv changed "
            "during history validation."
        )
        integrity_failures += 1

    summary = {
        "status":
            (
                "PASS"
                if integrity_failures == 0
                else "FAIL"
            ),

        "stage":
            "2A.7_END_TO_END_MARKET_HISTORY_VALIDATION",

        "completed_transactions":
            completed,

        "synthetic_transactions":
            synthetic,

        "player_involved_transactions":
            player_involved,

        "evidence_events":
            int(
                extraction[
                    "evidence_events"
                ]
            ),

        "item_forms_reviewed":
            int(
                report[
                    "item_forms_reviewed"
                ]
            ),

        "trusted_and_recent_rows":
            int(
                report[
                    "trusted_and_recent_rows"
                ]
            ),

        "synthetic_history_leaks":
            int(
                extraction[
                    "synthetic_history_leaks"
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

        "seller_runtime_unchanged":
            int(
                seller_before
                == seller_after
            ),

        "buyer_runtime_unchanged":
            int(
                buyer_before
                == buyer_after
            ),

        "integrity_failures":
            integrity_failures,

        "history_price_influence_ready":
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
    print(" FINAL 2A.7 VALIDATION")
    print("=" * 84)

    print()
    print(
        f"Completed transactions:          "
        f"{completed:>6}"
    )

    print(
        f"Synthetic transactions:          "
        f"{synthetic:>6}"
    )

    print(
        f"Player-involved transactions:    "
        f"{player_involved:>6}"
    )

    print(
        f"Evidence events:                 "
        f"{summary['evidence_events']:>6}"
    )

    print(
        f"Item forms reviewed:             "
        f"{summary['item_forms_reviewed']:>6}"
    )

    print(
        f"Trusted + recent:                "
        f"{summary['trusted_and_recent_rows']:>6}"
    )

    print()
    print(
        f"Synthetic history leaks:         "
        f"{summary['synthetic_history_leaks']:>6}"
    )

    print(
        f"Seller runtime unchanged:        "
        f"{summary['seller_runtime_unchanged']:>6}"
    )

    print(
        f"Buyer runtime unchanged:         "
        f"{summary['buyer_runtime_unchanged']:>6}"
    )

    print()
    print(
        f"Integrity failures:              "
        f"{integrity_failures:>6}"
    )

    print()
    print(
        "History price influence ready:       0"
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

    if integrity_failures:
        print("FAIL")

        raise MarketHistoryValidationError(
            "2A.7 end-to-end validation "
            "failed."
        )

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
