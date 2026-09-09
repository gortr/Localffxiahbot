from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path.home() / "ffxiahbot"

BASE_FILE = ROOT / "economy" / "generated" / "candidate-market-policy.csv"
CLASSIFICATION_FILE = ROOT / "economy" / "generated" / "market-classification.csv"
RESTRICTED_FILE = ROOT / "economy" / "generated" / "restricted-craft-policy.csv"
SCROLL_PUPPET_FILE = ROOT / "economy" / "generated" / "scroll-puppet-policy.csv"
HQ_RARE_FILE = ROOT / "economy" / "generated" / "hq-rare-maturity-policy.csv"
UNKNOWN_SOURCE_FILE = ROOT / "economy" / "generated" / "unknown-provenance-policy.csv"

LIVE_SELL_FILE = ROOT / "economy" / "generated" / "market-sell-phase0.csv"
LIVE_BUY_FILE = ROOT / "economy" / "generated" / "market-buy-phase0.csv"

OUTPUT_FILE = ROOT / "economy" / "generated" / "canonical-market-policy.csv"
AUDIT_FILE = ROOT / "economy" / "reports" / "canonical-policy-integration-audit.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "canonical-policy-integration-summary.json"


EXPECTED_TOTAL = 23534
EXPECTED_LIVE_SELL = 167
EXPECTED_LIVE_BUY = 159


class CanonicalIntegrationError(RuntimeError):
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


def as_bool_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0

    if isinstance(value, bool):
        return int(value)

    text = str(value).strip().lower()

    if text in {
        "1", "true", "yes", "y", "on", "allow", "allowed",
        "eligible", "enabled",
    }:
        return 1

    if text in {
        "0", "false", "no", "n", "off", "deny", "denied",
        "ineligible", "disabled", "",
    }:
        return 0

    try:
        return int(float(text) != 0)
    except ValueError:
        return 0


def normalize(value: str) -> str:
    return re.sub(
        r"[^a-z0-9]",
        "",
        value.lower(),
    )


def find_column(
    frame: pd.DataFrame,
    preferred: list[str],
    contains: list[str] | None = None,
    exclude_contains: list[str] | None = None,
) -> str | None:
    by_norm = {
        normalize(column): column
        for column in frame.columns
    }

    for candidate in preferred:
        found = by_norm.get(
            normalize(candidate)
        )
        if found:
            return found

    if contains:
        for column in frame.columns:
            norm = normalize(column)

            if all(
                normalize(part) in norm
                for part in contains
            ):
                if exclude_contains and any(
                    normalize(part) in norm
                    for part in exclude_contains
                ):
                    continue
                return column

    return None


def itemid_column(frame: pd.DataFrame) -> str:
    column = find_column(
        frame,
        ["itemid", "item_id", "id"],
    )

    if column is None:
        raise CanonicalIntegrationError(
            "Could not find itemid column. "
            f"Columns: {list(frame.columns)}"
        )

    return column


def class_column(frame: pd.DataFrame) -> str | None:
    return find_column(
        frame,
        [
            "candidate_class",
            "market_class",
            "policy_class",
            "recommended_class",
            "class",
        ],
        contains=["class"],
        exclude_contains=[
            "source",
            "mob",
        ],
    )


def seller_column(frame: pd.DataFrame) -> str | None:
    for preferred in [
        "seller_eligible",
        "seller_candidate",
        "seller_allowed",
        "sell_eligible",
        "seller_policy_eligible",
    ]:
        column = find_column(
            frame,
            [preferred],
        )
        if column:
            return column

    return find_column(
        frame,
        [],
        contains=[
            "seller",
            "eligible",
        ],
    )


def buyer_column(frame: pd.DataFrame) -> str | None:
    for preferred in [
        "buyer_eligible",
        "buyer_candidate",
        "buyer_allowed",
        "buy_eligible",
        "buyer_policy_eligible",
    ]:
        column = find_column(
            frame,
            [preferred],
        )
        if column:
            return column

    return find_column(
        frame,
        [],
        contains=[
            "buyer",
            "eligible",
        ],
    )


def maturity_column(frame: pd.DataFrame) -> str | None:
    return find_column(
        frame,
        [
            "maturity",
            "maturity_stage",
            "required_maturity",
            "policy_maturity",
            "minimum_maturity",
        ],
        contains=["maturity"],
    )


def reason_column(frame: pd.DataFrame) -> str | None:
    return find_column(
        frame,
        [
            "candidate_reason",
            "policy_reason",
            "reason",
        ],
        contains=["reason"],
    )


def policy_ready_column(frame: pd.DataFrame) -> str | None:
    return find_column(
        frame,
        [
            "policy_ready",
            "policyready",
        ],
        contains=[
            "policy",
            "ready",
        ],
    )


def pricing_ready_column(frame: pd.DataFrame) -> str | None:
    return find_column(
        frame,
        [
            "pricing_ready",
            "pricingready",
        ],
        contains=[
            "pricing",
            "ready",
        ],
    )


def load_unique(
    path: Path,
    label: str,
) -> pd.DataFrame:
    if not path.exists():
        raise CanonicalIntegrationError(
            f"Missing {label}: {path}"
        )

    frame = pd.read_csv(
        path,
        low_memory=False,
    )

    id_col = itemid_column(
        frame
    )

    if id_col != "itemid":
        frame = frame.rename(
            columns={
                id_col: "itemid"
            }
        )

    frame["itemid"] = frame[
        "itemid"
    ].map(
        as_int
    )

    if (
        frame["itemid"]
        .duplicated()
        .any()
    ):
        duplicates = (
            frame.loc[
                frame["itemid"].duplicated(
                    keep=False
                ),
                "itemid",
            ]
            .head(20)
            .tolist()
        )

        raise CanonicalIntegrationError(
            f"{label} has duplicate itemids: "
            f"{duplicates}"
        )

    return frame


def load_id_set(
    path: Path,
    label: str,
) -> set[int]:
    frame = load_unique(
        path,
        label,
    )

    return {
        int(itemid)
        for itemid in frame[
            "itemid"
        ].tolist()
        if int(itemid) > 0
    }


def sidecar_map(
    frame: pd.DataFrame,
) -> dict[
    int,
    dict[
        str,
        Any,
    ],
]:
    return {
        int(row["itemid"]):
            row.to_dict()
        for _, row in frame.iterrows()
    }


def overlay_fields(
    row: dict[str, Any],
    sidecar_row: dict[str, Any],
    sidecar: pd.DataFrame,
    label: str,
) -> None:
    class_col = class_column(
        sidecar
    )
    seller_col = seller_column(
        sidecar
    )
    buyer_col = buyer_column(
        sidecar
    )
    maturity_col = maturity_column(
        sidecar
    )
    reason_col = reason_column(
        sidecar
    )

    if class_col:
        value = clean_text(
            sidecar_row.get(
                class_col
            )
        )
        if value:
            row[
                "canonical_class"
            ] = value
            row[
                "class_source"
            ] = label

    if seller_col:
        row[
            "seller_policy_eligible"
        ] = as_bool_int(
            sidecar_row.get(
                seller_col
            )
        )
        row[
            "seller_source"
        ] = label

    if buyer_col:
        row[
            "buyer_policy_eligible"
        ] = as_bool_int(
            sidecar_row.get(
                buyer_col
            )
        )
        row[
            "buyer_source"
        ] = label

    if maturity_col:
        value = clean_text(
            sidecar_row.get(
                maturity_col
            )
        )
        if value:
            row[
                "maturity"
            ] = value
            row[
                "maturity_source"
            ] = label

    if reason_col:
        value = clean_text(
            sidecar_row.get(
                reason_col
            )
        )
        if value:
            row[
                "overlay_reason"
            ] = value

    overlays = clean_text(
        row.get(
            "applied_overlays"
        )
    )

    row[
        "applied_overlays"
    ] = (
        f"{overlays}|{label}"
        if overlays
        else label
    )


def main() -> int:
    base = load_unique(
        BASE_FILE,
        "candidate market policy",
    )

    classification = load_unique(
        CLASSIFICATION_FILE,
        "market classification",
    )

    restricted = load_unique(
        RESTRICTED_FILE,
        "restricted craft policy",
    )

    scroll_puppet = load_unique(
        SCROLL_PUPPET_FILE,
        "scroll/puppet policy",
    )

    hq_rare = load_unique(
        HQ_RARE_FILE,
        "HQ/Rare policy",
    )

    unknown_source = load_unique(
        UNKNOWN_SOURCE_FILE,
        "unknown provenance policy",
    )

    live_sell_ids = load_id_set(
        LIVE_SELL_FILE,
        "Phase-0 seller baseline",
    )

    live_buy_ids = load_id_set(
        LIVE_BUY_FILE,
        "Phase-0 buyer baseline",
    )

    if len(base) != EXPECTED_TOTAL:
        raise CanonicalIntegrationError(
            f"Expected {EXPECTED_TOTAL} base rows, "
            f"found {len(base)}"
        )

    if len(classification) != EXPECTED_TOTAL:
        raise CanonicalIntegrationError(
            f"Expected {EXPECTED_TOTAL} classification rows, "
            f"found {len(classification)}"
        )

    if len(live_sell_ids) != EXPECTED_LIVE_SELL:
        raise CanonicalIntegrationError(
            f"Expected {EXPECTED_LIVE_SELL} live seller ids, "
            f"found {len(live_sell_ids)}"
        )

    if len(live_buy_ids) != EXPECTED_LIVE_BUY:
        raise CanonicalIntegrationError(
            f"Expected {EXPECTED_LIVE_BUY} live buyer ids, "
            f"found {len(live_buy_ids)}"
        )

    base_class_col = class_column(
        base
    )

    if base_class_col is None:
        raise CanonicalIntegrationError(
            "Candidate market policy has no recognizable "
            f"class column. Columns: {list(base.columns)}"
        )

    base_seller_col = seller_column(
        base
    )

    base_buyer_col = buyer_column(
        base
    )

    base_maturity_col = maturity_column(
        base
    )

    base_reason_col = reason_column(
        base
    )

    base_policy_ready_col = policy_ready_column(
        base
    )

    base_pricing_ready_col = pricing_ready_column(
        base
    )

    class_map = sidecar_map(
        classification
    )

    restricted_map = sidecar_map(
        restricted
    )

    scroll_map = sidecar_map(
        scroll_puppet
    )

    hq_map = sidecar_map(
        hq_rare
    )

    unknown_map = sidecar_map(
        unknown_source
    )

    output_rows: list[
        dict[
            str,
            Any,
        ]
    ] = []

    audit_rows: list[
        dict[
            str,
            Any,
        ]
    ] = []

    for _, base_row in base.iterrows():
        itemid = int(
            base_row[
                "itemid"
            ]
        )

        canonical_class = clean_text(
            base_row.get(
                base_class_col
            )
        )

        if not canonical_class:
            classification_row = class_map.get(
                itemid,
                {}
            )

            class_col = class_column(
                classification
            )

            canonical_class = (
                clean_text(
                    classification_row.get(
                        class_col
                    )
                )
                if class_col
                else ""
            )

        row: dict[str, Any] = {
            "itemid":
                itemid,

            "name":
                clean_text(
                    base_row.get(
                        "name"
                    )
                ),

            "canonical_class":
                canonical_class,

            "class_source":
                "candidate-market-policy",

            "seller_policy_eligible":
                (
                    as_bool_int(
                        base_row.get(
                            base_seller_col
                        )
                    )
                    if base_seller_col
                    else 0
                ),

            "buyer_policy_eligible":
                (
                    as_bool_int(
                        base_row.get(
                            base_buyer_col
                        )
                    )
                    if base_buyer_col
                    else 0
                ),

            "seller_source":
                (
                    "candidate-market-policy"
                    if base_seller_col
                    else ""
                ),

            "buyer_source":
                (
                    "candidate-market-policy"
                    if base_buyer_col
                    else ""
                ),

            "maturity":
                (
                    clean_text(
                        base_row.get(
                            base_maturity_col
                        )
                    )
                    if base_maturity_col
                    else ""
                ),

            "maturity_source":
                (
                    "candidate-market-policy"
                    if base_maturity_col
                    else ""
                ),

            "base_reason":
                (
                    clean_text(
                        base_row.get(
                            base_reason_col
                        )
                    )
                    if base_reason_col
                    else ""
                ),

            "overlay_reason":
                "",

            "applied_overlays":
                "",

            "policy_ready":
                (
                    as_bool_int(
                        base_row.get(
                            base_policy_ready_col
                        )
                    )
                    if base_policy_ready_col
                    else 0
                ),

            "pricing_ready":
                (
                    as_bool_int(
                        base_row.get(
                            base_pricing_ready_col
                        )
                    )
                    if base_pricing_ready_col
                    else 0
                ),

            "source_policy_family":
                "",

            "source_gate_open":
                1,

            "synthetic_supply_source_allowed":
                1,

            "source_keep_protected":
                0,

            "pricing_gate_required":
                0,

            "live_seller_baseline":
                int(
                    itemid in live_sell_ids
                ),

            "live_buyer_baseline":
                int(
                    itemid in live_buy_ids
                ),

            "activation_ready":
                int(
                    itemid
                    in (
                        live_sell_ids
                        | live_buy_ids
                    )
                ),

            "auto_live_promotion":
                0,
        }

        # Dedicated semantic sidecars. Puppet/scroll is last so an internal
        # Puppet BLOCKED decision cannot be weakened by another overlay.
        restricted_row = restricted_map.get(
            itemid
        )

        if restricted_row is not None:
            overlay_fields(
                row,
                restricted_row,
                restricted,
                "restricted-craft",
            )

        hq_row = hq_map.get(
            itemid
        )

        if hq_row is not None:
            overlay_fields(
                row,
                hq_row,
                hq_rare,
                "hq-rare",
            )

        scroll_row = scroll_map.get(
            itemid
        )

        if scroll_row is not None:
            overlay_fields(
                row,
                scroll_row,
                scroll_puppet,
                "scroll-puppet",
            )

        # Unknown-provenance policy is a safety gate, not an expansion rule.
        source_row = unknown_map.get(
            itemid
        )

        if source_row is not None:
            family = clean_text(
                source_row.get(
                    "policy_family"
                )
            )

            row[
                "source_policy_family"
            ] = family

            row[
                "source_gate_open"
            ] = as_bool_int(
                source_row.get(
                    "source_gate_open"
                )
            )

            row[
                "synthetic_supply_source_allowed"
            ] = as_bool_int(
                source_row.get(
                    "synthetic_supply_source_allowed"
                )
            )

            row[
                "source_keep_protected"
            ] = as_bool_int(
                source_row.get(
                    "keep_protected"
                )
            )

            row[
                "pricing_gate_required"
            ] = as_bool_int(
                source_row.get(
                    "pricing_gate_required"
                )
            )

            if family in {
                "PROTECTED_SOURCE",
                "SOURCE_REVIEW",
                "UNKNOWN_ACQUISITION",
                "INTERNAL_REVIEW",
            }:
                if clean_text(
                    row[
                        "canonical_class"
                    ]
                ).upper() != "BLOCKED":
                    row[
                        "canonical_class"
                    ] = "PROTECTED"

                row[
                    "class_source"
                ] = "unknown-provenance-safety"

                row[
                    "seller_policy_eligible"
                ] = 0

                row[
                    "buyer_policy_eligible"
                ] = 0

                row[
                    "seller_source"
                ] = "unknown-provenance-safety"

                row[
                    "buyer_source"
                ] = "unknown-provenance-safety"

            elif family == "VENDOR_PRICING_BLOCK":
                row[
                    "seller_policy_eligible"
                ] = 0

                row[
                    "buyer_policy_eligible"
                ] = 0

                row[
                    "seller_source"
                ] = "vendor-pricing-block"

                row[
                    "buyer_source"
                ] = "vendor-pricing-block"

            elif family == "ORDINARY_SOURCE":
                # A resolved ordinary source only opens the source gate.
                # It does not create seller/buyer eligibility by itself.
                pass

            else:
                raise CanonicalIntegrationError(
                    f"Unhandled source policy family "
                    f"{family!r} for item {itemid}"
                )

            overlays = clean_text(
                row[
                    "applied_overlays"
                ]
            )

            row[
                "applied_overlays"
            ] = (
                f"{overlays}|unknown-provenance"
                if overlays
                else "unknown-provenance"
            )

        # Hard class invariant.
        if clean_text(
            row[
                "canonical_class"
            ]
        ).upper() == "BLOCKED":
            row[
                "seller_policy_eligible"
            ] = 0

            row[
                "buyer_policy_eligible"
            ] = 0

        # Source safety invariant.
        if not as_bool_int(
            row[
                "synthetic_supply_source_allowed"
            ]
        ):
            row[
                "seller_policy_eligible"
            ] = 0

        if as_bool_int(
            row[
                "source_keep_protected"
            ]
        ):
            row[
                "buyer_policy_eligible"
            ] = 0

        # Pricing safety invariant.
        if as_bool_int(
            row[
                "pricing_gate_required"
            ]
        ) and not as_bool_int(
            row[
                "pricing_ready"
            ]
        ):
            row[
                "seller_policy_eligible"
            ] = 0

            row[
                "buyer_policy_eligible"
            ] = 0

        output_rows.append(
            row
        )

        audit_rows.append(
            {
                "itemid":
                    itemid,

                "name":
                    row[
                        "name"
                    ],

                "canonical_class":
                    row[
                        "canonical_class"
                    ],

                "class_source":
                    row[
                        "class_source"
                    ],

                "applied_overlays":
                    row[
                        "applied_overlays"
                    ],

                "source_policy_family":
                    row[
                        "source_policy_family"
                    ],

                "seller_policy_eligible":
                    row[
                        "seller_policy_eligible"
                    ],

                "buyer_policy_eligible":
                    row[
                        "buyer_policy_eligible"
                    ],

                "live_seller_baseline":
                    row[
                        "live_seller_baseline"
                    ],

                "live_buyer_baseline":
                    row[
                        "live_buyer_baseline"
                    ],

                "pricing_ready":
                    row[
                        "pricing_ready"
                    ],

                "pricing_gate_required":
                    row[
                        "pricing_gate_required"
                    ],

                "maturity":
                    row[
                        "maturity"
                    ],

                "maturity_source":
                    row[
                        "maturity_source"
                    ],
            }
        )

    output = pd.DataFrame(
        output_rows
    ).sort_values(
        by=[
            "itemid"
        ],
        kind="stable",
    )

    audit = pd.DataFrame(
        audit_rows
    ).sort_values(
        by=[
            "itemid"
        ],
        kind="stable",
    )

    if len(output) != EXPECTED_TOTAL:
        raise CanonicalIntegrationError(
            f"Expected {EXPECTED_TOTAL} canonical rows, "
            f"found {len(output)}"
        )

    if (
        output[
            "itemid"
        ].duplicated().any()
    ):
        raise CanonicalIntegrationError(
            "Canonical policy contains duplicate itemids."
        )

    live_sell_count = int(
        output[
            "live_seller_baseline"
        ].sum()
    )

    live_buy_count = int(
        output[
            "live_buyer_baseline"
        ].sum()
    )

    if live_sell_count != EXPECTED_LIVE_SELL:
        raise CanonicalIntegrationError(
            f"Live seller baseline changed: "
            f"{live_sell_count} != {EXPECTED_LIVE_SELL}"
        )

    if live_buy_count != EXPECTED_LIVE_BUY:
        raise CanonicalIntegrationError(
            f"Live buyer baseline changed: "
            f"{live_buy_count} != {EXPECTED_LIVE_BUY}"
        )

    # Live activation is baseline-only. No policy overlay may add another ID.
    activation_ids = set(
        output.loc[
            output[
                "activation_ready"
            ]
            == 1,
            "itemid",
        ].astype(int)
    )

    expected_activation_ids = (
        live_sell_ids
        | live_buy_ids
    )

    if activation_ids != expected_activation_ids:
        added = sorted(
            activation_ids
            - expected_activation_ids
        )[:20]

        missing = sorted(
            expected_activation_ids
            - activation_ids
        )[:20]

        raise CanonicalIntegrationError(
            "Canonical integration changed the live activation set. "
            f"added={added}, missing={missing}"
        )

    if int(
        output[
            "auto_live_promotion"
        ].sum()
    ) != 0:
        raise CanonicalIntegrationError(
            "Canonical integration attempted automatic live promotion."
        )

    output.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    audit.to_csv(
        AUDIT_FILE,
        index=False,
    )

    summary = {
        "status":
            "PASS",

        "total_items":
            int(
                len(
                    output
                )
            ),

        "live_seller_baseline":
            live_sell_count,

        "live_buyer_baseline":
            live_buy_count,

        "activation_item_union":
            int(
                len(
                    activation_ids
                )
            ),

        "auto_live_promotions":
            0,

        "class_counts":
            {
                str(k): int(v)
                for k, v
                in (
                    output[
                        "canonical_class"
                    ]
                    .replace(
                        "",
                        "NONE",
                    )
                    .value_counts()
                    .to_dict()
                    .items()
                )
            },

        "seller_policy_eligible":
            int(
                output[
                    "seller_policy_eligible"
                ].sum()
            ),

        "buyer_policy_eligible":
            int(
                output[
                    "buyer_policy_eligible"
                ].sum()
            ),

        "source_policy_family_counts":
            {
                str(k): int(v)
                for k, v
                in (
                    output[
                        "source_policy_family"
                    ]
                    .replace(
                        "",
                        "NOT_APPLICABLE",
                    )
                    .value_counts()
                    .to_dict()
                    .items()
                )
            },

        "overlay_counts":
            {
                "restricted_craft":
                    int(
                        output[
                            "applied_overlays"
                        ].str.contains(
                            "restricted-craft",
                            na=False,
                        ).sum()
                    ),

                "hq_rare":
                    int(
                        output[
                            "applied_overlays"
                        ].str.contains(
                            "hq-rare",
                            na=False,
                        ).sum()
                    ),

                "scroll_puppet":
                    int(
                        output[
                            "applied_overlays"
                        ].str.contains(
                            "scroll-puppet",
                            na=False,
                        ).sum()
                    ),

                "unknown_provenance":
                    int(
                        output[
                            "applied_overlays"
                        ].str.contains(
                            "unknown-provenance",
                            na=False,
                        ).sum()
                    ),
            },
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
    print("=" * 54)
    print(" Canonical Market Policy Integration")
    print("=" * 54)
    print(
        f"Total items:                         "
        f"{summary['total_items']:>6}"
    )
    print(
        f"Live seller baseline:                "
        f"{summary['live_seller_baseline']:>6}"
    )
    print(
        f"Live buyer baseline:                 "
        f"{summary['live_buyer_baseline']:>6}"
    )
    print(
        f"Activation union:                    "
        f"{summary['activation_item_union']:>6}"
    )
    print(
        f"Seller policy eligible:              "
        f"{summary['seller_policy_eligible']:>6}"
    )
    print(
        f"Buyer policy eligible:               "
        f"{summary['buyer_policy_eligible']:>6}"
    )
    print(
        "Auto live promotions:                     0"
    )
    print()
    print("Overlay counts:")
    for label, count in summary[
        "overlay_counts"
    ].items():
        print(
            f"  {label:<28} "
            f"{count:>6}"
        )
    print()
    print("Canonical classes:")
    for label, count in sorted(
        summary[
            "class_counts"
        ].items(),
        key=lambda pair:
            (
                -pair[
                    1
                ],
                pair[
                    0
                ],
            ),
    ):
        print(
            f"  {label:<20} "
            f"{count:>6}"
        )
    print()
    print(f"Canonical: {OUTPUT_FILE}")
    print(f"Audit:     {AUDIT_FILE}")
    print(f"Summary:   {SUMMARY_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
