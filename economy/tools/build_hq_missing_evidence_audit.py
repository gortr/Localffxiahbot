from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pymysql
import yaml


ROOT = Path.home() / "ffxiahbot"

CONFIG_FILE = ROOT / "bin" / "config.yaml"
AUDIT_FILE = ROOT / "economy" / "reports" / "hq-rare-maturity-audit.csv"

OUTPUT_FILE = ROOT / "economy" / "reports" / "hq-missing-evidence-audit.csv"
SUMMARY_FILE = ROOT / "economy" / "reports" / "hq-missing-evidence-audit-summary.json"


class HQMissingAuditError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def as_int(value: Any) -> int:
    if value is None or pd.isna(value):
        return 0
    return int(value)


def load_db_settings() -> dict[str, Any]:
    data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        raise HQMissingAuditError(f"Invalid config mapping: {CONFIG_FILE}")

    source = data.get("db")
    if not isinstance(source, dict):
        source = data

    def required(*keys: str):
        for key in keys:
            if key in source and source[key] not in {None, ""}:
                return source[key]
        raise HQMissingAuditError(
            "Missing DB config key; expected one of: " + ", ".join(keys)
        )

    return {
        "host": required("hostname", "host"),
        "database": required("database", "dbname"),
        "user": required("username", "user"),
        "password": required("password"),
        "port": int(source.get("port", 3306)),
    }


def load_recipes() -> pd.DataFrame:
    cfg = load_db_settings()

    connection = pymysql.connect(
        host=str(cfg["host"]),
        user=str(cfg["user"]),
        password=str(cfg["password"]),
        database=str(cfg["database"]),
        port=int(cfg["port"]),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )

    query = """
        SELECT
            ID,
            Desynth,
            KeyItem,
            Result,
            ResultHQ1,
            ResultHQ2,
            ResultHQ3,
            ResultName,
            content_tag
        FROM synth_recipes
        ORDER BY ID
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
    finally:
        connection.close()

    return pd.DataFrame(rows)


def stripped_base_name(name: str) -> str:
    patterns = [
        r"_\+1$",
        r"_\+2$",
        r"_\+3$",
        r"_hq$",
        r"_+1$",
        r"_+2$",
        r"_+3$",
    ]

    base = name

    for pattern in patterns:
        base = re.sub(
            pattern,
            "",
            base,
            flags=re.IGNORECASE,
        )

    return base


def main() -> int:
    if not AUDIT_FILE.exists():
        raise HQMissingAuditError(f"Missing audit file: {AUDIT_FILE}")

    audit = pd.read_csv(AUDIT_FILE, low_memory=False)

    missing = audit[
        (audit["audit_bucket"] == "HQ_MATURITY_REVIEW")
        & (audit["hq_recipe_count"] == 0)
    ].copy()

    if len(missing) != 42:
        raise HQMissingAuditError(
            f"Expected 42 missing-HQ-evidence items, found {len(missing)}"
        )

    recipes = load_recipes()

    result_ids = set(
        recipes["Result"].fillna(0).astype(int).tolist()
    )

    all_hq_ids = set()

    for column in ["ResultHQ1", "ResultHQ2", "ResultHQ3"]:
        all_hq_ids.update(
            recipes[column]
            .fillna(0)
            .astype(int)
            .tolist()
        )

    rows: list[dict[str, Any]] = []

    for _, row in missing.iterrows():
        itemid = as_int(row.get("itemid"))
        name = clean_text(row.get("name"))
        base_name = stripped_base_name(name)

        name_looks_hq = int(
            (
                name.endswith("_+1")
                or name.endswith("_+2")
                or name.endswith("_+3")
                or "+1" in name
                or "+2" in name
                or "+3" in name
            )
        )

        rows.append(
            {
                "itemid": itemid,
                "name": name,
                "candidate_class": clean_text(
                    row.get("candidate_class")
                ),
                "candidate_reason": clean_text(
                    row.get("candidate_reason")
                ),
                "base_name_guess": base_name,
                "name_looks_hq": name_looks_hq,
                "appears_as_nq_result": int(
                    itemid in result_ids
                ),
                "appears_anywhere_as_hq_result": int(
                    itemid in all_hq_ids
                ),
                "vendor_item": as_int(
                    row.get("vendor_item")
                ),
                "stack_size": as_int(
                    row.get("stack_size")
                ),
                "rare": as_int(
                    row.get("rare")
                ),
                "can_equip": as_int(
                    row.get("can_equip")
                ),
                "pricing_ready": as_int(
                    row.get("pricing_ready")
                ),
                "provenance_tags": clean_text(
                    row.get("provenance_tags")
                ),
                "recommended_action": (
                    "FALSE_HQ_HEURISTIC_REVIEW"
                    if itemid in result_ids
                    else "SOURCE_OR_RECIPE_GAP_REVIEW"
                ),
                "activation_ready": 0,
                "auto_live_promotion": 0,
            }
        )

    output = pd.DataFrame(rows).sort_values(
        by=["recommended_action", "itemid"],
        kind="stable",
    )

    output.to_csv(OUTPUT_FILE, index=False)

    summary = {
        "status": "PASS",
        "total_items": int(len(output)),
        "name_looks_hq": int(output["name_looks_hq"].sum()),
        "appears_as_nq_result": int(
            output["appears_as_nq_result"].sum()
        ),
        "appears_anywhere_as_hq_result": int(
            output["appears_anywhere_as_hq_result"].sum()
        ),
        "actions": {
            str(k): int(v)
            for k, v in (
                output["recommended_action"]
                .value_counts()
                .to_dict()
                .items()
            )
        },
        "activation_ready": 0,
        "auto_live_promotions": 0,
    }

    SUMMARY_FILE.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print()
    print("======================================")
    print(" HQ Missing-Evidence Audit")
    print("======================================")
    print(f"Total items:              {summary['total_items']:>6}")
    print(f"Name looks HQ:            {summary['name_looks_hq']:>6}")
    print(
        f"Appears as NQ result:     "
        f"{summary['appears_as_nq_result']:>6}"
    )
    print(
        f"Appears as HQ result:     "
        f"{summary['appears_anywhere_as_hq_result']:>6}"
    )
    print("Activation ready: 0")
    print("Auto live promotions: 0")
    print()
    print(f"Audit:   {OUTPUT_FILE}")
    print(f"Summary: {SUMMARY_FILE}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
