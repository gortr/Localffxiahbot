from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

UNIFIED_FILE = ROOT / "economy" / "generated" / "unified-seller-buyer-policy.csv"
CANONICAL_FILE = ROOT / "economy" / "generated" / "canonical-market-policy.csv"
RESOLUTION_FILE = ROOT / "economy" / "reports" / "missing-maturity-resolution.csv"

OUTPUT_FILE = ROOT / "economy" / "generated" / "economy-maturity-policy.csv"
AUDIT_FILE = ROOT / "economy" / "reports" / "economy-maturity-policy-audit.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "economy-maturity-policy-summary.json"


EXPECTED_TOTAL = 23534
EXPECTED_LIVE_SELL = 167
EXPECTED_LIVE_BUY = 159

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


class EconomyMaturityPolicyError(RuntimeError):
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


def load_unique(path: Path, label: str) -> pd.DataFrame:
    if not path.exists():
        raise EconomyMaturityPolicyError(
            f"Missing {label}: {path}"
        )

    frame = pd.read_csv(
        path,
        low_memory=False,
    )

    if "itemid" not in frame.columns:
        raise EconomyMaturityPolicyError(
            f"{label} has no itemid column."
        )

    frame = frame.copy()
    frame["itemid"] = frame["itemid"].map(as_int)

    if frame["itemid"].duplicated().any():
        raise EconomyMaturityPolicyError(
            f"{label} contains duplicate itemids."
        )

    return frame


def main() -> int:
    unified = load_unique(
        UNIFIED_FILE,
        "unified seller/buyer policy",
    )

    canonical = load_unique(
        CANONICAL_FILE,
        "canonical market policy",
    )

    resolution = load_unique(
        RESOLUTION_FILE,
        "missing maturity resolution",
    )

    if len(unified) != EXPECTED_TOTAL:
        raise EconomyMaturityPolicyError(
            f"Expected {EXPECTED_TOTAL} unified rows, found {len(unified)}"
        )

    if len(canonical) != EXPECTED_TOTAL:
        raise EconomyMaturityPolicyError(
            f"Expected {EXPECTED_TOTAL} canonical rows, found {len(canonical)}"
        )

    if len(resolution) != 544:
        raise EconomyMaturityPolicyError(
            f"Expected 544 maturity-resolution rows, found {len(resolution)}"
        )

    canonical_by_id = {
        int(row["itemid"]): row.to_dict()
        for _, row in canonical.iterrows()
    }

    resolution_by_id = {
        int(row["itemid"]): row.to_dict()
        for _, row in resolution.iterrows()
    }

    rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []

    for _, row in unified.iterrows():
        itemid = as_int(row.get("itemid"))
        canonical_row = canonical_by_id[itemid]
        resolution_row = resolution_by_id.get(itemid)

        pre_class = clean_text(
            row.get("canonical_class")
        ).upper()

        final_class = pre_class

        pre_seller = as_int(
            row.get("seller_capable")
        )

        pre_buyer = as_int(
            row.get("buyer_capable")
        )

        live_seller = as_int(
            row.get("live_seller_baseline")
        )

        live_buyer = as_int(
            row.get("live_buyer_baseline")
        )

        existing_maturity = clean_text(
            row.get("maturity")
        ).upper()

        final_maturity = ""
        maturity_source = ""
        maturity_disposition = ""
        protection_reason = ""
        seller_capable = pre_seller
        buyer_capable = pre_buyer

        if resolution_row is not None:
            disposition = clean_text(
                resolution_row.get("disposition")
            ).upper()

            if disposition == "KEEP_PROTECTED":
                final_class = "PROTECTED"
                final_maturity = ""
                maturity_source = "missing-maturity-resolution"
                maturity_disposition = "KEEP_PROTECTED"
                protection_reason = clean_text(
                    resolution_row.get("resolution_reason")
                )
                seller_capable = 0
                buyer_capable = 0

            elif disposition == "ASSIGN_MATURITY":
                final_maturity = clean_text(
                    resolution_row.get("final_maturity")
                ).upper()

                if final_maturity not in STAGE_RANK:
                    raise EconomyMaturityPolicyError(
                        f"Invalid resolved maturity {final_maturity!r} "
                        f"for item {itemid}"
                    )

                maturity_source = "missing-maturity-resolution"
                maturity_disposition = "ASSIGNED"

            elif disposition == "REVIEW":
                raise EconomyMaturityPolicyError(
                    f"Unresolved maturity review remains for item {itemid}"
                )

            else:
                raise EconomyMaturityPolicyError(
                    f"Unknown maturity disposition {disposition!r} "
                    f"for item {itemid}"
                )

        elif existing_maturity:
            if existing_maturity not in STAGE_RANK:
                raise EconomyMaturityPolicyError(
                    f"Invalid existing maturity {existing_maturity!r} "
                    f"for item {itemid}"
                )

            final_maturity = existing_maturity
            maturity_source = (
                clean_text(
                    canonical_row.get("maturity_source")
                )
                or "existing-policy"
            )
            maturity_disposition = "EXISTING"

        else:
            if pre_seller or pre_buyer:
                audit_rows.append(
                    {
                        "itemid": itemid,
                        "name": clean_text(row.get("name")),
                        "severity": "ERROR",
                        "audit_type": "CAPABLE_ITEM_MISSING_MATURITY",
                        "details": (
                            f"seller={pre_seller},buyer={pre_buyer},"
                            f"class={pre_class}"
                        ),
                    }
                )

            maturity_disposition = "NOT_REQUIRED"

        if (
            final_class in {
                "BLOCKED",
                "PROTECTED",
            }
        ):
            seller_capable = 0
            buyer_capable = 0

        maturity_rank = (
            STAGE_RANK[final_maturity]
            if final_maturity
            else -1
        )

        if (
            (seller_capable or buyer_capable)
            and not final_maturity
        ):
            audit_rows.append(
                {
                    "itemid": itemid,
                    "name": clean_text(row.get("name")),
                    "severity": "ERROR",
                    "audit_type": "POST_POLICY_CAPABLE_MISSING_MATURITY",
                    "details": (
                        f"seller={seller_capable},buyer={buyer_capable},"
                        f"class={final_class}"
                    ),
                }
            )

        if (
            final_maturity
            and final_maturity not in STAGE_RANK
        ):
            audit_rows.append(
                {
                    "itemid": itemid,
                    "name": clean_text(row.get("name")),
                    "severity": "ERROR",
                    "audit_type": "INVALID_MATURITY_STAGE",
                    "details": final_maturity,
                }
            )

        stage_flags: dict[str, int] = {}

        for stage in STAGE_ORDER:
            rank = STAGE_RANK[stage]

            stage_flags[
                f"seller_unlocked_at_{stage.lower()}"
            ] = int(
                bool(
                    seller_capable
                    and maturity_rank >= 0
                    and maturity_rank <= rank
                )
            )

            stage_flags[
                f"buyer_unlocked_at_{stage.lower()}"
            ] = int(
                bool(
                    buyer_capable
                    and maturity_rank >= 0
                    and maturity_rank <= rank
                )
            )

        rows.append(
            {
                "itemid": itemid,
                "name": clean_text(row.get("name")),
                "pre_maturity_class": pre_class,
                "final_market_class": final_class,
                "seller_capable_pre_maturity": pre_seller,
                "buyer_capable_pre_maturity": pre_buyer,
                "seller_capable": seller_capable,
                "buyer_capable": buyer_capable,
                "minimum_maturity": final_maturity,
                "minimum_maturity_rank": maturity_rank,
                "maturity_source": maturity_source,
                "maturity_disposition": maturity_disposition,
                "protection_reason": protection_reason,
                "source_policy_family": clean_text(
                    row.get("source_policy_family")
                ),
                "vendor_item": as_int(
                    row.get("vendor_item")
                ),
                "pricing_ready": as_int(
                    row.get("pricing_ready")
                ),
                "live_seller_baseline": live_seller,
                "live_buyer_baseline": live_buyer,
                "activation_ready": int(
                    bool(live_seller or live_buyer)
                ),
                "auto_live_promotion": 0,
                **stage_flags,
            }
        )

    output = pd.DataFrame(rows).sort_values(
        by=["itemid"],
        kind="stable",
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

    if len(output) != EXPECTED_TOTAL:
        raise EconomyMaturityPolicyError(
            f"Expected {EXPECTED_TOTAL} maturity-policy rows, "
            f"found {len(output)}"
        )

    if output["itemid"].duplicated().any():
        raise EconomyMaturityPolicyError(
            "Maturity policy contains duplicate itemids."
        )

    live_sell_count = int(
        output["live_seller_baseline"].sum()
    )

    live_buy_count = int(
        output["live_buyer_baseline"].sum()
    )

    if live_sell_count != EXPECTED_LIVE_SELL:
        raise EconomyMaturityPolicyError(
            f"Live seller baseline changed: {live_sell_count}"
        )

    if live_buy_count != EXPECTED_LIVE_BUY:
        raise EconomyMaturityPolicyError(
            f"Live buyer baseline changed: {live_buy_count}"
        )

    protected_special = output[
        output["maturity_disposition"]
        == "KEEP_PROTECTED"
    ]

    if len(protected_special) != 2:
        raise EconomyMaturityPolicyError(
            f"Expected 2 special protected corrections, "
            f"found {len(protected_special)}"
        )

    capable = output[
        (output["seller_capable"] == 1)
        | (output["buyer_capable"] == 1)
    ]

    missing_capable = capable[
        output.loc[
            capable.index,
            "minimum_maturity",
        ].fillna("").eq("")
    ]

    if not missing_capable.empty:
        raise EconomyMaturityPolicyError(
            f"{len(missing_capable)} capable items still lack maturity."
        )

    if int(output["auto_live_promotion"].sum()) != 0:
        raise EconomyMaturityPolicyError(
            "Maturity policy attempted automatic live promotion."
        )

    if not audit.empty:
        audit.to_csv(
            AUDIT_FILE,
            index=False,
        )

        errors = int(
            (audit["severity"] == "ERROR").sum()
        )

        if errors:
            raise EconomyMaturityPolicyError(
                f"Maturity policy failed {errors} hard audits. "
                f"See {AUDIT_FILE}"
            )
    else:
        audit.to_csv(
            AUDIT_FILE,
            index=False,
        )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    maturity_counts = {
        str(k): int(v)
        for k, v in (
            capable["minimum_maturity"]
            .value_counts()
            .to_dict()
            .items()
        )
    }

    cumulative: dict[str, dict[str, int]] = {}

    for stage in STAGE_ORDER:
        seller_col = f"seller_unlocked_at_{stage.lower()}"
        buyer_col = f"buyer_unlocked_at_{stage.lower()}"

        cumulative[stage] = {
            "seller": int(output[seller_col].sum()),
            "buyer": int(output[buyer_col].sum()),
            "union": int(
                (
                    (output[seller_col] == 1)
                    | (output[buyer_col] == 1)
                ).sum()
            ),
        }

    summary = {
        "status": "PASS",
        "total_items": int(len(output)),
        "seller_capable_after_maturity_policy": int(
            output["seller_capable"].sum()
        ),
        "buyer_capable_after_maturity_policy": int(
            output["buyer_capable"].sum()
        ),
        "capable_union_after_maturity_policy": int(
            len(capable)
        ),
        "capable_missing_maturity": 0,
        "protected_special_items": int(
            len(protected_special)
        ),
        "maturity_counts": maturity_counts,
        "cumulative_unlock_counts": cumulative,
        "live_seller_baseline": live_sell_count,
        "live_buyer_baseline": live_buy_count,
        "activation_union": int(
            (
                (output["live_seller_baseline"] == 1)
                | (output["live_buyer_baseline"] == 1)
            ).sum()
        ),
        "hard_audit_errors": 0,
        "activation_ready": 0,
        "auto_live_promotions": 0,
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
    print("=" * 58)
    print(" Canonical Economy Maturity Policy")
    print("=" * 58)
    print(
        f"Total items:                           "
        f"{summary['total_items']:>6}"
    )
    print(
        f"Seller capable after policy:           "
        f"{summary['seller_capable_after_maturity_policy']:>6}"
    )
    print(
        f"Buyer capable after policy:            "
        f"{summary['buyer_capable_after_maturity_policy']:>6}"
    )
    print(
        f"Capable union after policy:             "
        f"{summary['capable_union_after_maturity_policy']:>6}"
    )
    print(
        f"Capable missing maturity:               "
        f"{summary['capable_missing_maturity']:>6}"
    )
    print(
        f"Protected special corrections:          "
        f"{summary['protected_special_items']:>6}"
    )
    print(
        f"Live seller baseline:                   "
        f"{summary['live_seller_baseline']:>6}"
    )
    print(
        f"Live buyer baseline:                    "
        f"{summary['live_buyer_baseline']:>6}"
    )
    print(
        "Hard audit errors:                          0"
    )
    print(
        "Auto live promotions:                       0"
    )
    print()
    print("Minimum maturity:")
    for stage in STAGE_ORDER:
        print(
            f"  {stage:<14} "
            f"{summary['maturity_counts'].get(stage, 0):>6}"
        )
    print()
    print("Cumulative capability by server maturity:")
    for stage in STAGE_ORDER:
        values = summary["cumulative_unlock_counts"][stage]
        print(
            f"  {stage:<14} "
            f"sell={values['seller']:>6} "
            f"buy={values['buyer']:>6} "
            f"union={values['union']:>6}"
        )
    print()
    print(f"Generated: {OUTPUT_FILE}")
    print(f"Audit:     {AUDIT_FILE}")
    print(f"Summary:   {SUMMARY_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
