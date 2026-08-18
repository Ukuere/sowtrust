"""
Run all idempotent Sowtrust migrations in the required order.

Use this for existing databases before/after deploying a new build:
  python scripts/run_all_migrations.py
"""
from pathlib import Path
import importlib
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import config
from migrations.init_db import init_db

MIGRATIONS = [
    "migrations.add_session_table",
    "migrations.add_three_sided_fees",
    "migrations.add_products_table",
    "migrations.add_payments_columns",
    "migrations.add_buyer_accounts",
    "migrations.add_buyer_kyc",
    "migrations.add_kyc_verification_system",
    "migrations.add_logistics_providers",
    "migrations.add_logistics_kyc",
    "migrations.add_logistics_quotes",
    "migrations.add_logistics_replacements",
    "migrations.add_production_mvp_workflows",
    "migrations.add_payment_integrity",
    "migrations.add_unified_identity",
]


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    db_path = Path(config.DATABASE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        initialized = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='farmers'"
        ).fetchone()
    if not initialized:
        print("[Sowtrust] Empty database detected; creating the base schema.")
        init_db()

    for module_name in MIGRATIONS:
        print(f"\n[Sowtrust] Running {module_name}")
        module = importlib.import_module(module_name)
        module.migrate()
    print("\n[Sowtrust] All migrations completed.")


if __name__ == "__main__":
    main()
