from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

OBSERVATION_DIR = (
    ROOT
    / "economy"
    / "runtime-observations"
    / "active-supply"
)

REPORTS = (
    ROOT
    / "economy"
    / "reports"
)

OUTPUT_CSV = (
    REPORTS
    / "supply-pressure-report.csv"
)

SUMMARY_JSON = (
    REPORTS
    / "supply-pressure-summary.json"
)


class SupplyPressureError(RuntimeError):
    pass


def main() -> int:
    snapshot_files = sorted(
        OBSERVATION_DIR.glob(
            "*.csv.gz"
        )
    )

    if not snapshot_files:
        raise SupplyPressureError(
            "No active-supply snapshots found."
        )

    frames = []

    for path in snapshot_files:
        frame = pd.read_csv(
            path
        )

        if (
            "snapshot_id"
            not in frame.columns
        ):
            raise SupplyPressureError(
                f"Invalid snapshot: {path}"
            )

        frames.append(
            frame
        )

    history = pd.concat(
        frames,
        ignore_index=True,
    )

    history[
        "observed_at_utc"
    ] = pd.to_datetime(
        history[
            "observed_at_utc"
        ],
        utc=True,
    )

    if (
        history[
            "unknown_active_listings"
        ].sum()
        != 0
    ):
        raise SupplyPressureError(
            "Unknown active listings exist "
            "in observation history."
        )

    results = []

    grouped = history.groupby(
        [
            "itemid",
            "stack",
        ],
        sort=True,
    )

    for (
        itemid,
        stack,
    ), rows in grouped:
        rows = rows.sort_values(
            "observed_at_utc"
        )

        observations = int(
            len(rows)
        )

        first_seen = (
            rows[
                "observed_at_utc"
            ].min()
        )

        last_seen = (
            rows[
                "observed_at_utc"
            ].max()
        )

        span_hours = (
            (
                last_seen
                - first_seen
            ).total_seconds()
            / 3600
        )

        player_present = (
            rows[
                "player_active_listings"
            ]
            > 0
        )

        player_meets_target = (
            rows[
                "player_supply_meets_target"
            ]
            == 1
        )

        bot_overhang = (
            rows[
                "bot_above_player_complement"
            ]
            == 1
        )

        below_target = (
            rows[
                "target_status"
            ]
            == "BELOW_TARGET"
        )

        at_target = (
            rows[
                "target_status"
            ]
            == "AT_TARGET"
        )

        above_target = (
            rows[
                "target_status"
            ]
            == "ABOVE_TARGET"
        )

        player_present_obs = int(
            player_present.sum()
        )

        player_meets_target_obs = int(
            player_meets_target.sum()
        )

        bot_overhang_obs = int(
            bot_overhang.sum()
        )

        player_presence_ratio = (
            player_present_obs
            / observations
        )

        player_meets_target_ratio = (
            player_meets_target_obs
            / observations
        )

        bot_overhang_ratio = (
            bot_overhang_obs
            / observations
        )

        sufficient_baseline = (
            observations >= 3
            and span_hours >= 6
        )

        persistence_ready = (
            observations >= 6
            and span_hours >= 24
        )

        if not sufficient_baseline:
            pressure_state = (
                "BASELINE_ONLY"
            )

        elif player_present_obs == 0:
            pressure_state = (
                "NO_PLAYER_PRESSURE"
            )

        elif player_presence_ratio >= 0.50:
            pressure_state = (
                "SUSTAINED_PLAYER_SUPPLY"
            )

        else:
            pressure_state = (
                "TRANSIENT_PLAYER_SUPPLY"
            )

        player_target_persistent = int(
            persistence_ready
            and (
                player_meets_target_ratio
                >= 0.50
            )
        )

        bot_overhang_persistent = int(
            persistence_ready
            and (
                bot_overhang_ratio
                >= 0.50
            )
        )

        results.append({
            "itemid":
                int(itemid),

            "stack":
                int(stack),

            "name":
                str(
                    rows.iloc[-1][
                        "name"
                    ]
                ),

            "configured_market_target":
                int(
                    rows.iloc[-1][
                        "configured_market_target"
                    ]
                ),

            "observations":
                observations,

            "first_observed_at_utc":
                first_seen.isoformat(),

            "last_observed_at_utc":
                last_seen.isoformat(),

            "observation_span_hours":
                round(
                    span_hours,
                    3,
                ),

            "player_present_observations":
                player_present_obs,

            "player_presence_ratio":
                round(
                    player_presence_ratio,
                    6,
                ),

            "player_meets_target_observations":
                player_meets_target_obs,

            "player_meets_target_ratio":
                round(
                    player_meets_target_ratio,
                    6,
                ),

            "bot_overhang_observations":
                bot_overhang_obs,

            "bot_overhang_ratio":
                round(
                    bot_overhang_ratio,
                    6,
                ),

            "below_target_observations":
                int(
                    below_target.sum()
                ),

            "at_target_observations":
                int(
                    at_target.sum()
                ),

            "above_target_observations":
                int(
                    above_target.sum()
                ),

            "average_player_listings":
                round(
                    float(
                        rows[
                            "player_active_listings"
                        ].mean()
                    ),
                    4,
                ),

            "max_player_listings":
                int(
                    rows[
                        "player_active_listings"
                    ].max()
                ),

            "average_ahbot_listings":
                round(
                    float(
                        rows[
                            "ahbot_active_listings"
                        ].mean()
                    ),
                    4,
                ),

            "min_ahbot_listings":
                int(
                    rows[
                        "ahbot_active_listings"
                    ].min()
                ),

            "max_ahbot_listings":
                int(
                    rows[
                        "ahbot_active_listings"
                    ].max()
                ),

            "pressure_state":
                pressure_state,

            "sufficient_baseline":
                int(
                    sufficient_baseline
                ),

            "persistence_ready":
                int(
                    persistence_ready
                ),

            "player_target_persistent":
                player_target_persistent,

            "bot_overhang_persistent":
                bot_overhang_persistent,

            # Observation only.
            "stock_change_authorized":
                0,

            "activation_ready":
                0,

            "auto_live_promotion":
                0,
        })

    report = pd.DataFrame(
        results
    )

    pressure_states = {
        state: int(
            (
                report[
                    "pressure_state"
                ]
                == state
            ).sum()
        )
        for state in (
            "BASELINE_ONLY",
            "NO_PLAYER_PRESSURE",
            "TRANSIENT_PLAYER_SUPPLY",
            "SUSTAINED_PLAYER_SUPPLY",
        )
    }

    integrity_failures = 0

    if (
        report[
            "stock_change_authorized"
        ]
        != 0
    ).any():
        integrity_failures += 1

    if (
        report[
            "activation_ready"
        ]
        != 0
    ).any():
        integrity_failures += 1

    if (
        report[
            "auto_live_promotion"
        ]
        != 0
    ).any():
        integrity_failures += 1

    summary = {
        "status":
            (
                "PASS"
                if integrity_failures == 0
                else "FAIL"
            ),

        "stage":
            "2B.2_SUPPLY_PRESSURE_PERSISTENCE",

        "snapshots":
            int(
                history[
                    "snapshot_id"
                ].nunique()
            ),

        "item_forms_evaluated":
            int(
                len(report)
            ),

        "pressure_states":
            pressure_states,

        "persistence_ready_forms":
            int(
                report[
                    "persistence_ready"
                ].sum()
            ),

        "player_target_persistent_forms":
            int(
                report[
                    "player_target_persistent"
                ].sum()
            ),

        "bot_overhang_persistent_forms":
            int(
                report[
                    "bot_overhang_persistent"
                ].sum()
            ),

        "integrity_failures":
            integrity_failures,

        "stock_change_authorized":
            0,

        "activation_ready":
            0,

        "auto_live_promotions":
            0,

        "generated_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),
    }

    REPORTS.mkdir(
        parents=True,
        exist_ok=True,
    )

    report.to_csv(
        OUTPUT_CSV,
        index=False,
    )

    SUMMARY_JSON.write_text(
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
    print(
        " Phase 2B.2 Supply Pressure / "
        "Persistence"
    )
    print("=" * 84)

    print()
    print(
        f"Snapshots:                       "
        f"{summary['snapshots']:>6}"
    )

    print(
        f"Item forms evaluated:            "
        f"{len(report):>6}"
    )

    print()
    print("Pressure states:")

    for (
        state,
        count,
    ) in pressure_states.items():
        print(
            f"  {state:<28}"
            f"{count:>6}"
        )

    print()
    print(
        f"Persistence-ready forms:         "
        f"{summary['persistence_ready_forms']:>6}"
    )

    print(
        f"Player-target persistent:        "
        f"{summary['player_target_persistent_forms']:>6}"
    )

    print(
        f"Bot-overhang persistent:         "
        f"{summary['bot_overhang_persistent_forms']:>6}"
    )

    print()
    print(
        f"Integrity failures:              "
        f"{integrity_failures:>6}"
    )

    print()
    print(
        "Stock change authorized:             0"
    )

    print(
        "Activation ready:                    0"
    )

    print(
        "Auto live promotions:                0"
    )

    print()
    print(
        f"Report:  {OUTPUT_CSV}"
    )

    print(
        f"Summary: {SUMMARY_JSON}"
    )

    print()

    if integrity_failures:
        print("FAIL")
        raise SupplyPressureError(
            "Supply-pressure analysis failed."
        )

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
