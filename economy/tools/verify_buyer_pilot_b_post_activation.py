from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ffxiahbot.auction.manager import Manager
from ffxiahbot.config import Config
from ffxiahbot.tables.auctionhouse import AuctionHouse


ROOT = Path.home() / "ffxiahbot"
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

OBS_ROOT = (
    ROOT
    / "economy"
    / "runtime-observations"
    / "buyer-pilot-b"
)

CONFIG_FILE = (
    ROOT
    / "bin"
    / "config.yaml"
)

BUYER_RUNTIME = (
    GENERATED
    / "market-buy.csv"
)

SELLER_RUNTIME = (
    GENERATED
    / "market-sell.csv"
)

CANDIDATE = (
    GENERATED
    / "market-buy-pilot-b-candidate.csv"
)

VALIDATION_SUMMARY = (
    REPORTS
    / "buyer-pilot-b-expansion-validation-summary.json"
)

MANAGER_FILE = (
    ROOT
    / "ffxiahbot"
    / "auction"
    / "manager.py"
)

SERVICE = "ffxiahbot-buyer.service"

PILOT_IDS = {
    5644,
    5653,
    5655,
}

EXPECTED_BUYER_ROWS = 155
EXPECTED_SELLER_ROWS = 173
EXPECTED_TICK = 300
EXPECTED_RATE = 0.015


class VerificationError(RuntimeError):
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


def systemctl_value(
    property_name: str,
) -> str:
    result = subprocess.run(
        [
            "systemctl",
            "show",
            SERVICE,
            "-p",
            property_name,
            "--value",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    return result.stdout.strip()


def get_current_invocation_log(
    invocation_id: str,
) -> str:
    if not invocation_id:
        return ""

    result = subprocess.run(
        [
            "journalctl",
            f"_SYSTEMD_INVOCATION_ID={invocation_id}",
            "--no-pager",
            "-o",
            "cat",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    return result.stdout


def as_int(value) -> int:
    if value is None or pd.isna(value):
        return 0

    return int(float(value))


def main() -> int:
    required = [
        CONFIG_FILE,
        BUYER_RUNTIME,
        SELLER_RUNTIME,
        CANDIDATE,
        VALIDATION_SUMMARY,
        MANAGER_FILE,
    ]

    for path in required:
        if not path.exists():
            raise VerificationError(
                f"Missing required input: {path}"
            )

    validation = json.loads(
        VALIDATION_SUMMARY.read_text(
            encoding="utf-8",
        )
    )

    if validation.get("status") != "PASS":
        raise VerificationError(
            "3C.4 validation is not PASS."
        )

    expected_candidate_hash = str(
        validation.get(
            "candidate_sha256",
            "",
        )
    )

    expected_seller_hash = str(
        validation.get(
            "seller_runtime_sha256",
            "",
        )
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

    buyer = pd.read_csv(
        BUYER_RUNTIME,
        low_memory=False,
    )

    candidate = pd.read_csv(
        CANDIDATE,
        low_memory=False,
    )

    seller = pd.read_csv(
        SELLER_RUNTIME,
        low_memory=False,
    )

    config = Config.from_yaml(
        CONFIG_FILE
    )

    active_state = systemctl_value(
        "ActiveState"
    )

    main_pid = systemctl_value(
        "MainPID"
    )

    invocation_id = systemctl_value(
        "InvocationID"
    )

    active_enter = systemctl_value(
        "ActiveEnterTimestamp"
    )

    journal = get_current_invocation_log(
        invocation_id
    )

    scheduler_started = int(
        "Scheduler started"
        in journal
    )

    buyer_cycles_started = (
        journal.count(
            'Running job "Buy Items'
        )
    )

    # Rich/systemd can wrap APScheduler's
    # "executed successfully" message across lines.
    # Match the complete Buy Items completion record
    # across arbitrary whitespace rather than relying
    # on a literal single-line phrase.
    successful_matches = re.findall(
        r'Job "Buy Items.*?executed\s+successfully',
        journal,
        flags=re.DOTALL,
    )

    buyer_cycles_successful = min(
        buyer_cycles_started,
        len(successful_matches),
    )

    job_exceptions = (
        journal.count(
            "raised an exception"
        )
        + journal.count(
            "Traceback (most recent call last)"
        )
    )

    manager_text = MANAGER_FILE.read_text(
        encoding="utf-8",
    )

    rate_gate_present = int(
        "rate_allowed_this_cycle"
        in manager_text
        and "purchase_key"
        in manager_text
    )

    failures = []

    if active_state != "active":
        failures.append(
            "Buyer service is not active."
        )

    if buyer_hash != expected_candidate_hash:
        failures.append(
            "Live buyer SHA does not match "
            "validated Pilot B candidate."
        )

    if candidate_hash != expected_candidate_hash:
        failures.append(
            "Candidate SHA changed after activation."
        )

    if seller_hash != expected_seller_hash:
        failures.append(
            "Seller runtime changed during "
            "Buyer Pilot B activation."
        )

    if len(buyer) != EXPECTED_BUYER_ROWS:
        failures.append(
            f"Expected {EXPECTED_BUYER_ROWS} "
            "live buyer rows."
        )

    if len(seller) != EXPECTED_SELLER_ROWS:
        failures.append(
            f"Expected {EXPECTED_SELLER_ROWS} "
            "seller rows."
        )

    if int(config.tick) != EXPECTED_TICK:
        failures.append(
            "Buyer tick changed."
        )

    if not config.use_buying_rates:
        failures.append(
            "Buying rates are disabled."
        )

    if not rate_gate_present:
        failures.append(
            "Hardened buyer rate gate "
            "not detected."
        )

    if not scheduler_started:
        failures.append(
            "Current buyer invocation did not "
            "log scheduler startup."
        )

    if job_exceptions:
        failures.append(
            "Current buyer invocation contains "
            "job exceptions."
        )

    live_pilot = buyer[
        buyer["itemid"]
        .astype(int)
        .isin(PILOT_IDS)
    ].copy()

    expected_pilot = candidate[
        candidate["itemid"]
        .astype(int)
        .isin(PILOT_IDS)
    ].copy()

    if len(live_pilot) != 3:
        failures.append(
            "Live runtime does not contain "
            "exactly three Pilot B rows."
        )

    if not (
        live_pilot
        .sort_values("itemid")
        .reset_index(drop=True)
        .equals(
            expected_pilot
            .sort_values("itemid")
            .reset_index(drop=True)
        )
    ):
        failures.append(
            "Live Pilot B rows differ "
            "from validated candidate."
        )

    for column in [
        "buy_rate_single",
        "buy_rate_stacks",
    ]:
        if any(
            abs(
                float(value)
                - EXPECTED_RATE
            )
            > 1e-12
            for value in live_pilot[
                column
            ]
        ):
            failures.append(
                f"Unexpected {column}."
            )

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
        raise VerificationError(
            "Cannot connect to auction database."
        )

    pilot_by_id = (
        live_pilot
        .set_index("itemid")
    )

    census_rows = []

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

                form_rows = [
                    row
                    for row in rows
                    if int(row.itemid) == iid
                    and int(row.stack) == stack
                ]

                active_player = [
                    row
                    for row in form_rows
                    if int(row.seller) != 0
                    and int(row.sale) == 0
                    and int(row.sell_date) == 0
                ]

                active_bot = [
                    row
                    for row in form_rows
                    if int(row.seller) == 0
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
                    for row in form_rows
                    if int(row.sell_date) != 0
                ]

                ahbot_buys = [
                    row
                    for row in completed
                    if int(row.seller) != 0
                    and str(
                        row.buyer_name or ""
                    ).strip()
                    == config.name
                ]

                organic_completed = [
                    row
                    for row in completed
                    if int(row.seller) != 0
                    and str(
                        row.buyer_name or ""
                    ).strip()
                    != config.name
                ]

                paid = [
                    int(row.sale)
                    for row in ahbot_buys
                ]

                census_rows.append({
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

                    "unexpected_active_ahbot_seller_rows":
                        len(active_bot),

                    "completed_history_rows":
                        len(completed),

                    "ahbot_player_purchases":
                        len(ahbot_buys),

                    "organic_completed_rows":
                        len(organic_completed),

                    "ahbot_total_gil_paid":
                        sum(paid),

                    "ahbot_minimum_payment":
                        min(paid)
                        if paid
                        else None,

                    "ahbot_maximum_payment":
                        max(paid)
                        if paid
                        else None,

                    "maximum_purchase_per_cycle":
                        1,

                    "rate_semantic":
                        "ONE_ROLL_PER_ITEM_FORM_PER_CYCLE",
                })

    census = pd.DataFrame(
        census_rows
    ).sort_values(
        [
            "itemid",
            "stack",
        ]
    )

    unexpected_bot_rows = int(
        census[
            "unexpected_active_ahbot_seller_rows"
        ].sum()
    )

    if unexpected_bot_rows:
        failures.append(
            "Pilot B items unexpectedly have "
            "active AHBot seller listings."
        )

    ahbot_purchases = int(
        census[
            "ahbot_player_purchases"
        ].sum()
    )

    ahbot_gil_paid = int(
        census[
            "ahbot_total_gil_paid"
        ].sum()
    )

    active_player = int(
        census[
            "active_player_listings"
        ].sum()
    )

    eligible_player = int(
        census[
            "eligible_player_listings"
        ].sum()
    )

    completed_history = int(
        census[
            "completed_history_rows"
        ].sum()
    )

    # A first successful scheduler cycle is enough
    # to close technical post-activation verification.
    # Actual player supply/purchases are observational
    # and are not required for PASS.
    first_cycle_observed = int(
        buyer_cycles_successful >= 1
    )

    closeout_ready = int(
        not failures
        and first_cycle_observed
    )

    if failures:
        status = "FAIL"
        phase_state = "POST_ACTIVATION_BLOCKED"

    elif closeout_ready:
        status = "PASS"
        phase_state = "POST_ACTIVATION_VERIFIED"

    else:
        status = "PASS"
        phase_state = (
            "POST_ACTIVATION_HEALTHY_"
            "PENDING_FIRST_CYCLE"
        )

    stamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    OBS_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    census_file = (
        OBS_ROOT
        / f"{stamp}-census.csv"
    )

    summary_file = (
        OBS_ROOT
        / f"{stamp}-summary.json"
    )

    census.to_csv(
        census_file,
        index=False,
    )

    summary = {
        "status":
            status,

        "phase_state":
            phase_state,

        "observation_utc":
            stamp,

        "service":
            SERVICE,

        "service_active":
            int(
                active_state == "active"
            ),

        "service_main_pid":
            main_pid,

        "service_invocation_id":
            invocation_id,

        "service_active_since":
            active_enter,

        "scheduler_started":
            scheduler_started,

        "buyer_cycles_started":
            buyer_cycles_started,

        "buyer_cycles_successful":
            buyer_cycles_successful,

        "job_exceptions":
            job_exceptions,

        "first_cycle_observed":
            first_cycle_observed,

        "live_buyer_rows":
            len(buyer),

        "live_buyer_sha256":
            buyer_hash,

        "expected_candidate_sha256":
            expected_candidate_hash,

        "seller_runtime_rows":
            len(seller),

        "seller_runtime_sha256":
            seller_hash,

        "rate_gate_hardening_present":
            rate_gate_present,

        "tick_seconds":
            int(config.tick),

        "use_buying_rates":
            bool(
                config.use_buying_rates
            ),

        "pilot_item_count":
            len(live_pilot),

        "active_player_listings":
            active_player,

        "eligible_player_listings":
            eligible_player,

        "completed_history_rows":
            completed_history,

        "pilot_ahbot_player_purchases":
            ahbot_purchases,

        "pilot_ahbot_gil_paid":
            ahbot_gil_paid,

        "unexpected_active_ahbot_seller_rows":
            unexpected_bot_rows,

        "runtime_mutation_performed":
            0,

        "database_mutation_performed":
            0,

        "completed_history_modified":
            0,

        "rollback_required":
            int(bool(failures)),

        "closeout_ready":
            closeout_ready,

        "failures":
            failures,
    }

    summary_file.write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print("=" * 106)
    print(
        " Phase 3C.7 Buyer Pilot B "
        "Post-Activation Verification"
    )
    print("=" * 106)
    print()

    print(
        census.to_string(
            index=False
        )
    )

    print()
    print(
        "Service active:                 ",
        int(
            active_state == "active"
        ),
    )

    print(
        "Service main PID:               ",
        main_pid,
    )

    print(
        "Scheduler started:              ",
        scheduler_started,
    )

    print(
        "Buyer cycles started:           ",
        buyer_cycles_started,
    )

    print(
        "Buyer cycles successful:        ",
        buyer_cycles_successful,
    )

    print(
        "Job exceptions:                 ",
        job_exceptions,
    )

    print()
    print(
        "Live buyer rows:                ",
        len(buyer),
    )

    print(
        "Live runtime matches candidate: ",
        int(
            buyer_hash
            == expected_candidate_hash
        ),
    )

    print(
        "Rate hardening present:         ",
        rate_gate_present,
    )

    print(
        "Buying rates enabled:           ",
        int(
            config.use_buying_rates
        ),
    )

    print()
    print(
        "Active player listings:         ",
        active_player,
    )

    print(
        "Eligible player listings:       ",
        eligible_player,
    )

    print(
        "Completed Pilot B history:      ",
        completed_history,
    )

    print(
        "AHBot Pilot B purchases:        ",
        ahbot_purchases,
    )

    print(
        "Pilot B gil paid:               ",
        ahbot_gil_paid,
    )

    print(
        "Unexpected AHBot seller rows:   ",
        unexpected_bot_rows,
    )

    print()
    print(
        "Runtime mutation performed:     0"
    )

    print(
        "Database mutation performed:    0"
    )

    print(
        "Completed history modified:     0"
    )

    print(
        "Rollback required:              ",
        int(bool(failures)),
    )

    print()
    print(
        "Phase state:",
        phase_state,
    )

    print(
        "Closeout ready:",
        closeout_ready,
    )

    print()
    print(
        "Census:",
        census_file,
    )

    print(
        "Summary:",
        summary_file,
    )

    print()
    print(status)

    if failures:
        raise SystemExit(1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
