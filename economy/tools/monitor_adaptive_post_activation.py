from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path.home() / "ffxiahbot"

POLICY_ROOT = (
    ROOT
    / "economy"
    / "policy"
)

GENERATED = (
    ROOT
    / "economy"
    / "generated"
)

OBS_ROOT = (
    ROOT
    / "economy"
    / "runtime-observations"
)

ADAPTIVE_ROOT = (
    OBS_ROOT
    / "adaptive"
)

ACTIVATION_ROOT = (
    ADAPTIVE_ROOT
    / "activation"
)

MONITOR_ROOT = (
    ADAPTIVE_ROOT
    / "post-activation"
)

CONTRACT_FILE = (
    POLICY_ROOT
    / "adaptive-control-contract.json"
)

SELLER_RUNTIME = (
    GENERATED
    / "market-sell.csv"
)

BUYER_RUNTIME = (
    GENERATED
    / "market-buy.csv"
)

SELLER_SERVICE = (
    "ffxiahbot-seller.service"
)

BUYER_SERVICE = (
    "ffxiahbot-buyer.service"
)


class MonitorError(RuntimeError):
    pass


def as_int(value: Any) -> int:
    if value is None:
        return 0

    try:
        return int(float(value))
    except (
        TypeError,
        ValueError,
    ):
        return 0


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


def load_json(path: Path) -> dict:
    return json.loads(
        path.read_text(
            encoding="utf-8",
        )
    )


def latest_activation_gate() -> tuple[Path, dict]:
    paths = sorted(
        ACTIVATION_ROOT.glob(
            "*-activation-gate.json"
        )
    )

    if not paths:
        raise MonitorError(
            "No 3F.7 activation gate found."
        )

    path = paths[-1]
    data = load_json(path)

    if (
        data.get("phase") != "3F.7"
        or data.get("status") != "PASS"
    ):
        raise MonitorError(
            "Latest 3F.7 activation gate "
            "is not PASS."
        )

    return path, data


def service_active(
    service: str,
) -> int:
    result = subprocess.run(
        [
            "systemctl",
            "is-active",
            service,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    return int(
        result.returncode == 0
        and result.stdout.strip()
        == "active"
    )


def require_zero_authority(
    label: str,
    data: dict,
) -> None:
    keys = [
        "price_change_authorized",
        "rate_change_authorized",
        "stock_change_authorized",
        "catalog_change_authorized",
        "runtime_mutation_authorized",
        "database_mutation_authorized",
        "adaptive_mutation_authorized",
        "auto_live_promotion",
    ]

    bad = []

    for key in keys:
        if (
            key in data
            and as_int(
                data.get(key)
            ) != 0
        ):
            bad.append(key)

    if bad:
        raise MonitorError(
            f"{label} grants unexpected "
            "authority: "
            + ", ".join(bad)
        )


def main() -> int:
    for path in [
        CONTRACT_FILE,
        SELLER_RUNTIME,
        BUYER_RUNTIME,
    ]:
        if not path.exists():
            raise MonitorError(
                f"Missing required input: {path}"
            )

    contract = load_json(
        CONTRACT_FILE
    )

    if contract.get("status") != "PASS":
        raise MonitorError(
            "3F.1 contract is not PASS."
        )

    require_zero_authority(
        "3F.1 contract",
        contract,
    )

    gate_path, gate = (
        latest_activation_gate()
    )

    require_zero_authority(
        "3F.7 activation gate",
        gate,
    )

    activation_state = str(
        gate.get(
            "activation_state",
            "",
        )
    )

    controlled_activation_allowed = as_int(
        gate.get(
            "controlled_activation_allowed"
        )
    )

    runtime_publish_performed = as_int(
        gate.get(
            "runtime_publish_performed"
        )
    )

    db_mutation_performed = as_int(
        gate.get(
            "database_mutation_performed"
        )
    )

    service_restart_performed = as_int(
        gate.get(
            "service_restart_performed"
        )
    )

    live_seller_sha = sha256(
        SELLER_RUNTIME
    )

    live_buyer_sha = sha256(
        BUYER_RUNTIME
    )

    gate_seller_sha = str(
        gate.get(
            "seller_runtime_sha256",
            "",
        )
    )

    gate_buyer_sha = str(
        gate.get(
            "buyer_runtime_sha256",
            "",
        )
    )

    seller_runtime_matches_gate = int(
        bool(gate_seller_sha)
        and live_seller_sha
        == gate_seller_sha
    )

    buyer_runtime_matches_gate = int(
        bool(gate_buyer_sha)
        and live_buyer_sha
        == gate_buyer_sha
    )

    seller_service_active = (
        service_active(
            SELLER_SERVICE
        )
    )

    buyer_service_active = (
        service_active(
            BUYER_SERVICE
        )
    )

    failures = []

    if not seller_service_active:
        failures.append(
            "Seller service is not active."
        )

    if not buyer_service_active:
        failures.append(
            "Buyer service is not active."
        )

    # --------------------------------------------------
    # Current dormant state
    # --------------------------------------------------

    if (
        activation_state.startswith(
            "ACTIVATION_LOCKED"
        )
        and runtime_publish_performed == 0
        and db_mutation_performed == 0
    ):
        monitoring_state = (
            "NO_ACTIVATION_TO_MONITOR"
        )

        live_activation_observed = 0
        post_activation_exercised = 0

        if not seller_runtime_matches_gate:
            failures.append(
                "Seller runtime changed after "
                "locked 3F.7 gate."
            )

        if not buyer_runtime_matches_gate:
            failures.append(
                "Buyer runtime changed after "
                "locked 3F.7 gate."
            )

    elif (
        activation_state
        == "CONTROLLED_ACTIVATION_PRECHECK_READY"
        and runtime_publish_performed == 0
    ):
        monitoring_state = (
            "AWAITING_CONTROLLED_ACTIVATION"
        )

        live_activation_observed = 0
        post_activation_exercised = 0

    elif runtime_publish_performed == 1:
        live_activation_observed = 1
        post_activation_exercised = 1

        # A future real activation path must record
        # expected post-publish runtime hashes in
        # the activation gate. Until then, fail
        # closed rather than guessing.
        if not gate_seller_sha:
            failures.append(
                "Activated state lacks seller "
                "runtime hash."
            )

        if not gate_buyer_sha:
            failures.append(
                "Activated state lacks buyer "
                "runtime hash."
            )

        if (
            gate_seller_sha
            and not seller_runtime_matches_gate
        ):
            failures.append(
                "Seller runtime differs from "
                "activated runtime hash."
            )

        if (
            gate_buyer_sha
            and not buyer_runtime_matches_gate
        ):
            failures.append(
                "Buyer runtime differs from "
                "activated runtime hash."
            )

        if failures:
            monitoring_state = (
                "POST_ACTIVATION_HEALTH_FAILURE"
            )
        else:
            monitoring_state = (
                "POST_ACTIVATION_BASELINE_HEALTHY"
            )

    else:
        monitoring_state = (
            "UNKNOWN_ACTIVATION_STATE"
        )

        live_activation_observed = 0
        post_activation_exercised = 0

        failures.append(
            "Unable to classify activation state."
        )

    monitoring_harness_ready = int(
        monitoring_state
        in {
            "NO_ACTIVATION_TO_MONITOR",
            "AWAITING_CONTROLLED_ACTIVATION",
            "POST_ACTIVATION_BASELINE_HEALTHY",
        }
        and not failures
    )

    engineering_ready_for_closeout = int(
        monitoring_harness_ready
        and monitoring_state
        == "NO_ACTIVATION_TO_MONITOR"
    )

    now = datetime.now(
        timezone.utc
    )

    MONITOR_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    output = (
        MONITOR_ROOT
        / (
            now.strftime(
                "%Y%m%dT%H%M%SZ"
            )
            + "-monitor-summary.json"
        )
    )

    summary = {
        "status":
            (
                "PASS"
                if not failures
                else "FAIL"
            ),

        "phase":
            "3F.8",

        "observation_utc":
            now.isoformat(),

        "monitoring_state":
            monitoring_state,

        "monitoring_harness_ready":
            monitoring_harness_ready,

        "engineering_ready_for_closeout":
            engineering_ready_for_closeout,

        "activation_state":
            activation_state,

        "controlled_activation_allowed":
            controlled_activation_allowed,

        "live_activation_observed":
            live_activation_observed,

        "post_activation_exercised":
            post_activation_exercised,

        "runtime_publish_performed":
            runtime_publish_performed,

        "database_mutation_performed":
            db_mutation_performed,

        "service_restart_performed":
            service_restart_performed,

        "seller_service_active":
            seller_service_active,

        "buyer_service_active":
            buyer_service_active,

        "seller_runtime_matches_3f7":
            seller_runtime_matches_gate,

        "buyer_runtime_matches_3f7":
            buyer_runtime_matches_gate,

        "seller_runtime_sha256":
            live_seller_sha,

        "buyer_runtime_sha256":
            live_buyer_sha,

        "interpretation_contract": {
            "no_activation_means_post_activation_"
            "health_claimed":
                False,

            "monitoring_harness_ready_means_"
            "activation_authorized":
                False,

            "post_activation_exercised_requires_"
            "real_activation":
                True,
        },

        "price_change_authorized":
            0,

        "rate_change_authorized":
            0,

        "stock_change_authorized":
            0,

        "catalog_change_authorized":
            0,

        "runtime_mutation_authorized":
            0,

        "database_mutation_authorized":
            0,

        "auto_live_promotion":
            0,

        "sources": {
            "contract":
                str(
                    CONTRACT_FILE
                ),

            "activation_gate":
                str(
                    gate_path
                ),
        },

        "failures":
            failures,
    }

    output.write_text(
        json.dumps(
            summary,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print("=" * 112)
    print(
        " Phase 3F.8 Adaptive "
        "Post-Activation Monitoring"
    )
    print("=" * 112)
    print()

    print(
        "Activation state:                 ",
        activation_state,
    )

    print(
        "Controlled activation allowed:    ",
        controlled_activation_allowed,
    )

    print()
    print(
        "Live activation observed:         ",
        live_activation_observed,
    )

    print(
        "Post-activation exercised:        ",
        post_activation_exercised,
    )

    print()
    print(
        "Seller service active:            ",
        seller_service_active,
    )

    print(
        "Buyer service active:             ",
        buyer_service_active,
    )

    print()
    print(
        "Seller runtime matches 3F.7:      ",
        seller_runtime_matches_gate,
    )

    print(
        "Buyer runtime matches 3F.7:       ",
        buyer_runtime_matches_gate,
    )

    print()
    print(
        "Monitoring harness ready:         ",
        monitoring_harness_ready,
    )

    print(
        "Engineering ready for closeout:   ",
        engineering_ready_for_closeout,
    )

    print()
    print(
        "Monitoring state:",
        monitoring_state,
    )

    print()
    print(
        "Runtime mutation authorized:      0"
    )

    print(
        "Database mutation authorized:     0"
    )

    print(
        "Auto live promotion:              0"
    )

    print()
    print(
        "Summary:",
        output,
    )

    print()
    print(summary["status"])

    if failures:
        print()
        print("Failures:")

        for failure in failures:
            print(
                " -",
                failure,
            )

        raise SystemExit(1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
