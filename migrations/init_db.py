"""
AgriHub Global — Database Initialisation
Run once:  python migrations/init_db.py
"""
import sqlite3
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ─── FARMERS ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS farmers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    member_uuid   TEXT    NOT NULL UNIQUE DEFAULT (lower(hex(randomblob(16)))),
    name          TEXT    NOT NULL,
    phone         TEXT    NOT NULL UNIQUE,
    crop          TEXT    NOT NULL,
    location      TEXT    NOT NULL,
    pin_hash      TEXT    NOT NULL,
    price         REAL    DEFAULT 0.0,
    balance       REAL    DEFAULT 0.0,
    credit_score  INTEGER DEFAULT 0,
    kyc_status    TEXT    DEFAULT 'PENDING',   -- PENDING | VERIFIED | SUSPENDED
    is_active     INTEGER DEFAULT 1,
    created_at    TEXT    DEFAULT (datetime('now'))
);

-- ─── AGENTS ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    phone       TEXT    NOT NULL UNIQUE,
    pin_hash    TEXT    NOT NULL,
    location    TEXT    NOT NULL,
    recruits    INTEGER DEFAULT 0,
    balance     REAL    DEFAULT 0.0,
    is_active   INTEGER DEFAULT 1,
    created_at  TEXT    DEFAULT (datetime('now'))
);

-- ─── BUYERS ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS buyers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phone       TEXT    NOT NULL UNIQUE,
    name        TEXT,
    pin_hash    TEXT,
    balance     REAL    DEFAULT 0.0,
    created_at  TEXT    DEFAULT (datetime('now'))
);

-- ─── ESCROW LEDGER ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS escrow_ledger (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_id            TEXT    NOT NULL UNIQUE DEFAULT (upper(substr(hex(randomblob(8)),1,12))),
    farmer_phone      TEXT    NOT NULL REFERENCES farmers(phone),
    buyer_phone       TEXT    NOT NULL,
    crop              TEXT    NOT NULL,
    quantity_bags     INTEGER NOT NULL DEFAULT 1,
    amount            REAL    NOT NULL,
    service_fee       REAL    NOT NULL,
    release_code_hash TEXT    NOT NULL,
    status            TEXT    NOT NULL DEFAULT 'ESCROW_LOCKED',
                                -- ESCROW_LOCKED | DELIVERED | DISPUTED | EXPIRED | CANCELLED
    logistics_id      TEXT,
    locked_at         TEXT    DEFAULT (datetime('now')),
    released_at       TEXT,
    expires_at        TEXT    DEFAULT (datetime('now', '+72 hours'))
);

-- ─── BUYER REQUESTS ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS buyer_requests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_phone  TEXT    NOT NULL,
    crop         TEXT    NOT NULL,
    qty_bags     INTEGER NOT NULL DEFAULT 1,
    max_price    REAL,
    location     TEXT,
    status       TEXT    NOT NULL DEFAULT 'OPEN',  -- OPEN | MATCHED | CLOSED
    created_at   TEXT    DEFAULT (datetime('now'))
);

-- ─── LOGISTICS LOG ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS logistics_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    logistics_id        TEXT    NOT NULL UNIQUE DEFAULT (upper(substr(hex(randomblob(6)),1,8))),
    txn_id              TEXT    REFERENCES escrow_ledger(txn_id),
    courier_name        TEXT,
    courier_phone       TEXT,
    origin              TEXT,
    destination         TEXT,
    status              TEXT    DEFAULT 'PENDING',  -- PENDING | IN_TRANSIT | DELIVERED
    dispatched_at       TEXT,
    delivery_timestamp  TEXT,
    created_at          TEXT    DEFAULT (datetime('now'))
);

-- ─── AUDIT LOG ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor      TEXT    NOT NULL,
    action     TEXT    NOT NULL,
    details    TEXT,
    ip_address TEXT,
    created_at TEXT    DEFAULT (datetime('now'))
);

-- ─── INDEXES ───────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_farmers_phone    ON farmers(phone);
CREATE INDEX IF NOT EXISTS idx_escrow_status    ON escrow_ledger(status);
CREATE INDEX IF NOT EXISTS idx_escrow_buyer     ON escrow_ledger(buyer_phone);
CREATE INDEX IF NOT EXISTS idx_escrow_farmer    ON escrow_ledger(farmer_phone);
CREATE INDEX IF NOT EXISTS idx_requests_status  ON buyer_requests(status);
"""


def init_db():
    db_path = config.DATABASE_PATH
    print(f"[AgriHub] Initialising database at: {db_path}")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
    print("[AgriHub] ✅ Database schema created successfully.")


if __name__ == "__main__":
    init_db()
