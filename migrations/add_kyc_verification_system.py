"""
Sowtrust — Migration: KYC Verification System (spec sections 2, 3, 7).

Two additive pieces:

  1. `buyers` gains ID/document fields — individual (id_type, id_number,
     id_document_path) and business (CAC number/document, authorized
     representative) — per spec section 2.

  2. New `kyc_verifications` table — a proper audit record, not a boolean.
     Deliberately generic (user_type + user_id, not a buyer-only table) so
     the same table can carry farmer and logistics-provider verification
     records later without another migration. Only buyers write to it
     for now.

DESIGN NOTE: the spec gives two KYC status vocabularies for buyers —
section 6's onboarding sequence (REGISTERED -> PHONE_VERIFIED ->
PROFILE_COMPLETED -> KYC_PENDING -> VERIFIED) and section 3's record
statuses (PENDING/UNDER_REVIEW/VERIFIED/REJECTED/EXPIRED/SUSPENDED).
This migration does NOT create two separate status columns — buyers.kyc_status
is the single source of truth checkout gates on, using a merged vocabulary
(see BUYER_KYC_STATUSES in buyer_service.py). kyc_verifications.status uses
section 3's vocabulary for the audit record itself. If you want these
fully separated later, that's a straightforward follow-up, not a rewrite.

Also note: PHONE_VERIFIED is in the vocabulary but not yet reachable —
there's no OTP mechanism wired into buyer registration yet (that would
live in sms_service.py, which predates what I have visibility into).
New buyers currently land on PROFILE_COMPLETED directly after
registration, skipping that step honestly rather than faking it.

Safe to run multiple times, safe against a live database.

Run once:  python migrations/add_kyc_verification_system.py
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
        print("Buyers — ID + business verification fields (spec section 2):")
        _add_column_if_missing(conn, "buyers", "id_type", "TEXT")
        _add_column_if_missing(conn, "buyers", "id_number", "TEXT")
        _add_column_if_missing(conn, "buyers", "id_document_path", "TEXT")
        _add_column_if_missing(conn, "buyers", "business_reg_number", "TEXT")
        _add_column_if_missing(conn, "buyers", "business_reg_document_path", "TEXT")
        _add_column_if_missing(conn, "buyers", "authorized_rep_name", "TEXT")
        _add_column_if_missing(conn, "buyers", "authorized_rep_id_number", "TEXT")
        _add_column_if_missing(conn, "buyers", "kyc_submitted_at", "TEXT")
        _add_column_if_missing(conn, "buyers", "kyc_reviewed_at", "TEXT")
        _add_column_if_missing(conn, "buyers", "kyc_rejection_reason", "TEXT")

        print("KYC verification records (spec section 3):")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kyc_verifications (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_type           TEXT NOT NULL,   -- 'buyer' | 'farmer' | 'logistics_provider'
                user_id             TEXT NOT NULL,   -- phone number
                verification_type   TEXT NOT NULL,   -- e.g. 'identity', 'business'
                status              TEXT NOT NULL DEFAULT 'PENDING',
                                    -- PENDING | UNDER_REVIEW | VERIFIED | REJECTED | EXPIRED | SUSPENDED
                provider            TEXT,             -- NULL = manual review; set once a 3rd-party is wired in
                provider_reference  TEXT,
                submitted_at        TEXT DEFAULT (datetime('now')),
                verified_at         TEXT,
                reviewed_by         TEXT,
                rejection_reason    TEXT,
                expiry_date         TEXT,
                created_at          TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kyc_verifications_user ON kyc_verifications(user_type, user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kyc_verifications_status ON kyc_verifications(status)"
        )
        print("  + kyc_verifications ready")

    print("[Sowtrust] ✅ KYC verification system ready.")


if __name__ == "__main__":
    migrate()
