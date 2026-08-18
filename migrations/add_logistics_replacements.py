"""Add the audited provider-replacement workflow for locked quotes."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import config


def migrate():
    print(f"[Sowtrust] Migrating logistics replacements at: {config.DATABASE_PATH}")
    with sqlite3.connect(config.DATABASE_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS logistics_quote_replacements (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id                 TEXT NOT NULL,
                quote_id                 INTEGER NOT NULL,
                proposed_provider_id     INTEGER NOT NULL,
                proposed_amount          REAL NOT NULL,
                proposed_amount_kobo     INTEGER NOT NULL,
                status                   TEXT NOT NULL,
                requested_by             TEXT NOT NULL,
                reason                   TEXT,
                requested_at             TEXT DEFAULT (datetime('now')),
                buyer_approved_at        TEXT,
                applied_at               TEXT,
                FOREIGN KEY(order_id) REFERENCES escrow_ledger(txn_id),
                FOREIGN KEY(quote_id) REFERENCES logistics_quotes(id),
                FOREIGN KEY(proposed_provider_id) REFERENCES logistics_providers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_quote_replacements_order
                ON logistics_quote_replacements(order_id, status);
            """
        )
    print("[Sowtrust] Logistics replacement workflow ready.")


if __name__ == "__main__":
    migrate()
