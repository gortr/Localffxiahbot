from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path.home() / "ffxiahbot"
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

OBSERVATIONS = (
    ROOT
    / "economy"
    / "runtime-observations"
)

SUPPLY_OBSERVATIONS = (
    OBSERVATIONS
    / "active-supply"
)

MATURITY_OBSERVATIONS = (
    OBSERVATIONS
    / "maturity"
)

SELLER_RUNTIME = (
    GENERATED
    / "market-sell.csv"
)

BUYER_RUNTIME = (
    GENERATED
    / "market-buy.csv"
)

SNAPSHOT_INDEX = (
    SUPPLY_OBSERVATIONS
    / "snapshot-index.csv"
)

MATURITY_STATE = (
    MATURITY_OBSERVATIONS
    / "maturity-state.csv"
)

REFRESH_SUMMARY = (
    REPORTS
    / "market-intelligence-refresh-summary.json"
)

HISTORY_SUMMARY = (
    REPORTS
    / "market-history-validation-summary.json"
)

SUPPLY_SUMMARY = (
    REPORTS
    / "supply-normalization-validation-summary.json"
)

MATURITY_SUMMARY = (
    REPORTS
    / "market-maturity-validation-summary.json"
)

CENSUS_SUMMARY = (
    REPORTS
    / "active-supply-census-summary.json"
)

PRESSURE_SUMMARY = (
    REPORTS
    / "supply-pressure-summary.json"
)

OUTPUT_JSON = (
    REPORTS
    / "market-intelligence-health.json"
)

OUTPUT_MD = (
    REPORTS
    / "market-intelligence-health.md"
)


SELLER_SERVICE = "ffxiahbot-seller.service"
BUYER_SERVICE = "ffxiahbot-buyer.service"

INTELLIGENCE_SERVICE = (
    "ffxiahbot-supply-snapshot.service"
)

INTELLIGENCE_TIMER = (
    "ffxiahbot-supply-snapshot.timer"
)


# Timer runs every six hours.
# Allow two hours of scheduling/maintenance grace.
FRESH_PASS_HOURS = 8

# More than one missed six-hour cycle is unhealthy.
FRESH_FAIL_HOURS = 14


class HealthCheckError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(
                1024 * 1024
            ),
            b"",
        ):
            h.update(chunk)

    return h.hexdigest()


def load_json(
    path: Path,
) -> dict[str, Any]:
    if not path.exists():
        raise HealthCheckError(
            f"Missing required file: {path}"
        )

    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


def systemctl(
    *args: str,
) -> tuple[int, str]:
    result = subprocess.run(
        [
            "systemctl",
            *args,
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    text = (
        result.stdout.strip()
        or result.stderr.strip()
    )

    return (
        result.returncode,
        text,
    )


def parse_timestamp(
    value: Any,
) -> datetime:
    text = str(
        value
    ).strip()

    if not text:
        raise HealthCheckError(
            "Empty timestamp."
        )

    timestamp = datetime.fromisoformat(
        text.replace(
            "Z",
            "+00:00",
        )
    )

    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(
            tzinfo=timezone.utc
        )

    return timestamp.astimezone(
        timezone.utc
    )


def age_hours(
    timestamp: datetime,
    now: datetime,
) -> float:
    return max(
        (
            now
            - timestamp
        ).total_seconds()
        / 3600,
        0.0,
    )


def freshness_status(
    hours: float,
) -> str:
    if hours <= FRESH_PASS_HOURS:
        return "PASS"

    if hours <= FRESH_FAIL_HOURS:
        return "WARN"

    return "FAIL"


def csv_row_count(
    path: Path,
) -> int:
    if not path.exists():
        raise HealthCheckError(
            f"Missing CSV: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        reader = csv.DictReader(
            handle
        )

        return sum(
            1
            for _ in reader
        )


def latest_snapshot() -> tuple[
    int,
    datetime,
    str,
]:
    if not SNAPSHOT_INDEX.exists():
        raise HealthCheckError(
            "Snapshot index is missing."
        )

    rows = []

    with SNAPSHOT_INDEX.open(
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        reader = csv.DictReader(
            handle
        )

        for row in reader:
            rows.append(
                row
            )

    if not rows:
        raise HealthCheckError(
            "Snapshot index is empty."
        )

    last = rows[-1]

    return (
        len(rows),
        parse_timestamp(
            last[
                "observed_at_utc"
            ]
        ),
        str(
            last[
                "snapshot_id"
            ]
        ),
    )


def as_int(
    value: Any,
) -> int:
    if value is None:
        return 0

    try:
        return int(value)
    except (
        TypeError,
        ValueError,
    ):
        return 0


def add_check(
    checks: list[dict[str, Any]],
    *,
    category: str,
    name: str,
    status: str,
    value: Any,
    detail: str = "",
) -> None:
    checks.append({
        "category":
            category,

        "name":
            name,

        "status":
            status,

        "value":
            value,

        "detail":
            detail,
    })


def overall_status(
    checks: list[dict[str, Any]],
) -> str:
    statuses = {
        str(
            check[
                "status"
            ]
        )
        for check in checks
    }

    if "FAIL" in statuses:
        return "FAIL"

    if "WARN" in statuses:
        return "WARN"

    return "PASS"


def main() -> int:
    now = datetime.now(
        timezone.utc
    )

    checks: list[
        dict[str, Any]
    ] = []

    # -------------------------------------------------
    # Load current intelligence summaries
    # -------------------------------------------------

    refresh = load_json(
        REFRESH_SUMMARY
    )

    history = load_json(
        HISTORY_SUMMARY
    )

    supply = load_json(
        SUPPLY_SUMMARY
    )

    maturity = load_json(
        MATURITY_SUMMARY
    )

    census = load_json(
        CENSUS_SUMMARY
    )

    pressure = load_json(
        PRESSURE_SUMMARY
    )

    # -------------------------------------------------
    # Services
    # -------------------------------------------------

    for service, label in (
        (
            SELLER_SERVICE,
            "Seller service",
        ),
        (
            BUYER_SERVICE,
            "Buyer service",
        ),
    ):
        returncode, value = systemctl(
            "is-active",
            service,
        )

        add_check(
            checks,
            category="Services",
            name=label,
            status=(
                "PASS"
                if (
                    returncode == 0
                    and value == "active"
                )
                else "FAIL"
            ),
            value=value,
        )

    timer_rc, timer_state = systemctl(
        "is-active",
        INTELLIGENCE_TIMER,
    )

    add_check(
        checks,
        category="Services",
        name="Intelligence timer active",
        status=(
            "PASS"
            if (
                timer_rc == 0
                and timer_state == "active"
            )
            else "FAIL"
        ),
        value=timer_state,
    )

    enabled_rc, enabled_state = (
        systemctl(
            "is-enabled",
            INTELLIGENCE_TIMER,
        )
    )

    add_check(
        checks,
        category="Services",
        name="Intelligence timer enabled",
        status=(
            "PASS"
            if (
                enabled_rc == 0
                and enabled_state
                in {
                    "enabled",
                    "enabled-runtime",
                }
            )
            else "FAIL"
        ),
        value=enabled_state,
    )

    _, exec_start = systemctl(
        "show",
        INTELLIGENCE_SERVICE,
        "-p",
        "ExecStart",
        "--value",
    )

    unified_loaded = (
        "refresh_market_intelligence.py"
        in exec_start
    )

    add_check(
        checks,
        category="Services",
        name="Unified refresh ExecStart",
        status=(
            "PASS"
            if unified_loaded
            else "FAIL"
        ),
        value=(
            "loaded"
            if unified_loaded
            else "unexpected"
        ),
        detail=exec_start,
    )

    # -------------------------------------------------
    # Latest validation status
    # -------------------------------------------------

    for label, summary in (
        (
            "2A market history",
            history,
        ),
        (
            "2B supply intelligence",
            supply,
        ),
        (
            "2C market maturity",
            maturity,
        ),
        (
            "Unified refresh",
            refresh,
        ),
    ):
        status = str(
            summary.get(
                "status",
                "MISSING",
            )
        )

        integrity = as_int(
            summary.get(
                "integrity_failures",
                0,
            )
        )

        add_check(
            checks,
            category="Pipeline",
            name=label,
            status=(
                "PASS"
                if (
                    status == "PASS"
                    and integrity == 0
                )
                else "FAIL"
            ),
            value=status,
            detail=(
                f"integrity_failures="
                f"{integrity}"
            ),
        )

    # -------------------------------------------------
    # Freshness
    # -------------------------------------------------

    refresh_time = parse_timestamp(
        refresh[
            "finished_at_utc"
        ]
    )

    refresh_age = age_hours(
        refresh_time,
        now,
    )

    add_check(
        checks,
        category="Freshness",
        name="Last unified refresh",
        status=freshness_status(
            refresh_age
        ),
        value=f"{refresh_age:.2f}h ago",
        detail=refresh_time.isoformat(),
    )

    (
        snapshot_count,
        latest_snapshot_time,
        latest_snapshot_id,
    ) = latest_snapshot()

    snapshot_age = age_hours(
        latest_snapshot_time,
        now,
    )

    add_check(
        checks,
        category="Freshness",
        name="Latest supply snapshot",
        status=freshness_status(
            snapshot_age
        ),
        value=f"{snapshot_age:.2f}h ago",
        detail=latest_snapshot_id,
    )

    # -------------------------------------------------
    # Observation counts
    # -------------------------------------------------

    configured_forms = as_int(
        census.get(
            "configured_item_forms"
        )
    )

    maturity_rows = csv_row_count(
        MATURITY_STATE
    )

    pressure_forms = as_int(
        pressure.get(
            "item_forms_evaluated"
        )
    )

    add_check(
        checks,
        category="Data",
        name="Configured item forms",
        status=(
            "PASS"
            if configured_forms > 0
            else "FAIL"
        ),
        value=configured_forms,
    )

    add_check(
        checks,
        category="Data",
        name="Maturity ledger rows",
        status=(
            "PASS"
            if maturity_rows
            == configured_forms
            else "FAIL"
        ),
        value=maturity_rows,
        detail=(
            f"expected="
            f"{configured_forms}"
        ),
    )

    add_check(
        checks,
        category="Data",
        name="Supply pressure forms",
        status=(
            "PASS"
            if pressure_forms
            == configured_forms
            else "FAIL"
        ),
        value=pressure_forms,
        detail=(
            f"expected="
            f"{configured_forms}"
        ),
    )

    add_check(
        checks,
        category="Data",
        name="Supply snapshot count",
        status=(
            "PASS"
            if snapshot_count > 0
            else "FAIL"
        ),
        value=snapshot_count,
    )

    # -------------------------------------------------
    # Auction census integrity
    # -------------------------------------------------

    unknown_rows = as_int(
        census.get(
            "global_unknown_active_rows"
        )
    )

    unconfigured_rows = as_int(
        census.get(
            "unconfigured_active_rows"
        )
    )

    add_check(
        checks,
        category="Auction",
        name="Unknown active listings",
        status=(
            "PASS"
            if unknown_rows == 0
            else "FAIL"
        ),
        value=unknown_rows,
    )

    add_check(
        checks,
        category="Auction",
        name="Unconfigured active listings",
        status=(
            "PASS"
            if unconfigured_rows == 0
            else "WARN"
        ),
        value=unconfigured_rows,
        detail=(
            "WARN rather than automatic "
            "failure because future player "
            "listings may legitimately be "
            "outside the configured bot catalog."
        ),
    )

    # -------------------------------------------------
    # Runtime hashes
    # -------------------------------------------------

    if not SELLER_RUNTIME.exists():
        raise HealthCheckError(
            f"Missing runtime: {SELLER_RUNTIME}"
        )

    if not BUYER_RUNTIME.exists():
        raise HealthCheckError(
            f"Missing runtime: {BUYER_RUNTIME}"
        )

    seller_hash = sha256(
        SELLER_RUNTIME
    )

    buyer_hash = sha256(
        BUYER_RUNTIME
    )

    expected_seller_hash = str(
        refresh.get(
            "seller_runtime_sha256_after",
            "",
        )
    )

    expected_buyer_hash = str(
        refresh.get(
            "buyer_runtime_sha256_after",
            "",
        )
    )

    add_check(
        checks,
        category="Runtime",
        name="Seller runtime hash",
        status=(
            "PASS"
            if (
                expected_seller_hash
                and seller_hash
                == expected_seller_hash
            )
            else "FAIL"
        ),
        value=(
            "match"
            if seller_hash
            == expected_seller_hash
            else "mismatch"
        ),
        detail=seller_hash,
    )

    add_check(
        checks,
        category="Runtime",
        name="Buyer runtime hash",
        status=(
            "PASS"
            if (
                expected_buyer_hash
                and buyer_hash
                == expected_buyer_hash
            )
            else "FAIL"
        ),
        value=(
            "match"
            if buyer_hash
            == expected_buyer_hash
            else "mismatch"
        ),
        detail=buyer_hash,
    )

    # -------------------------------------------------
    # Safety authority firewall
    # -------------------------------------------------

    authority_fields = {
        "history": (
            "history_price_influence_ready",
            "history_stock_influence_ready",
            "activation_ready",
            "auto_live_promotions",
        ),

        "supply": (
            "deployment_change_authorized",
            "stock_change_authorized",
            "active_listing_change_authorized",
            "history_stock_influence_ready",
            "activation_ready",
            "auto_live_promotions",
        ),

        "maturity": (
            "price_signal_use_authorized",
            "stock_signal_use_authorized",
            "promotion_effective_for_pricing",
            "promotion_effective_for_stock",
            "price_change_authorized",
            "stock_change_authorized",
            "active_listing_change_authorized",
            "activation_ready",
            "auto_live_promotions",
        ),

        "refresh": (
            "price_change_authorized",
            "stock_change_authorized",
            "active_listing_change_authorized",
            "activation_ready",
            "auto_live_promotions",
        ),
    }

    summary_lookup = {
        "history":
            history,

        "supply":
            supply,

        "maturity":
            maturity,

        "refresh":
            refresh,
    }

    authority_failures = []

    for group, fields in authority_fields.items():
        summary = summary_lookup[
            group
        ]

        for field in fields:
            value = as_int(
                summary.get(
                    field,
                    0,
                )
            )

            if value != 0:
                authority_failures.append(
                    f"{group}.{field}={value}"
                )

    add_check(
        checks,
        category="Safety",
        name="Mutation authority firewall",
        status=(
            "PASS"
            if not authority_failures
            else "FAIL"
        ),
        value=(
            "all disabled"
            if not authority_failures
            else "violation"
        ),
        detail=(
            "; ".join(
                authority_failures
            )
        ),
    )

    # -------------------------------------------------
    # Final state
    # -------------------------------------------------

    overall = overall_status(
        checks
    )

    status_counts = {
        state: sum(
            1
            for check in checks
            if check[
                "status"
            ]
            == state
        )
        for state in (
            "PASS",
            "WARN",
            "FAIL",
        )
    }

    output = {
        "status":
            overall,

        "stage":
            "2D.3_OPERATIONAL_HEALTH",

        "generated_at_utc":
            now.isoformat(),

        "freshness_thresholds_hours": {
            "pass_max":
                FRESH_PASS_HOURS,

            "fail_above":
                FRESH_FAIL_HOURS,
        },

        "configured_item_forms":
            configured_forms,

        "supply_snapshots":
            snapshot_count,

        "latest_snapshot_id":
            latest_snapshot_id,

        "latest_snapshot_at_utc":
            latest_snapshot_time.isoformat(),

        "latest_snapshot_age_hours":
            round(
                snapshot_age,
                4,
            ),

        "last_refresh_at_utc":
            refresh_time.isoformat(),

        "last_refresh_age_hours":
            round(
                refresh_age,
                4,
            ),

        "seller_runtime_sha256":
            seller_hash,

        "buyer_runtime_sha256":
            buyer_hash,

        "status_counts":
            status_counts,

        "checks":
            checks,

        "price_change_authorized":
            0,

        "stock_change_authorized":
            0,

        "active_listing_change_authorized":
            0,

        "activation_ready":
            0,

        "auto_live_promotions":
            0,
    }

    REPORTS.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUTPUT_JSON.write_text(
        json.dumps(
            output,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    lines = [
        "# AHBot Market Intelligence Health",
        "",
        f"**Overall:** {overall}",
        "",
        f"Generated: `{now.isoformat()}`",
        "",
        "## Summary",
        "",
        f"- Configured item-forms: **{configured_forms}**",
        f"- Supply snapshots: **{snapshot_count}**",
        f"- Latest snapshot age: **{snapshot_age:.2f} hours**",
        f"- Last unified refresh age: **{refresh_age:.2f} hours**",
        "",
        "## Checks",
        "",
        "| Category | Check | Status | Value |",
        "|---|---|---:|---|",
    ]

    for check in checks:
        lines.append(
            "| "
            + str(
                check[
                    "category"
                ]
            )
            + " | "
            + str(
                check[
                    "name"
                ]
            )
            + " | "
            + str(
                check[
                    "status"
                ]
            )
            + " | "
            + str(
                check[
                    "value"
                ]
            )
            + " |"
        )

    lines.extend([
        "",
        "## Safety",
        "",
        "- Price changes authorized: **0**",
        "- Stock changes authorized: **0**",
        "- Active listing changes authorized: **0**",
        "- Activation ready: **0**",
        "- Auto-live promotions: **0**",
        "",
    ])

    OUTPUT_MD.write_text(
        "\n".join(
            lines
        ),
        encoding="utf-8",
    )

    print()
    print("=" * 84)
    print(
        " Phase 2D.3 Operational "
        "Health Validation"
    )
    print("=" * 84)

    categories = []

    for check in checks:
        category = str(
            check[
                "category"
            ]
        )

        if category not in categories:
            categories.append(
                category
            )

    for category in categories:
        print()
        print(
            f"{category}:"
        )

        for check in checks:
            if (
                check[
                    "category"
                ]
                != category
            ):
                continue

            print(
                f"  "
                f"{check['name']:<36}"
                f"{check['status']:<6} "
                f"{check['value']}"
            )

    print()
    print(
        f"PASS checks:                     "
        f"{status_counts['PASS']:>6}"
    )

    print(
        f"WARN checks:                     "
        f"{status_counts['WARN']:>6}"
    )

    print(
        f"FAIL checks:                     "
        f"{status_counts['FAIL']:>6}"
    )

    print()
    print(
        "Price change authorized:            0"
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
        f"JSON:   {OUTPUT_JSON}"
    )

    print(
        f"Report: {OUTPUT_MD}"
    )

    print()
    print(
        f"OVERALL HEALTH: {overall}"
    )

    # WARN remains a successful process result so
    # temporary freshness warnings do not masquerade
    # as pipeline crashes.
    if overall == "FAIL":
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
