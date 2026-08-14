"""
Sowtrust — Migration: Logistics Provider KYC Documents (spec section 5, 8).

logistics_providers already has kyc_status (from add_logistics_providers.py)
and it already gates job assignment — assign_provider() in
logistics_service.py has checked `kyc_status != 'VERIFIED'` since the
step 2 build. What's missing is anywhere for a provider to actually
SUBMIT the documents that earn that status, and an audit trail of the
decision.

Reuses the same `kyc_verifications` table buyers write to (created in
add_kyc_verification_system.py) — user_type='logistics_provider' this
time. That table was deliberately built generic for exactly this reuse.

Safe to run multiple times, safe against a live database.

Run once:  python migrations/add_logistics_kyc.py
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
        # If add_kyc_verification_system.py hasn't run yet on this DB,
        # kyc_verifications won't exist — this migration needs it.
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='kyc_verifications'"
        ).fetchone()
        if not exists:
            raise RuntimeError(
                "kyc_verifications table not found — run "
                "migrations/add_kyc_verification_system.py first."
            )

        print("Logistics providers — KYC document fields (spec section 5):")
        _add_column_if_missing(conn, "logistics_providers", "id_type", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "id_number", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "id_document_path", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "drivers_license_number", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "drivers_license_path", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "vehicle_registration_document_path", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "kyc_submitted_at", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "kyc_reviewed_at", "TEXT")
        _add_column_if_missing(conn, "logistics_providers", "kyc_rejection_reason", "TEXT")

    print("[Sowtrust] ✅ Logistics provider KYC documents ready.")


if __name__ == "__main__":
    migrate()
