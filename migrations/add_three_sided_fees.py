"""
Sowtrust — Migration: Three-Sided Revenue Model.

Adds:
  1. platform_config — single-row table holding the three fee percentages,
     editable without a redeploy (satisfies "must be configurable, not
     hardcoded").
  2. New split financial columns on escrow_ledger — replaces the old
     single `service_fee` concept with explicit buyer/seller/logistics
     components, per the three-sided model.

BACKWARD COMPATIBILITY: the old `amount` and `service_fee` columns are
kept and kept in sync (not dropped) — existing code/tests/dashboard
queries referencing them keep working. New code should use the new
fields; `amount`/`service_fee` are now considered legacy aliases for
`product_amount`/`seller_platform_fee`.

Safe to run multiple times and safe to run against a live database.

Run once:  python migrations/add_three_sided_fees.py
"""
import sqlite3
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import config


def _add_column_if_missing(conn, table, column, coltype):
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        print(f"  + added {table}.{column}")
    else:
        print(f"  · {table}.{column} already exists, skipping")


def migrate():
    db_path = config.DATABASE_PATH
    print(f"[Sowtrust] Migrating database at: {db_path}")

    with sqlite3.connect(db_path) as conn:
        print("Platform config (configurable fee percentages):")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS platform_config (
                id                     INTEGER PRIMARY KEY CHECK (id = 1),
                buyer_fee_percent      REAL NOT NULL DEFAULT 2.5,
                seller_fee_percent     REAL NOT NULL DEFAULT 2.5,
                logistics_fee_percent  REAL NOT NULL DEFAULT 2.5,
                updated_at             TEXT DEFAULT (datetime('now')),
                updated_by             TEXT
            )
        """)
        # Single row, seeded once. INSERT OR IGNORE means re-running this
        # migration never resets fees you've already changed.
        conn.execute("""
            INSERT OR IGNORE INTO platform_config (id, buyer_fee_percent, seller_fee_percent, logistics_fee_percent)
            VALUES (1, 2.5, 2.5, 2.5)
        """)
        print("  + platform_config ready (default 2.5% / 2.5% / 2.5%)")

        print("Escrow ledger — split financial fields:")
        _add_column_if_missing(conn, "escrow_ledger", "product_amount", "REAL")
        _add_column_if_missing(conn, "escrow_ledger", "buyer_platform_fee", "REAL")
        _add_column_if_missing(conn, "escrow_ledger", "seller_platform_fee", "REAL")
        _add_column_if_missing(conn, "escrow_ledger", "logistics_amount", "REAL DEFAULT 0")
        _add_column_if_missing(conn, "escrow_ledger", "logistics_platform_fee", "REAL DEFAULT 0")
        _add_column_if_missing(conn, "escrow_ledger", "buyer_total", "REAL")
        _add_column_if_missing(conn, "escrow_ledger", "farmer_settlement_amount", "REAL")
        _add_column_if_missing(conn, "escrow_ledger", "logistics_settlement_amount", "REAL DEFAULT 0")
        _add_column_if_missing(conn, "escrow_ledger", "sowtrust_total_revenue", "REAL")

        # Backfill existing rows from the old amount/service_fee columns,
        # so historical transactions get sane values in the new fields
        # rather than NULL. Old rows never charged a buyer-side fee (that
        # was a pre-existing display-only bug — see migration notes),
        # so buyer_platform_fee is backfilled as 0 for them, not 2.5%.
        cur = conn.execute("""
            UPDATE escrow_ledger
            SET product_amount = amount,
                buyer_platform_fee = 0,
                seller_platform_fee = service_fee,
                buyer_total = amount,
                farmer_settlement_amount = amount - service_fee,
                sowtrust_total_revenue = service_fee
            WHERE product_amount IS NULL
        """)
        print(f"  + backfilled {cur.rowcount} existing transaction(s)")

    print("[Sowtrust] ✅ Three-sided fee model ready.")


if __name__ == "__main__":
    migrate()
