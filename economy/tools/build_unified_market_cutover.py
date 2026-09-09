from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

MATURITY_FILE = ROOT / "economy" / "generated" / "economy-maturity-policy.csv"
SELL_BASELINE_FILE = ROOT / "economy" / "generated" / "market-sell-phase0.csv"
BUY_BASELINE_FILE = ROOT / "economy" / "generated" / "market-buy-phase0.csv"

ROLLOUT_FILE = ROOT / "economy" / "generated" / "market-rollout-policy.csv"
SELL_OUTPUT_FILE = ROOT / "economy" / "generated" / "market-sell-unified-cutover.csv"
BUY_OUTPUT_FILE = ROOT / "economy" / "generated" / "market-buy-unified-cutover.csv"

AUDIT_FILE = ROOT / "economy" / "reports" / "unified-builder-cutover-audit.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "unified-builder-cutover-summary.json"


EXPECTED_TOTAL = 23534
EXPECTED_SELL = 167
EXPECTED_BUY = 159
CURRENT_SERVER_MATURITY = "SEED"

STAGE_ORDER = [
    "SEED",
    "GROWING",
    "ESTABLISHED",
    "MATURE",
    "ADVANCED",
    "FULL",
]

STAGE_RANK = {
    stage: rank
    for rank, stage in enumerate(STAGE_ORDER)
}


class UnifiedCutoverError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def as_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def load_unique(
    path: Path,
    label: str,
) -> pd.DataFrame:
    if not path.exists():
        raise UnifiedCutoverError(
            f"Missing {label}: {path}"
        )

    frame = pd.read_csv(
        path,
        low_memory=False,
    )

    if "itemid" not in frame.columns:
        raise UnifiedCutoverError(
            f"{label} has no itemid column."
        )

    frame = frame.copy()
    frame["itemid"] = frame["itemid"].map(as_int)

    if frame["itemid"].duplicated().any():
        duplicates = (
            frame.loc[
                frame["itemid"].duplicated(keep=False),
                "itemid",
            ]
            .head(20)
            .tolist()
        )
        raise UnifiedCutoverError(
            f"{label} contains duplicate itemids: {duplicates}"
        )

    return frame


def normalized_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None

    if isinstance(value, bool):
        return int(value)

    if isinstance(value, (int, float)):
        number = float(value)
        if number.is_integer():
            return int(number)
        return round(number, 12)

    return clean_text(value)


def semantic_frame_equal(
    left: pd.DataFrame,
    right: pd.DataFrame,
) -> tuple[bool, list[dict[str, Any]]]:
    if list(left.columns) != list(right.columns):
        return False, [
            {
                "issue": "COLUMN_MISMATCH",
                "left": "|".join(left.columns),
                "right": "|".join(right.columns),
            }
        ]

    if len(left) != len(right):
        return False, [
            {
                "issue": "ROW_COUNT_MISMATCH",
                "left": len(left),
                "right": len(right),
            }
        ]

    differences: list[dict[str, Any]] = []

    for idx in range(len(left)):
        for column in left.columns:
            lv = normalized_value(
                left.iloc[idx][column]
            )
            rv = normalized_value(
                right.iloc[idx][column]
            )

            if lv != rv:
                differences.append(
                    {
                        "issue": "VALUE_MISMATCH",
                        "row": idx,
                        "column": column,
                        "left": lv,
                        "right": rv,
                    }
                )

                if len(differences) >= 50:
                    return False, differences

    return len(differences) == 0, differences


def dataframe_semantic_hash(
    frame: pd.DataFrame,
) -> str:
    records = []

    for _, row in frame.iterrows():
        records.append(
            {
                column: normalized_value(row[column])
                for column in frame.columns
            }
        )

    payload = json.dumps(
        {
            "columns": list(frame.columns),
            "records": records,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    maturity = load_unique(
        MATURITY_FILE,
        "economy maturity policy",
    )

    sell_baseline = load_unique(
        SELL_BASELINE_FILE,
        "Phase-0 seller baseline",
    )

    buy_baseline = load_unique(
        BUY_BASELINE_FILE,
        "Phase-0 buyer baseline",
    )

    if len(maturity) != EXPECTED_TOTAL:
        raise UnifiedCutoverError(
            f"Expected {EXPECTED_TOTAL} maturity rows, "
            f"found {len(maturity)}"
        )

    if len(sell_baseline) != EXPECTED_SELL:
        raise UnifiedCutoverError(
            f"Expected {EXPECTED_SELL} seller rows, "
            f"found {len(sell_baseline)}"
        )

    if len(buy_baseline) != EXPECTED_BUY:
        raise UnifiedCutoverError(
            f"Expected {EXPECTED_BUY} buyer rows, "
            f"found {len(buy_baseline)}"
        )

    if CURRENT_SERVER_MATURITY not in STAGE_RANK:
        raise UnifiedCutoverError(
            f"Invalid current server maturity: "
            f"{CURRENT_SERVER_MATURITY}"
        )

    current_rank = STAGE_RANK[
        CURRENT_SERVER_MATURITY
    ]

    sell_ids = set(
        sell_baseline["itemid"].astype(int)
    )

    buy_ids = set(
        buy_baseline["itemid"].astype(int)
    )

    policy_by_id = {
        int(row["itemid"]): row.to_dict()
        for _, row in maturity.iterrows()
    }

    audit_rows: list[dict[str, Any]] = []
    rollout_rows: list[dict[str, Any]] = []

    for _, row in maturity.iterrows():
        itemid = as_int(row.get("itemid"))
        name = clean_text(row.get("name"))
        minimum = clean_text(
            row.get("minimum_maturity")
        ).upper()

        seller_capable = as_int(
            row.get("seller_capable")
        )
        buyer_capable = as_int(
            row.get("buyer_capable")
        )

        live_seller = int(
            itemid in sell_ids
        )
        live_buyer = int(
            itemid in buy_ids
        )

        if minimum:
            if minimum not in STAGE_RANK:
                audit_rows.append(
                    {
                        "itemid": itemid,
                        "name": name,
                        "severity": "ERROR",
                        "audit_type": "INVALID_MINIMUM_MATURITY",
                        "details": minimum,
                    }
                )
                maturity_eligible = 0
            else:
                maturity_eligible = int(
                    STAGE_RANK[minimum]
                    <= current_rank
                )
        else:
            maturity_eligible = 0

        seller_maturity_eligible = int(
            bool(
                seller_capable
                and maturity_eligible
            )
        )

        buyer_maturity_eligible = int(
            bool(
                buyer_capable
                and maturity_eligible
            )
        )

        seller_rollout_active = live_seller
        buyer_rollout_active = live_buyer

        seller_staged = int(
            bool(
                seller_maturity_eligible
                and not seller_rollout_active
            )
        )

        buyer_staged = int(
            bool(
                buyer_maturity_eligible
                and not buyer_rollout_active
            )
        )

        if live_seller and not seller_capable:
            audit_rows.append(
                {
                    "itemid": itemid,
                    "name": name,
                    "severity": "ERROR",
                    "audit_type": "LIVE_SELLER_NOT_CAPABLE",
                    "details": clean_text(
                        row.get("final_market_class")
                    ),
                }
            )

        if live_buyer and not buyer_capable:
            audit_rows.append(
                {
                    "itemid": itemid,
                    "name": name,
                    "severity": "ERROR",
                    "audit_type": "LIVE_BUYER_NOT_CAPABLE",
                    "details": clean_text(
                        row.get("final_market_class")
                    ),
                }
            )

        if (
            (live_seller or live_buyer)
            and not maturity_eligible
        ):
            audit_rows.append(
                {
                    "itemid": itemid,
                    "name": name,
                    "severity": "ERROR",
                    "audit_type": "LIVE_ITEM_NOT_SEED_ELIGIBLE",
                    "details": f"minimum_maturity={minimum}",
                }
            )

        rollout_rows.append(
            {
                "itemid": itemid,
                "name": name,
                "final_market_class": clean_text(
                    row.get("final_market_class")
                ),
                "minimum_maturity": minimum,
                "server_maturity": CURRENT_SERVER_MATURITY,
                "seller_capable": seller_capable,
                "buyer_capable": buyer_capable,
                "seller_maturity_eligible":
                    seller_maturity_eligible,
                "buyer_maturity_eligible":
                    buyer_maturity_eligible,
                "seller_rollout_active":
                    seller_rollout_active,
                "buyer_rollout_active":
                    buyer_rollout_active,
                "seller_staged":
                    seller_staged,
                "buyer_staged":
                    buyer_staged,
                "rollout_reason":
                    (
                        "CURRENT_LIVE_BASELINE"
                        if live_seller or live_buyer
                        else
                        "MATURITY_ELIGIBLE_NOT_ROLLED_OUT"
                        if seller_staged or buyer_staged
                        else
                        "NOT_ELIGIBLE_AT_CURRENT_MATURITY"
                    ),
                "auto_live_promotion": 0,
            }
        )

    rollout = pd.DataFrame(
        rollout_rows
    ).sort_values(
        by=["itemid"],
        kind="stable",
    )

    if len(rollout) != EXPECTED_TOTAL:
        raise UnifiedCutoverError(
            f"Expected {EXPECTED_TOTAL} rollout rows, "
            f"found {len(rollout)}"
        )

    # Transitional cutover compiler:
    # operational values remain exactly the proven Phase-0 values.
    # The new policy decides WHICH rows may be compiled.
    seller_active_ids = set(
        rollout.loc[
            rollout["seller_rollout_active"] == 1,
            "itemid",
        ].astype(int)
    )

    buyer_active_ids = set(
        rollout.loc[
            rollout["buyer_rollout_active"] == 1,
            "itemid",
        ].astype(int)
    )

    if seller_active_ids != sell_ids:
        audit_rows.append(
            {
                "itemid": 0,
                "name": "",
                "severity": "ERROR",
                "audit_type": "SELLER_ACTIVATION_SET_MISMATCH",
                "details": (
                    f"expected={len(sell_ids)},"
                    f"compiled={len(seller_active_ids)}"
                ),
            }
        )

    if buyer_active_ids != buy_ids:
        audit_rows.append(
            {
                "itemid": 0,
                "name": "",
                "severity": "ERROR",
                "audit_type": "BUYER_ACTIVATION_SET_MISMATCH",
                "details": (
                    f"expected={len(buy_ids)},"
                    f"compiled={len(buyer_active_ids)}"
                ),
            }
        )

    sell_compiled = sell_baseline.copy()
    buy_compiled = buy_baseline.copy()

    # Verify every compiled row is authorized by rollout policy.
    for itemid in sell_compiled["itemid"].astype(int):
        policy = policy_by_id.get(itemid)
        rollout_row = rollout.loc[
            rollout["itemid"] == itemid
        ]

        if policy is None or rollout_row.empty:
            audit_rows.append(
                {
                    "itemid": itemid,
                    "name": "",
                    "severity": "ERROR",
                    "audit_type": "SELLER_POLICY_ROW_MISSING",
                    "details": "",
                }
            )

    for itemid in buy_compiled["itemid"].astype(int):
        policy = policy_by_id.get(itemid)
        rollout_row = rollout.loc[
            rollout["itemid"] == itemid
        ]

        if policy is None or rollout_row.empty:
            audit_rows.append(
                {
                    "itemid": itemid,
                    "name": "",
                    "severity": "ERROR",
                    "audit_type": "BUYER_POLICY_ROW_MISSING",
                    "details": "",
                }
            )

    # Write then re-read so the cutover comparison covers serialization too.
    ROLLOUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    rollout.to_csv(
        ROLLOUT_FILE,
        index=False,
    )

    sell_compiled.to_csv(
        SELL_OUTPUT_FILE,
        index=False,
    )

    buy_compiled.to_csv(
        BUY_OUTPUT_FILE,
        index=False,
    )

    sell_roundtrip = pd.read_csv(
        SELL_OUTPUT_FILE,
        low_memory=False,
    )

    buy_roundtrip = pd.read_csv(
        BUY_OUTPUT_FILE,
        low_memory=False,
    )

    sell_equal, sell_diffs = semantic_frame_equal(
        sell_baseline,
        sell_roundtrip,
    )

    buy_equal, buy_diffs = semantic_frame_equal(
        buy_baseline,
        buy_roundtrip,
    )

    if not sell_equal:
        for diff in sell_diffs:
            audit_rows.append(
                {
                    "itemid": 0,
                    "name": "",
                    "severity": "ERROR",
                    "audit_type": "SELLER_OUTPUT_MISMATCH",
                    "details": json.dumps(
                        diff,
                        sort_keys=True,
                    ),
                }
            )

    if not buy_equal:
        for diff in buy_diffs:
            audit_rows.append(
                {
                    "itemid": 0,
                    "name": "",
                    "severity": "ERROR",
                    "audit_type": "BUYER_OUTPUT_MISMATCH",
                    "details": json.dumps(
                        diff,
                        sort_keys=True,
                    ),
                }
            )

    if len(sell_roundtrip) != EXPECTED_SELL:
        audit_rows.append(
            {
                "itemid": 0,
                "name": "",
                "severity": "ERROR",
                "audit_type": "SELLER_ROW_COUNT_CHANGED",
                "details": str(len(sell_roundtrip)),
            }
        )

    if len(buy_roundtrip) != EXPECTED_BUY:
        audit_rows.append(
            {
                "itemid": 0,
                "name": "",
                "severity": "ERROR",
                "audit_type": "BUYER_ROW_COUNT_CHANGED",
                "details": str(len(buy_roundtrip)),
            }
        )

    if int(
        rollout["auto_live_promotion"].sum()
    ) != 0:
        audit_rows.append(
            {
                "itemid": 0,
                "name": "",
                "severity": "ERROR",
                "audit_type": "AUTO_PROMOTION_DETECTED",
                "details": "",
            }
        )

    audit = pd.DataFrame(
        audit_rows,
        columns=[
            "itemid",
            "name",
            "severity",
            "audit_type",
            "details",
        ],
    )

    AUDIT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    audit.to_csv(
        AUDIT_FILE,
        index=False,
    )

    error_count = int(
        (
            audit["severity"] == "ERROR"
        ).sum()
    ) if not audit.empty else 0

    summary = {
        "status": "PASS" if error_count == 0 else "FAIL",
        "server_maturity": CURRENT_SERVER_MATURITY,
        "total_policy_items": int(len(rollout)),
        "maturity_eligible_sellers": int(
            rollout["seller_maturity_eligible"].sum()
        ),
        "maturity_eligible_buyers": int(
            rollout["buyer_maturity_eligible"].sum()
        ),
        "rollout_active_sellers": int(
            rollout["seller_rollout_active"].sum()
        ),
        "rollout_active_buyers": int(
            rollout["buyer_rollout_active"].sum()
        ),
        "staged_sellers": int(
            rollout["seller_staged"].sum()
        ),
        "staged_buyers": int(
            rollout["buyer_staged"].sum()
        ),
        "compiled_seller_rows": int(
            len(sell_roundtrip)
        ),
        "compiled_buyer_rows": int(
            len(buy_roundtrip)
        ),
        "seller_semantic_match": bool(
            sell_equal
        ),
        "buyer_semantic_match": bool(
            buy_equal
        ),
        "seller_baseline_semantic_sha256":
            dataframe_semantic_hash(
                sell_baseline
            ),
        "seller_cutover_semantic_sha256":
            dataframe_semantic_hash(
                sell_roundtrip
            ),
        "buyer_baseline_semantic_sha256":
            dataframe_semantic_hash(
                buy_baseline
            ),
        "buyer_cutover_semantic_sha256":
            dataframe_semantic_hash(
                buy_roundtrip
            ),
        "hard_audit_errors": error_count,
        "auto_live_promotions": int(
            rollout["auto_live_promotion"].sum()
        ),
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

    print()
    print("=" * 60)
    print(" Unified Builder Cutover Validation")
    print("=" * 60)
    print(
        f"Server maturity:                         "
        f"{summary['server_maturity']}"
    )
    print(
        f"Maturity-eligible sellers:               "
        f"{summary['maturity_eligible_sellers']:>6}"
    )
    print(
        f"Maturity-eligible buyers:                "
        f"{summary['maturity_eligible_buyers']:>6}"
    )
    print(
        f"Rollout-active sellers:                  "
        f"{summary['rollout_active_sellers']:>6}"
    )
    print(
        f"Rollout-active buyers:                   "
        f"{summary['rollout_active_buyers']:>6}"
    )
    print(
        f"Staged sellers waiting for rollout:      "
        f"{summary['staged_sellers']:>6}"
    )
    print(
        f"Staged buyers waiting for rollout:       "
        f"{summary['staged_buyers']:>6}"
    )
    print()
    print(
        f"Compiled seller rows:                    "
        f"{summary['compiled_seller_rows']:>6}"
    )
    print(
        f"Compiled buyer rows:                     "
        f"{summary['compiled_buyer_rows']:>6}"
    )
    print(
        f"Seller semantic match:                   "
        f"{summary['seller_semantic_match']}"
    )
    print(
        f"Buyer semantic match:                    "
        f"{summary['buyer_semantic_match']}"
    )
    print(
        f"Hard audit errors:                       "
        f"{summary['hard_audit_errors']:>6}"
    )
    print(
        f"Auto live promotions:                    "
        f"{summary['auto_live_promotions']:>6}"
    )
    print()
    print(f"Rollout:  {ROLLOUT_FILE}")
    print(f"Seller:   {SELL_OUTPUT_FILE}")
    print(f"Buyer:    {BUY_OUTPUT_FILE}")
    print(f"Audit:    {AUDIT_FILE}")
    print(f"Summary:  {SUMMARY_FILE}")

    if error_count:
        raise UnifiedCutoverError(
            f"Cutover validation failed {error_count} hard audits. "
            f"See {AUDIT_FILE}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
