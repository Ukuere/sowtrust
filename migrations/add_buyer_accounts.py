"""
Sowtrust — Migration: Buyer Web Accounts (step 3 foundation).

USSD buyers were previously anonymous phone numbers, auto-inserted the
moment they placed an order (see escrow_service.initiate_escrow_payment,
`INSERT OR IGNORE INTO buyers (phone)`). The web app needs real accounts:
password-based login plus basic business/KYC info (spec section 7).

Phone number stays the single identity key across USSD and web, so a
buyer who orders on both channels sees one unified order history in
escrow_ledger (keyed on buyer_phone already).

Farmers/logistics keep their own separate 4-digit PIN auth over USSD —
buyers get a normal web password since they're on a browser, not a
feature phone.

Safe to run multiple times, safe against a live database.

Run once:  python migrations/add_buyer_accounts.py
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
        # Defensive — buyers should already exist (escrow_service creates
        # it via INSERT OR IGNORE), but CREATE TABLE IF NOT EXISTS means
        # this migration also works standalone against a fresh DB.
        conn.execute("CREATE TABLE IF NOT EXISTS buyers (phone TEXT PRIMARY KEY)")

        print("Buyers — web account fields:")
        _add_column_if_missing(conn, "buyers", "name", "TEXT")
        _add_column_if_missing(conn, "buyers", "business_name", "TEXT")
        _add_column_if_missing(conn, "buyers", "email", "TEXT")
        _add_column_if_missing(conn, "buyers", "password_hash", "TEXT")
        _add_column_if_missing(conn, "buyers", "address", "TEXT")
        _add_column_if_missing(conn, "buyers", "kyc_status", "TEXT DEFAULT 'PENDING'")
        _add_column_if_missing(conn, "buyers", "is_active", "INTEGER DEFAULT 1")
        _add_column_if_missing(conn, "buyers", "created_at", "TEXT DEFAULT (datetime('now'))")

        # NULLs don't collide under a unique index in SQLite, so buyers
        # who only ever ordered over USSD (no email) are unaffected.
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_buyers_email ON buyers(email)")
        print("  + idx_buyers_email ready")

    print("[Sowtrust] ✅ Buyer web accounts ready.")


if __name__ == "__main__":
    migrate()
