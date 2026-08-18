"""Add integer-kobo ledger columns and idempotent webhook event records."""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import config


def _add(conn, table, column, definition="INTEGER"):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        print(f"  + added {table}.{column}")


def migrate():
    print(f"[Sowtrust] Migrating payment integrity at: {config.DATABASE_PATH}")
    with sqlite3.connect(config.DATABASE_PATH) as conn:
        escrow_columns = (
            "product_amount_kobo", "buyer_platform_fee_kobo",
            "seller_platform_fee_kobo", "logistics_amount_kobo",
            "logistics_platform_fee_kobo", "buyer_total_kobo",
            "farmer_settlement_amount_kobo", "logistics_settlement_amount_kobo",
            "sowtrust_total_revenue_kobo", "amount_paid_kobo",
        )
        for column in escrow_columns:
            _add(conn, "escrow_ledger", column)

        backfill = {
            "product_amount_kobo": "COALESCE(product_amount, amount)",
            "buyer_platform_fee_kobo": "buyer_platform_fee",
            "seller_platform_fee_kobo": "COALESCE(seller_platform_fee, service_fee)",
            "logistics_amount_kobo": "logistics_amount",
            "logistics_platform_fee_kobo": "logistics_platform_fee",
            "buyer_total_kobo": "buyer_total",
            "farmer_settlement_amount_kobo": "farmer_settlement_amount",
            "logistics_settlement_amount_kobo": "logistics_settlement_amount",
            "sowtrust_total_revenue_kobo": "sowtrust_total_revenue",
        }
        for column, source in backfill.items():
            conn.execute(
                f"""UPDATE escrow_ledger SET {column}=CAST(ROUND(({source}) * 100) AS INTEGER)
                    WHERE {column} IS NULL AND ({source}) IS NOT NULL"""
            )

        for column in ("quoted_amount_kobo", "commission_amount_kobo", "provider_net_amount_kobo"):
            _add(conn, "logistics_quotes", column)
        conn.execute(
            """UPDATE logistics_quotes SET
                 quoted_amount_kobo=COALESCE(quoted_amount_kobo, CAST(ROUND(quoted_amount*100) AS INTEGER)),
                 commission_amount_kobo=COALESCE(commission_amount_kobo, CAST(ROUND(commission_amount*100) AS INTEGER)),
                 provider_net_amount_kobo=COALESCE(provider_net_amount_kobo, CAST(ROUND(provider_net_amount*100) AS INTEGER))"""
        )

        for column in ("quote_amount_kobo", "platform_fee_kobo", "settlement_amount_kobo"):
            _add(conn, "logistics_log", column)
        conn.execute(
            """UPDATE logistics_log SET
                 quote_amount_kobo=COALESCE(quote_amount_kobo, CAST(ROUND(quote_amount*100) AS INTEGER)),
                 platform_fee_kobo=COALESCE(platform_fee_kobo, CAST(ROUND(platform_fee*100) AS INTEGER)),
                 settlement_amount_kobo=COALESCE(settlement_amount_kobo, CAST(ROUND(settlement_amount*100) AS INTEGER))"""
        )

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS payment_webhook_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key    TEXT NOT NULL UNIQUE,
                event_type   TEXT NOT NULL,
                reference    TEXT,
                payload_hash TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'RECEIVED',
                error        TEXT,
                received_at  TEXT DEFAULT (datetime('now')),
                processed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_payment_webhook_status
                ON payment_webhook_events(status, received_at);
            """
        )
    print("[Sowtrust] Payment integrity migration ready.")


if __name__ == "__main__":
    migrate()
