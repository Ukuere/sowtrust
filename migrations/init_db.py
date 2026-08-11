"""
Sowtrust Global — Database Initialisation
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
    bank_code           TEXT,               -- Paystack bank code, e.g. "50515" for Moniepoint MFB
    bank_account_number TEXT,               -- 10-digit NUBAN (can be a digital wallet: OPay/Kuda/PalmPay etc.)
    bank_account_name   TEXT,               -- name returned by Paystack account resolution — must match farmer name
    bank_verified_at    TEXT,               -- set only after successful resolve_account_number match
    created_at    TEXT    DEFAULT (datetime('now'))
);

-- ─── PRODUCTS (dynamic catalog — farmers list any crop by name) ────────────
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    name_lower  TEXT    NOT NULL UNIQUE,
    created_at  TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_products_name_lower ON products(name_lower);

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
    status            TEXT    NOT NULL DEFAULT 'PENDING_PAYMENT',
                                -- PENDING_PAYMENT | ESCROW_LOCKED | DELIVERED |
                                -- DISPUTED | EXPIRED | CANCELLED | PAYOUT_FAILED
    logistics_id      TEXT,
    -- Buyer → Sowtrust collection (Paystack "Charge" via bank transfer)
    payment_reference     TEXT    UNIQUE,   -- Paystack transaction reference for buyer's payment
    virtual_account_number TEXT,            -- one-time NUBAN buyer transfers to
    virtual_account_bank   TEXT,            -- e.g. "Wema Bank"
    payment_confirmed_at   TEXT,
    -- Sowtrust → Farmer payout (Paystack "Transfer")
    payout_reference       TEXT    UNIQUE,  -- Paystack transfer_code
    payout_status           TEXT,           -- pending | success | failed | reversed
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
    print(f"[Sowtrust] Initialising database at: {db_path}")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
    print("[Sowtrust] ✅ Database schema created successfully.")


if __name__ == "__main__":
    init_db()
