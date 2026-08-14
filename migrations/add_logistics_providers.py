"""
Sowtrust — Migration: Logistics Provider Model.

Turns logistics from a free-text tracking log into a real financial
participant:

  1. NEW `logistics_providers` table — verified providers with business
     details, vehicle info, and a Paystack-verified payout account
     (mirroring exactly how farmers already work).
  2. `logistics_log` gains a provider reference + delivery code +
     payout tracking, so a delivery can be confirmed and settled.

Note on the delivery code: this is DISTINCT from the escrow release
code. The release code is buyer->farmer (proves goods arrived, releases
the farmer's money). The delivery code is buyer->logistics provider
(proves delivery completed, releases the provider's money). Two
separate parties being paid for two separate things — they must not
share a code.

Safe to run multiple times and safe against a live database.

Run once:  python migrations/add_logistics_providers.py
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
        print("Logistics providers table:")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logistics_providers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_uuid   TEXT NOT NULL UNIQUE DEFAULT (lower(hex(randomblob(16)))),
                name            TEXT NOT NULL,
                business_name   TEXT,
                phone           TEXT NOT NULL UNIQUE,
                email           TEXT,
                address         TEXT,
                operating_area  TEXT,
                pin_hash        TEXT NOT NULL,
                -- Vehicle / capability
                vehicle_type         TEXT,
                vehicle_registration TEXT,
                vehicle_capacity_kg  REAL,
                -- Payout destination (same pattern as farmers — Paystack-verified)
                bank_code            TEXT,
                bank_account_number  TEXT,
                bank_account_name    TEXT,
                bank_verified_at     TEXT,
                -- Status
                kyc_status      TEXT DEFAULT 'PENDING',   -- PENDING | VERIFIED | SUSPENDED
                is_active       INTEGER DEFAULT 1,
                completed_jobs  INTEGER DEFAULT 0,
                rating          REAL DEFAULT 0.0,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_logistics_providers_phone ON logistics_providers(phone)"
        )
        _add_column_if_missing(conn, "logistics_providers", "provider_uuid", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "name", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "business_name", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "phone", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "email", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "address", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "operating_area", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "pin_hash", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "vehicle_type", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "vehicle_registration", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "vehicle_capacity_kg", "REAL")
        _add_column_if_missing(conn, "logistics_providers", "bank_code", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "bank_account_number", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "bank_account_name", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "bank_verified_at", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "kyc_status", "TEXT DEFAULT 'PENDING'")
        _add_column_if_missing(conn, "logistics_providers", "is_active", "INTEGER DEFAULT 1")
        _add_column_if_missing(conn, "logistics_providers", "completed_jobs", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "logistics_providers", "rating", "REAL DEFAULT 0.0")
        _add_column_if_missing(conn, "logistics_providers", "created_at", "TEXT")
        conn.execute(
            """UPDATE logistics_providers
               SET provider_uuid = COALESCE(provider_uuid, lower(hex(randomblob(16)))),
                   kyc_status = COALESCE(kyc_status, 'PENDING'),
                   is_active = COALESCE(is_active, 1),
                   completed_jobs = COALESCE(completed_jobs, 0),
                   rating = COALESCE(rating, 0.0),
                   created_at = COALESCE(created_at, datetime('now'))"""
        )
        print("  + logistics_providers ready")

        print("Logistics log — provider link, delivery code, settlement:")
        _add_column_if_missing(conn, "logistics_log", "provider_id", "INTEGER")
        _add_column_if_missing(conn, "logistics_log", "quote_amount", "REAL")
        _add_column_if_missing(conn, "logistics_log", "platform_fee", "REAL")
        _add_column_if_missing(conn, "logistics_log", "settlement_amount", "REAL")
        _add_column_if_missing(conn, "logistics_log", "delivery_code_hash", "TEXT")
        _add_column_if_missing(conn, "logistics_log", "delivery_code_used_at", "TEXT")
        _add_column_if_missing(conn, "logistics_log", "payout_reference", "TEXT")
        _add_column_if_missing(conn, "logistics_log", "payout_status", "TEXT")
        _add_column_if_missing(conn, "logistics_log", "confirmed_at", "TEXT")

        # Log every delivery-code verification attempt (spec section 10).
        print("Delivery code attempt log:")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS delivery_code_attempts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                logistics_id  TEXT,
                txn_id        TEXT,
                attempted_by  TEXT,
                success       INTEGER NOT NULL DEFAULT 0,
                reason        TEXT,
                created_at    TEXT DEFAULT (datetime('now'))
            )
        """)
        print("  + delivery_code_attempts ready")

    print("[Sowtrust] ✅ Logistics provider model ready.")


if __name__ == "__main__":
    migrate()
