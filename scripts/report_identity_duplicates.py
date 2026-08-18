"""Report invalid and equivalent phone identities without changing data."""
from collections import defaultdict
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from migrations.add_unified_identity import normalize_phone
from config.settings import config


TABLES = {
    "FARMER": "farmers",
    "BUYER": "buyers",
    "AGENT": "agents",
    "LOGISTICS": "logistics_providers",
}


def mask_phone(phone):
    return f"{phone[:4]}***{phone[-4:]}" if phone and len(phone) >= 8 else "masked"


def main():
    report = {"database": config.DATABASE_PATH, "roles": {}, "cross_role": []}
    identities = defaultdict(list)
    with sqlite3.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = sqlite3.Row
        for role, table in TABLES.items():
            rows = conn.execute(f"SELECT id, phone FROM {table} ORDER BY id").fetchall()
            grouped = defaultdict(list)
            invalid = []
            for row in rows:
                normalized = normalize_phone(row["phone"])
                if not normalized:
                    invalid.append({"record_id": row["id"], "phone": "invalid/masked"})
                    continue
                grouped[normalized].append(row["id"])
                identities[normalized].append({"role": role, "record_id": row["id"]})
            duplicates = [
                {"phone": mask_phone(phone), "record_ids": ids,
                 "strategy": "Keep the oldest record as primary; merge related records only after operations review."}
                for phone, ids in grouped.items() if len(ids) > 1
            ]
            report["roles"][role] = {
                "record_count": len(rows), "invalid": invalid, "duplicates": duplicates,
            }
    report["cross_role"] = [
        {"phone": mask_phone(phone), "roles": values,
         "strategy": "Link all roles to one users record; do not merge role profiles."}
        for phone, values in identities.items()
        if len({value["role"] for value in values}) > 1
    ]
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
