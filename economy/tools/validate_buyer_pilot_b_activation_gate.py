from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd

from ffxiahbot.auction.manager import Manager
from ffxiahbot.config import Config
from ffxiahbot.tables.auctionhouse import AuctionHouse


ROOT = Path.home() / "ffxiahbot"
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

CONFIG_FILE = (
    ROOT
    / "bin"
    / "config.yaml"
)

BUYER_RUNTIME = (
    GENERATED
    / "market-buy.csv"
)

CANDIDATE = (
    GENERATED
    / "market-buy-pilot-b-candidate.csv"
)

VALIDATION_SUMMARY = (
    REPORTS
    / "buyer-pilot-b-expansion-validation-summary.json"
)

SNAPSHOT_FILE = (
    REPORTS
    / "buyer-pilot-b-preactivation-market-snapshot.csv"
)

SUMMARY_FILE = (
    REPORTS
    / "buyer-pilot-b-activation-gate-summary.json"
)

SERVICE = "ffxiahbot-buyer.service"

PILOT_IDS = {
    5644,
    5653,
    5655,
}

EXPECTED_RUNTIME_ROWS = 152
EXPECTED_CANDIDATE_ROWS = 155
EXPECTED_TICK = 300
EXPECTED_RATE = 0.015


class ActivationGateError(RuntimeError):
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


def service_is_active() -> bool:
    result = subprocess.run(
        [
            "systemctl",
            "is-active",
            "--quiet",
            SERVICE,
        ],
        check=False,
    )

    return result.returncode == 0


def as_int(value) -> int:
    if value is None or pd.isna(value):
        return 0

    return int(float(value))


def main() -> int:
    required = [
        CONFIG_FILE,
        BUYER_RUNTIME,
        CANDIDATE,
        VALIDATION_SUMMARY,
    ]

    for path in required:
        if not path.exists():
            raise ActivationGateError(
                f"Missing required input: {path}"
            )

    validation = json.loads(
        VALIDATION_SUMMARY.read_text(
            encoding="utf-8",
        )
    )

    if validation.get("status") != "PASS":
        raise ActivationGateError(
            "3C.4 validation is not PASS."
        )

    if int(
        validation.get(
            "integrity_failures",
            1,
        )
    ) != 0:
        raise ActivationGateError(
            "3C.4 contains integrity failures."
        )

    buyer_hash = sha256(
        BUYER_RUNTIME
    )

    candidate_hash = sha256(
        CANDIDATE
    )

    if (
        buyer_hash
        != validation.get(
            "buyer_runtime_sha256"
        )
    ):
        raise ActivationGateError(
            "Live buyer runtime changed "
            "since 3C.4."
        )

    if (
        candidate_hash
        != validation.get(
            "candidate_sha256"
        )
    ):
        raise ActivationGateError(
            "Pilot B candidate changed "
            "since 3C.4."
        )

    live = pd.read_csv(
        BUYER_RUNTIME,
        low_memory=False,
    )

    candidate = pd.read_csv(
        CANDIDATE,
        low_memory=False,
    )

    if len(live) != EXPECTED_RUNTIME_ROWS:
        raise ActivationGateError(
            f"Expected {EXPECTED_RUNTIME_ROWS} "
            "live buyer rows."
        )

    if len(candidate) != EXPECTED_CANDIDATE_ROWS:
        raise ActivationGateError(
            f"Expected {EXPECTED_CANDIDATE_ROWS} "
            "candidate rows."
        )

    pilot = candidate[
        candidate["itemid"]
        .astype(int)
        .isin(PILOT_IDS)
    ].copy()

    if len(pilot) != 3:
        raise ActivationGateError(
            "Expected exactly three Pilot B rows."
        )

    # ------------------------------------------------
    # Load the exact config the service uses.
    # ------------------------------------------------

    config = Config.from_yaml(
        CONFIG_FILE
    )

    if int(config.tick) != EXPECTED_TICK:
        raise ActivationGateError(
            "Buyer tick is not 300 seconds."
        )

    if not config.use_buying_rates:
        raise ActivationGateError(
            "use_buying_rates is not enabled."
        )

    # ------------------------------------------------
    # Connect using the project's normal DB path.
    # Read-only inspection only.
    # ------------------------------------------------

    manager = (
        Manager.create_database_and_manager(
            hostname=config.hostname,
            database=config.database,
            username=config.username,
            password=config.password,
            port=config.port,
            name=config.name,
            fail=True,
        )
    )

    if not manager.can_connect():
        raise ActivationGateError(
            "Cannot connect to auction database."
        )

    pilot_by_id = (
        pilot
        .set_index("itemid")
    )

    rows_out = []

    with manager.scoped_session() as session:
        rows = (
            session.query(AuctionHouse)
            .filter(
                AuctionHouse.itemid.in_(
                    sorted(PILOT_IDS)
                )
            )
            .all()
        )

        for iid in sorted(PILOT_IDS):
            p = pilot_by_id.loc[iid]

            for stack in [0, 1]:
                bid = as_int(
                    p[
                        "price_stacks"
                        if stack
                        else "price_single"
                    ]
                )

                matching = [
                    row
                    for row in rows
                    if int(row.itemid) == iid
                    and int(row.stack) == stack
                ]

                active_player = [
                    row
                    for row in matching
                    if int(row.seller) != 0
                    and int(row.sale) == 0
                    and int(row.sell_date) == 0
                ]

                eligible = [
                    row
                    for row in active_player
                    if int(row.price) <= bid
                ]

                completed = [
                    row
                    for row in matching
                    if int(row.sell_date) != 0
                ]

                asks = [
                    int(row.price)
                    for row in active_player
                ]

                eligible_asks = [
                    int(row.price)
                    for row in eligible
                ]

                rows_out.append({
                    "itemid":
                        iid,

                    "name":
                        p["name"],

                    "stack":
                        stack,

                    "configured_bid":
                        bid,

                    "buy_rate":
                        float(
                            p[
                                "buy_rate_stacks"
                                if stack
                                else "buy_rate_single"
                            ]
                        ),

                    "active_player_listings":
                        len(active_player),

                    "eligible_player_listings":
                        len(eligible),

                    "minimum_active_ask":
                        min(asks)
                        if asks
                        else None,

                    "maximum_active_ask":
                        max(asks)
                        if asks
                        else None,

                    "minimum_eligible_ask":
                        min(eligible_asks)
                        if eligible_asks
                        else None,

                    "completed_history_rows":
                        len(completed),

                    "maximum_purchase_per_cycle":
                        1,

                    "rate_semantic":
                        "ONE_ROLL_PER_ITEM_FORM_PER_CYCLE",
                })

    snapshot = pd.DataFrame(
        rows_out
    ).sort_values(
        [
            "itemid",
            "stack",
        ]
    )

    snapshot.to_csv(
        SNAPSHOT_FILE,
        index=False,
    )

    active_player_total = int(
        snapshot[
            "active_player_listings"
        ].sum()
    )

    eligible_total = int(
        snapshot[
            "eligible_player_listings"
        ].sum()
    )

    history_total = int(
        snapshot[
            "completed_history_rows"
        ].sum()
    )

    service_active = int(
        service_is_active()
    )

    failures = []

    if not service_active:
        failures.append(
            "Buyer service is not active "
            "before activation."
        )

    if any(
        abs(
            float(rate)
            - EXPECTED_RATE
        )
        > 1e-12
        for rate in pilot[
            "buy_rate_single"
        ]
    ):
        failures.append(
            "Unexpected Pilot B single rate."
        )

    if any(
        abs(
            float(rate)
            - EXPECTED_RATE
        )
        > 1e-12
        for rate in pilot[
            "buy_rate_stacks"
        ]
    ):
        failures.append(
            "Unexpected Pilot B stack rate."
        )

    summary = {
        "status":
            "PASS"
            if not failures
            else "FAIL",

        "activation_gate":
            (
                "READY_FOR_MANUAL_ACTIVATION"
                if not failures
                else "BLOCKED"
            ),

        "live_buyer_rows":
            len(live),

        "candidate_buyer_rows":
            len(candidate),

        "pilot_items":
            len(pilot),

        "tick_seconds":
            int(config.tick),

        "use_buying_rates":
            bool(
                config.use_buying_rates
            ),

        "buyer_service_active":
            service_active,

        "buyer_runtime_sha256":
            buyer_hash,

        "candidate_sha256":
            candidate_hash,

        "active_player_listings":
            active_player_total,

        "currently_eligible_player_listings":
            eligible_total,

        "completed_history_rows":
            history_total,

        "service_restart_required_for_hardened_code":
            1,

        "database_mutation_performed":
            0,

        "runtime_mutation_performed":
            0,

        "completed_history_modified":
            0,

        "automatic_activation_authorized":
            0,

        "manual_activation_gate_ready":
            int(
                not failures
            ),

        "failures":
            failures,
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

    print("=" * 104)
    print(
        " Phase 3C.5 Buyer Pilot B "
        "Final Activation Gate"
    )
    print("=" * 104)
    print()

    print(
        snapshot.to_string(
            index=False
        )
    )

    print()
    print(
        "Live buyer rows:              ",
        len(live),
    )

    print(
        "Candidate buyer rows:         ",
        len(candidate),
    )

    print(
        "Buyer tick seconds:           ",
        int(config.tick),
    )

    print(
        "Buying rates enabled:         ",
        int(
            config.use_buying_rates
        ),
    )

    print(
        "Buyer service active:         ",
        service_active,
    )

    print()
    print(
        "Active player listings:       ",
        active_player_total,
    )

    print(
        "Currently eligible listings:  ",
        eligible_total,
    )

    print(
        "Existing completed history:   ",
        history_total,
    )

    print()
    print(
        "Service restart required:     1"
    )

    print(
        "Database mutation performed:  0"
    )

    print(
        "Runtime mutation performed:   0"
    )

    print(
        "Completed history modified:   0"
    )

    print(
        "Automatic activation:         0"
    )

    print()
    print(
        "Activation gate:",
        summary[
            "activation_gate"
        ],
    )

    print()
    print(
        "Snapshot:",
        SNAPSHOT_FILE,
    )

    print(
        "Summary: ",
        SUMMARY_FILE,
    )

    print()
    print(summary["status"])

    if failures:
        raise SystemExit(1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
