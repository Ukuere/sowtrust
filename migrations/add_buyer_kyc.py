"""
Sowtrust — Migration: Buyer KYC + Order Delivery Snapshot.

Extends the buyer web accounts added in add_buyer_accounts.py per spec
section 7 (structured buyer registration) and section 13 (every order
must be able to answer "where did this go" without joining back to a
buyer row that may have since changed).

Adds:
  1. buyers      — delivery address/city/state, buyer_type, email
                    verification fields.
  2. escrow_ledger — a SNAPSHOT of the buyer's name + delivery address at
                    the moment the order was placed. Deliberately
                    duplicated data, not a foreign-key lookup: if a buyer
                    edits their profile next week, an order placed today
                    must still show where it was actually shipped to.

Does not touch product_amount/fee columns — those are unaffected by
buyer KYC and already correct (see add_three_sided_fees.py).

Safe to run multiple times, safe against a live database.

Run once:  python migrations/add_buyer_kyc.py
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
        print("Buyers — structured KYC fields (spec section 7):")
        _add_column_if_missing(conn, "buyers", "delivery_address", "TEXT")
        _add_column_if_missing(conn, "buyers", "city", "TEXT")
        _add_column_if_missing(conn, "buyers", "state", "TEXT")
        # Individual | Retailer | Wholesaler | Restaurant/Hospitality | Processor | Other
        _add_column_if_missing(conn, "buyers", "buyer_type", "TEXT")
        _add_column_if_missing(conn, "buyers", "email_verified", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "buyers", "email_verification_token", "TEXT")
        _add_column_if_missing(conn, "buyers", "email_verification_sent_at", "TEXT")

        print("Escrow ledger — delivery snapshot per order (spec section 7):")
        _add_column_if_missing(conn, "escrow_ledger", "buyer_name", "TEXT")
        _add_column_if_missing(conn, "escrow_ledger", "delivery_address", "TEXT")
        _add_column_if_missing(conn, "escrow_ledger", "delivery_city", "TEXT")
        _add_column_if_missing(conn, "escrow_ledger", "delivery_state", "TEXT")

    print("[Sowtrust] ✅ Buyer KYC + order delivery snapshot ready.")


if __name__ == "__main__":
    migrate()
