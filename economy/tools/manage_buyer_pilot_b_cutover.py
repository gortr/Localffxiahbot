from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"
GENERATED = ROOT / "economy" / "generated"
REPORTS = ROOT / "economy" / "reports"

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

BACKUP_ROOT = (
    ROOT
    / "economy"
    / "runtime-backups"
)

SERVICE = "ffxiahbot-buyer.service"

EXPECTED_PRE_ROWS = 152
EXPECTED_PILOT_ROWS = 155

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


class CutoverError(RuntimeError):
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


def load_validation() -> dict[str, Any]:
    if not VALIDATION_SUMMARY.exists():
        raise CutoverError(
            "Missing 3C.4 validation summary."
        )

    data = json.loads(
        VALIDATION_SUMMARY.read_text(
            encoding="utf-8",
        )
    )

    if data.get("status") != "PASS":
        raise CutoverError(
            "3C.4 validation is not PASS."
        )

    if int(
        data.get(
            "integrity_failures",
            1,
        )
    ) != 0:
        raise CutoverError(
            "3C.4 validation has "
            "integrity failures."
        )

    if int(
        data.get(
            "rate_gate_hardening_present",
            0,
        )
    ) != 1:
        raise CutoverError(
            "Buyer rate-gate hardening "
            "was not validated."
        )

    return data


def validate_shape(
    path: Path,
    expected_rows: int,
) -> None:
    frame = pd.read_csv(
        path,
        low_memory=False,
    )

    if list(frame.columns) != EXPECTED_COLUMNS:
        raise CutoverError(
            f"Unexpected schema: {path}"
        )

    if len(frame) != expected_rows:
        raise CutoverError(
            f"{path.name}: expected "
            f"{expected_rows} rows, "
            f"found {len(frame)}."
        )

    if frame.duplicated(
        subset=["itemid"]
    ).any():
        raise CutoverError(
            f"{path.name} contains "
            "duplicate itemids."
        )


def verify_inputs(
    require_pre_cutover_runtime: bool,
) -> dict[str, Any]:
    for path in [
        BUYER_RUNTIME,
        SELLER_RUNTIME,
        CANDIDATE,
        VALIDATION_SUMMARY,
    ]:
        if not path.exists():
            raise CutoverError(
                f"Missing required input: {path}"
            )

    summary = load_validation()

    candidate_hash = sha256(
        CANDIDATE
    )

    expected_candidate = str(
        summary.get(
            "candidate_sha256",
            "",
        )
    )

    if (
        not expected_candidate
        or candidate_hash
        != expected_candidate
    ):
        raise CutoverError(
            "Candidate hash no longer "
            "matches validated 3C.4 input."
        )

    validate_shape(
        CANDIDATE,
        EXPECTED_PILOT_ROWS,
    )

    buyer_hash = sha256(
        BUYER_RUNTIME
    )

    seller_hash = sha256(
        SELLER_RUNTIME
    )

    expected_pre_buyer = str(
        summary.get(
            "buyer_runtime_sha256",
            "",
        )
    )

    expected_seller = str(
        summary.get(
            "seller_runtime_sha256",
            "",
        )
    )

    if seller_hash != expected_seller:
        raise CutoverError(
            "Seller runtime changed "
            "since 3C.4."
        )

    if require_pre_cutover_runtime:
        if buyer_hash != expected_pre_buyer:
            raise CutoverError(
                "Buyer runtime changed since "
                "3C.4 or Pilot B is already "
                "published."
            )

        validate_shape(
            BUYER_RUNTIME,
            EXPECTED_PRE_ROWS,
        )

    return {
        "summary":
            summary,

        "candidate_hash":
            candidate_hash,

        "buyer_hash":
            buyer_hash,

        "seller_hash":
            seller_hash,

        "expected_pre_buyer":
            expected_pre_buyer,

        "expected_seller":
            expected_seller,
    }


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


def restart_service() -> None:
    subprocess.run(
        [
            "sudo",
            "systemctl",
            "restart",
            SERVICE,
        ],
        check=True,
    )

    if not service_is_active():
        raise CutoverError(
            f"{SERVICE} is not active "
            "after restart."
        )


def atomic_copy(
    source: Path,
    destination: Path,
) -> None:
    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=destination.parent,
        prefix=destination.name + ".tmp.",
        delete=False,
    ) as handle:
        temp_path = Path(
            handle.name
        )

        with source.open("rb") as src:
            shutil.copyfileobj(
                src,
                handle,
            )

        handle.flush()
        os.fsync(
            handle.fileno()
        )

    try:
        os.replace(
            temp_path,
            destination,
        )
    finally:
        if temp_path.exists():
            temp_path.unlink()


def make_backup() -> Path:
    stamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    backup_dir = (
        BACKUP_ROOT
        / f"buyer-pilot-b-{stamp}"
    )

    backup_dir.mkdir(
        parents=True,
        exist_ok=False,
    )

    shutil.copy2(
        BUYER_RUNTIME,
        backup_dir
        / BUYER_RUNTIME.name,
    )

    manifest = {
        "created_utc":
            stamp,

        "source":
            str(BUYER_RUNTIME),

        "buyer_runtime_sha256":
            sha256(
                BUYER_RUNTIME
            ),

        "completed_sale_history_modified":
            False,

        "database_rows_modified":
            False,
    }

    (
        backup_dir
        / "manifest.json"
    ).write_text(
        json.dumps(
            manifest,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    return backup_dir


def dry_run() -> int:
    data = verify_inputs(
        require_pre_cutover_runtime=False,
    )

    current = data[
        "buyer_hash"
    ]

    pre_hash = data[
        "expected_pre_buyer"
    ]

    candidate_hash = data[
        "candidate_hash"
    ]

    if current == pre_hash:
        state = "PRE_CUTOVER_READY"

        validate_shape(
            BUYER_RUNTIME,
            EXPECTED_PRE_ROWS,
        )

    elif current == candidate_hash:
        state = (
            "PILOT_B_ALREADY_PUBLISHED"
        )

        validate_shape(
            BUYER_RUNTIME,
            EXPECTED_PILOT_ROWS,
        )

    else:
        raise CutoverError(
            "Live buyer runtime matches "
            "neither validated pre-cutover "
            "runtime nor validated Pilot B "
            "candidate."
        )

    print()
    print("=" * 88)
    print(
        " Phase 3C.4 Pilot B "
        "Cutover Dry Run"
    )
    print("=" * 88)
    print()

    print(
        "State:                       ",
        state,
    )

    print(
        "Buyer service active:        ",
        int(
            service_is_active()
        ),
    )

    print()
    print(
        "Validated pre-cutover SHA:"
    )
    print(pre_hash)

    print()
    print(
        "Current buyer SHA:"
    )
    print(current)

    print()
    print(
        "Validated candidate SHA:"
    )
    print(candidate_hash)

    print()
    print(
        "Seller runtime unchanged:     1"
    )

    print(
        "Candidate integrity:          1"
    )

    print(
        "Database mutation required:   0"
    )

    print(
        "Completed history touched:    0"
    )

    print(
        "Apply executed:               0"
    )

    print()
    print("PASS")

    return 0


def apply() -> int:
    data = verify_inputs(
        require_pre_cutover_runtime=True,
    )

    backup_dir = make_backup()

    try:
        atomic_copy(
            CANDIDATE,
            BUYER_RUNTIME,
        )

        published_hash = sha256(
            BUYER_RUNTIME
        )

        if (
            published_hash
            != data["candidate_hash"]
        ):
            raise CutoverError(
                "Published buyer runtime "
                "hash mismatch."
            )

        validate_shape(
            BUYER_RUNTIME,
            EXPECTED_PILOT_ROWS,
        )

        restart_service()

    except Exception:
        backup_file = (
            backup_dir
            / BUYER_RUNTIME.name
        )

        if backup_file.exists():
            atomic_copy(
                backup_file,
                BUYER_RUNTIME,
            )

            try:
                restart_service()
            except Exception:
                pass

        raise

    print()
    print("=" * 88)
    print(
        " Phase 3C.6 Pilot B "
        "Controlled Activation"
    )
    print("=" * 88)
    print()

    print(
        "Backup:",
        backup_dir,
    )

    print(
        "Buyer rows:",
        EXPECTED_PILOT_ROWS,
    )

    print(
        "Buyer SHA256:"
    )
    print(
        sha256(
            BUYER_RUNTIME
        )
    )

    print(
        "Buyer service active:",
        int(
            service_is_active()
        ),
    )

    print()
    print(
        "Database rows modified:       0"
    )

    print(
        "Completed history modified:   0"
    )

    print()
    print("PASS")

    return 0


def rollback(
    backup: str,
) -> int:
    backup_dir = Path(
        backup
    ).expanduser().resolve()

    backup_file = (
        backup_dir
        / BUYER_RUNTIME.name
    )

    manifest_file = (
        backup_dir
        / "manifest.json"
    )

    if not backup_file.exists():
        raise CutoverError(
            "Backup buyer runtime "
            "does not exist."
        )

    if not manifest_file.exists():
        raise CutoverError(
            "Backup manifest missing."
        )

    manifest = json.loads(
        manifest_file.read_text(
            encoding="utf-8",
        )
    )

    expected_hash = str(
        manifest.get(
            "buyer_runtime_sha256",
            "",
        )
    )

    if (
        not expected_hash
        or sha256(
            backup_file
        )
        != expected_hash
    ):
        raise CutoverError(
            "Backup hash validation failed."
        )

    # Configuration rollback only.
    #
    # No auction_house rows are deleted or
    # rewritten. Completed sale history and any
    # legitimate Pilot B transactions remain.
    atomic_copy(
        backup_file,
        BUYER_RUNTIME,
    )

    validate_shape(
        BUYER_RUNTIME,
        EXPECTED_PRE_ROWS,
    )

    restart_service()

    print()
    print("=" * 88)
    print(
        " Phase 3C Pilot B "
        "Configuration Rollback"
    )
    print("=" * 88)
    print()

    print(
        "Restored from:",
        backup_dir,
    )

    print(
        "Buyer rows:",
        EXPECTED_PRE_ROWS,
    )

    print(
        "Buyer SHA256:"
    )
    print(
        sha256(
            BUYER_RUNTIME
        )
    )

    print()
    print(
        "Auction DB rows deleted:      0"
    )

    print(
        "Completed history modified:   0"
    )

    print(
        "Historical Pilot B sales "
        "preserved:                   1"
    )

    print()
    print("PASS")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser()

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    sub.add_parser(
        "dry-run"
    )

    sub.add_parser(
        "apply"
    )

    rollback_parser = sub.add_parser(
        "rollback"
    )

    rollback_parser.add_argument(
        "--backup",
        required=True,
        help=(
            "Path to buyer Pilot B "
            "runtime-backup directory."
        ),
    )

    args = parser.parse_args()

    if args.command == "dry-run":
        return dry_run()

    if args.command == "apply":
        return apply()

    if args.command == "rollback":
        return rollback(
            args.backup
        )

    raise CutoverError(
        "Unknown command."
    )


if __name__ == "__main__":
    raise SystemExit(main())
