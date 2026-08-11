"""
Sowtrust — Migration: real payment collection & settlement (Paystack).

Adds the columns needed to move from a simulated escrow (funds "locked"
with no real money changing hands) to a real one:
  - farmers get a verified bank/wallet destination for payouts
  - escrow_ledger tracks the actual Paystack payment + payout references

Safe to run multiple times and safe to run against a live database —
SQLite doesn't support "ADD COLUMN IF NOT EXISTS", so we check first.

Run once:  python migrations/add_payments_columns.py
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
        print("Farmers — payout destination:")
        _add_column_if_missing(conn, "farmers", "bank_code", "TEXT")
        _add_column_if_missing(conn, "farmers", "bank_account_number", "TEXT")
        _add_column_if_missing(conn, "farmers", "bank_account_name", "TEXT")
        _add_column_if_missing(conn, "farmers", "bank_verified_at", "TEXT")

        print("Escrow ledger — real payment tracking:")
        _add_column_if_missing(conn, "escrow_ledger", "payment_reference", "TEXT")
        _add_column_if_missing(conn, "escrow_ledger", "virtual_account_number", "TEXT")
        _add_column_if_missing(conn, "escrow_ledger", "virtual_account_bank", "TEXT")
        _add_column_if_missing(conn, "escrow_ledger", "payment_confirmed_at", "TEXT")
        _add_column_if_missing(conn, "escrow_ledger", "payout_reference", "TEXT")
        _add_column_if_missing(conn, "escrow_ledger", "payout_status", "TEXT")

        # Any existing rows created before this migration were simulated —
        # they never had real money move. Mark them so they're not confused
        # with real transactions in reporting.
        conn.execute(
            """UPDATE escrow_ledger SET payout_status = 'SIMULATED_LEGACY'
               WHERE payout_status IS NULL AND status IN ('DELIVERED')"""
        )

    print("[Sowtrust] ✅ Payment columns ready.")


if __name__ == "__main__":
    migrate()
