"""
Sowtrust - Production MVP workflow migration.

Adds product media/listing review, notification records, and dispute
records. Safe to run multiple times against an existing SQLite database.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import config


def _add_column_if_missing(conn, table, column, coltype):
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        print(f"  + added {table}.{column}")
    else:
        print(f"  - {table}.{column} already exists")


def migrate():
    db_path = config.DATABASE_PATH
    print(f"[Sowtrust] Migrating database at: {db_path}")

    with sqlite3.connect(db_path) as conn:
        print("Farmers - product media and publishing workflow:")
        _add_column_if_missing(conn, "farmers", "product_description", "TEXT")
        _add_column_if_missing(conn, "farmers", "quantity_available", "INTEGER DEFAULT 0")
        _add_column_if_missing(conn, "farmers", "product_image_path", "TEXT")
        _add_column_if_missing(conn, "farmers", "listing_status", "TEXT DEFAULT 'DRAFT'")
        _add_column_if_missing(conn, "farmers", "listed_by_agent_phone", "TEXT")
        _add_column_if_missing(conn, "farmers", "listing_submitted_at", "TEXT")
        _add_column_if_missing(conn, "farmers", "listing_published_at", "TEXT")
        _add_column_if_missing(conn, "farmers", "listing_reviewed_by", "TEXT")
        _add_column_if_missing(conn, "farmers", "listing_rejection_reason", "TEXT")
        _add_column_if_missing(conn, "farmers", "listing_updated_at", "TEXT")
        conn.execute(
            """UPDATE farmers
               SET listing_status='PUBLISHED',
                   listing_published_at=COALESCE(listing_published_at, datetime('now'))
               WHERE price > 0
                 AND kyc_status='VERIFIED'
                 AND is_active=1
                 AND (listing_status IS NULL OR listing_status='')"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_farmers_listing_status ON farmers(listing_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_farmers_crop_status ON farmers(crop, listing_status)")

        print("Notifications:")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS notifications (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient_type TEXT NOT NULL,
                recipient_id   TEXT,
                phone          TEXT,
                email          TEXT,
                event_type     TEXT NOT NULL,
                channel        TEXT NOT NULL,
                subject        TEXT,
                message        TEXT NOT NULL,
                status         TEXT NOT NULL DEFAULT 'PENDING',
                error          TEXT,
                metadata       TEXT,
                created_at     TEXT DEFAULT (datetime('now')),
                sent_at        TEXT
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications(recipient_type, recipient_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_event ON notifications(event_type)")

        print("Disputes:")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS disputes (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                dispute_id       TEXT NOT NULL UNIQUE,
                txn_id           TEXT NOT NULL REFERENCES escrow_ledger(txn_id),
                raised_by_type   TEXT NOT NULL,
                raised_by_id     TEXT NOT NULL,
                reason           TEXT NOT NULL,
                details          TEXT,
                status           TEXT NOT NULL DEFAULT 'OPEN',
                resolution       TEXT,
                resolved_by      TEXT,
                created_at       TEXT DEFAULT (datetime('now')),
                resolved_at      TEXT
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_disputes_txn ON disputes(txn_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_disputes_status ON disputes(status)")

    print("[Sowtrust] Production MVP workflows ready.")


if __name__ == "__main__":
    migrate()
