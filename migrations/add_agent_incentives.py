"""Add the configurable, auditable agent incentive ledger.

Amounts are stored in integer kobo. This is an internal earnings ledger,
not a stored-value wallet or customer payment account.
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.settings import config


DEFAULT_POLICIES = (
    (
        "FARMER_VERIFIED", "Farmer verified",
        "Reward an attributed agent after a farmer passes verification.",
        "FARMER_VERIFIED", 50_000, 1, None, None,
    ),
    (
        "FIRST_LISTING_APPROVED", "First listing approved",
        "Reward the attributed listing agent after the first qualifying listing is approved.",
        "LISTING_APPROVED", 25_000, 1, None, None,
    ),
    (
        "FIRST_TRANSACTION_COMPLETED", "First transaction completed",
        "Reward the attributed agent after the farmer's first fully settled order.",
        "ORDER_COMPLETED", 75_000, 1, 1, None,
    ),
    (
        "FARMER_RETENTION_BONUS", "Farmer retention bonus",
        "Reward the attributed agent when the farmer reaches the configured retention milestone.",
        "RETENTION_MILESTONE_REACHED", 50_000, 1, 2, 90,
    ),
)


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate():
    print(f"[Sowtrust] Migrating agent incentive ledger at: {config.DATABASE_PATH}")
    with sqlite3.connect(config.DATABASE_PATH) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        if "completed_at" not in _columns(conn, "escrow_ledger"):
            conn.execute("ALTER TABLE escrow_ledger ADD COLUMN completed_at TEXT")
            print("  + added escrow_ledger.completed_at")

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS agent_incentive_policies (
                id                           INTEGER PRIMARY KEY AUTOINCREMENT,
                incentive_code               TEXT NOT NULL UNIQUE,
                name                         TEXT NOT NULL,
                description                  TEXT,
                event_type                   TEXT NOT NULL,
                amount_kobo                  INTEGER NOT NULL CHECK (amount_kobo >= 0),
                currency                     TEXT NOT NULL DEFAULT 'NGN',
                enabled                      INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                effective_from               TEXT,
                effective_to                 TEXT,
                max_occurrences_per_farmer   INTEGER NOT NULL DEFAULT 1
                                               CHECK (max_occurrences_per_farmer > 0),
                requires_admin_review        INTEGER NOT NULL DEFAULT 1
                                               CHECK (requires_admin_review IN (0, 1)),
                qualifying_transaction_count INTEGER,
                qualifying_period_days       INTEGER,
                created_at                   TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at                   TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS agent_farmer_relationships (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id          INTEGER NOT NULL REFERENCES agents(id),
                farmer_id         INTEGER NOT NULL REFERENCES farmers(id),
                relationship_type TEXT NOT NULL,
                assigned_at       TEXT NOT NULL DEFAULT (datetime('now')),
                verified_at       TEXT,
                active            INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                created_by        TEXT NOT NULL,
                created_at        TEXT NOT NULL DEFAULT (datetime('now')),
                ended_at          TEXT,
                UNIQUE(agent_id, farmer_id, relationship_type)
            );

            CREATE TABLE IF NOT EXISTS agent_payout_batches (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                period_start      TEXT NOT NULL,
                period_end        TEXT NOT NULL,
                frequency         TEXT NOT NULL DEFAULT 'WEEKLY'
                                      CHECK (frequency IN ('WEEKLY', 'BIWEEKLY', 'MONTHLY', 'CUSTOM')),
                status            TEXT NOT NULL DEFAULT 'DRAFT'
                                      CHECK (status IN ('DRAFT', 'UNDER_REVIEW', 'APPROVED',
                                                        'PROCESSING', 'PAID', 'FAILED', 'CANCELLED')),
                total_amount_kobo INTEGER NOT NULL DEFAULT 0,
                currency          TEXT NOT NULL DEFAULT 'NGN',
                created_by        TEXT NOT NULL,
                created_at        TEXT NOT NULL DEFAULT (datetime('now')),
                approved_by       TEXT,
                approved_at       TEXT,
                paid_at           TEXT,
                payment_reference TEXT,
                notes             TEXT
            );

            CREATE TABLE IF NOT EXISTS agent_ledger_entries (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id            INTEGER NOT NULL REFERENCES agents(id),
                farmer_id           INTEGER REFERENCES farmers(id),
                listing_id          INTEGER,
                order_id            TEXT REFERENCES escrow_ledger(txn_id),
                incentive_policy_id INTEGER NOT NULL REFERENCES agent_incentive_policies(id),
                incentive_code      TEXT NOT NULL,
                description         TEXT NOT NULL,
                amount_kobo         INTEGER NOT NULL,
                currency            TEXT NOT NULL DEFAULT 'NGN',
                status              TEXT NOT NULL DEFAULT 'PENDING'
                                      CHECK (status IN ('PENDING', 'UNDER_REVIEW', 'APPROVED',
                                                        'REJECTED', 'PAYABLE', 'PAID', 'REVERSED')),
                source_event        TEXT NOT NULL,
                source_reference    TEXT,
                created_at          TEXT NOT NULL DEFAULT (datetime('now')),
                approved_at         TEXT,
                approved_by         TEXT,
                rejected_at         TEXT,
                rejected_by         TEXT,
                rejection_reason    TEXT,
                paid_at             TEXT,
                payout_batch_id     INTEGER REFERENCES agent_payout_batches(id),
                reversal_of         INTEGER REFERENCES agent_ledger_entries(id),
                metadata_json       TEXT,
                risk_flags          TEXT,
                idempotency_key     TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS agent_payout_items (
                id                           INTEGER PRIMARY KEY AUTOINCREMENT,
                payout_batch_id              INTEGER NOT NULL REFERENCES agent_payout_batches(id),
                agent_id                     INTEGER NOT NULL REFERENCES agents(id),
                gross_approved_earnings_kobo INTEGER NOT NULL DEFAULT 0,
                deductions_kobo              INTEGER NOT NULL DEFAULT 0,
                net_payout_amount_kobo       INTEGER NOT NULL DEFAULT 0,
                status                       TEXT NOT NULL DEFAULT 'PENDING'
                                               CHECK (status IN ('PENDING', 'PROCESSING', 'PAID',
                                                                 'FAILED', 'CANCELLED')),
                payment_reference            TEXT,
                paid_at                      TEXT,
                created_at                   TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(payout_batch_id, agent_id)
            );

            CREATE INDEX IF NOT EXISTS idx_agent_relationship_farmer
                ON agent_farmer_relationships(farmer_id, active, relationship_type);
            CREATE INDEX IF NOT EXISTS idx_agent_relationship_agent
                ON agent_farmer_relationships(agent_id, active);
            CREATE INDEX IF NOT EXISTS idx_agent_ledger_agent_status
                ON agent_ledger_entries(agent_id, status, created_at);
            CREATE INDEX IF NOT EXISTS idx_agent_ledger_farmer_code
                ON agent_ledger_entries(farmer_id, incentive_code);
            CREATE INDEX IF NOT EXISTS idx_agent_ledger_order
                ON agent_ledger_entries(order_id);
            CREATE INDEX IF NOT EXISTS idx_agent_payout_batch_status
                ON agent_payout_batches(status, period_end);

            CREATE TRIGGER IF NOT EXISTS trg_agent_ledger_no_delete
            BEFORE DELETE ON agent_ledger_entries
            BEGIN
                SELECT RAISE(ABORT, 'Agent incentive ledger entries cannot be deleted');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_agent_ledger_immutable_fields
            BEFORE UPDATE OF agent_id, farmer_id, listing_id, order_id,
                             incentive_policy_id, incentive_code, description,
                             amount_kobo, currency, source_event, source_reference,
                             created_at, reversal_of, idempotency_key
            ON agent_ledger_entries
            BEGIN
                SELECT RAISE(ABORT, 'Immutable agent incentive ledger fields cannot be changed');
            END;
            """
        )

        for policy in DEFAULT_POLICIES:
            conn.execute(
                """INSERT OR IGNORE INTO agent_incentive_policies
                   (incentive_code, name, description, event_type, amount_kobo,
                    max_occurrences_per_farmer, qualifying_transaction_count,
                    qualifying_period_days, requires_admin_review)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                policy,
            )

    print("[Sowtrust] Agent incentive ledger ready.")


if __name__ == "__main__":
    migrate()
