"""
Run all idempotent Sowtrust migrations in the required order.

Use this for existing databases before/after deploying a new build:
  python scripts/run_all_migrations.py
"""
from pathlib import Path
import importlib
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    "migrations.add_production_mvp_workflows",
]


def main():
    for module_name in MIGRATIONS:
        print(f"\n[Sowtrust] Running {module_name}")
        module = importlib.import_module(module_name)
        module.migrate()
    print("\n[Sowtrust] All migrations completed.")


if __name__ == "__main__":
    main()
