"""
Sowtrust - Migration: Quote-before-payment logistics workflow.

Adds a first-class logistics_quotes table so operations-assisted quotes
can be requested, selected, locked, and explicitly accepted by the buyer
before any Paystack payment is initialized.

Safe to run multiple times.
"""
import sqlite3
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import config


def migrate():
    db_path = config.DATABASE_PATH
    print(f"[Sowtrust] Migrating database at: {db_path}")

    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logistics_quotes (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id              TEXT NOT NULL UNIQUE,
                pickup_location       TEXT NOT NULL,
                delivery_location     TEXT NOT NULL,
                product_name          TEXT NOT NULL,
                quantity              INTEGER NOT NULL DEFAULT 1,
                logistics_provider_id INTEGER,
                quoted_amount         REAL,
                commission_rate       REAL DEFAULT 2.5,
                commission_amount     REAL,
                provider_net_amount   REAL,
                status                TEXT NOT NULL DEFAULT 'PENDING',
                                      -- PENDING | QUOTED | SELECTED | BUYER_ACCEPTED |
                                      -- LOCKED | EXPIRED | REJECTED | CANCELLED
                quoted_by             TEXT,
                created_at            TEXT DEFAULT (datetime('now')),
                accepted_at           TEXT,
                expires_at            TEXT,
                buyer_accepted_at     TEXT,
                locked_at             TEXT,
                FOREIGN KEY(order_id) REFERENCES escrow_ledger(txn_id),
                FOREIGN KEY(logistics_provider_id) REFERENCES logistics_providers(id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_logistics_quotes_order ON logistics_quotes(order_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_logistics_quotes_status ON logistics_quotes(status)"
        )

    print("[Sowtrust] Logistics quote workflow ready.")


if __name__ == "__main__":
    migrate()
