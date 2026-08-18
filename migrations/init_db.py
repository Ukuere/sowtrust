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
    normalized_phone TEXT UNIQUE,
    registration_channel TEXT DEFAULT 'LEGACY',
    verification_status TEXT DEFAULT 'UNVERIFIED',
    account_status TEXT DEFAULT 'ACTIVE',
    phone_verified INTEGER DEFAULT 0,
    crop          TEXT    NOT NULL,
    location      TEXT    NOT NULL,
    pin_hash      TEXT    NOT NULL,
    price         REAL    DEFAULT 0.0,
    product_description TEXT,
    quantity_available INTEGER DEFAULT 0,
    product_image_path TEXT,
    listing_status TEXT DEFAULT 'DRAFT', -- DRAFT | PUBLISHED | SOLD | EXPIRED | SUSPENDED
    image_uploaded_by TEXT,
    image_uploaded_at TEXT,
    listed_by_agent_phone TEXT,
    listing_submitted_at TEXT,
    listing_published_at TEXT,
    listing_reviewed_by TEXT,
    listing_rejection_reason TEXT,
    listing_updated_at TEXT,
    balance       REAL    DEFAULT 0.0,
    credit_score  INTEGER DEFAULT 0,
    kyc_status    TEXT    DEFAULT 'PENDING',   -- PENDING | VERIFIED | SUSPENDED
    is_active     INTEGER DEFAULT 1,
    bank_code           TEXT,               -- Paystack bank code, e.g. "50515" for Moniepoint MFB
    bank_account_number TEXT,               -- 10-digit NUBAN (can be a digital wallet: OPay/Kuda/PalmPay etc.)
    bank_account_name   TEXT,               -- name returned by Paystack account resolution — must match farmer name
    bank_verified_at    TEXT,               -- set only after successful resolve_account_number match
    created_at    TEXT    DEFAULT (datetime('now'))
    ,updated_at   TEXT    DEFAULT (datetime('now'))
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
    normalized_phone TEXT UNIQUE,
    registration_channel TEXT DEFAULT 'LEGACY',
    verification_status TEXT DEFAULT 'UNVERIFIED',
    account_status TEXT DEFAULT 'ACTIVE',
    phone_verified INTEGER DEFAULT 0,
    pin_hash    TEXT    NOT NULL,
    location    TEXT    NOT NULL,
    recruits    INTEGER DEFAULT 0,
    balance     REAL    DEFAULT 0.0,
    is_active   INTEGER DEFAULT 1,
    created_at  TEXT    DEFAULT (datetime('now')),
    updated_at  TEXT    DEFAULT (datetime('now'))
);

-- ─── BUYERS ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS buyers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phone       TEXT    NOT NULL UNIQUE,
    normalized_phone TEXT UNIQUE,
    registration_channel TEXT DEFAULT 'LEGACY',
    verification_status TEXT DEFAULT 'UNVERIFIED',
    account_status TEXT DEFAULT 'ACTIVE',
    phone_verified INTEGER DEFAULT 0,
    name        TEXT,
    pin_hash    TEXT,
    business_name TEXT,
    email       TEXT,
    password_hash TEXT,
    address     TEXT,
    delivery_address TEXT,
    city        TEXT,
    state       TEXT,
    buyer_type  TEXT,
    email_verified INTEGER DEFAULT 0,
    email_verification_token TEXT,
    email_verification_sent_at TEXT,
    id_type     TEXT,
    id_number   TEXT,
    id_document_path TEXT,
    business_reg_number TEXT,
    business_reg_document_path TEXT,
    authorized_rep_name TEXT,
    authorized_rep_id_number TEXT,
    kyc_status  TEXT DEFAULT 'REGISTERED',
    kyc_submitted_at TEXT,
    kyc_reviewed_at TEXT,
    kyc_rejection_reason TEXT,
    is_active   INTEGER DEFAULT 1,
    balance     REAL    DEFAULT 0.0,
    created_at  TEXT    DEFAULT (datetime('now')),
    updated_at  TEXT    DEFAULT (datetime('now'))
);

-- ─── PLATFORM CONFIG (configurable fee percentages) ────────────────────────
CREATE TABLE IF NOT EXISTS platform_config (
    id                     INTEGER PRIMARY KEY CHECK (id = 1),
    buyer_fee_percent      REAL NOT NULL DEFAULT 2.5,
    seller_fee_percent     REAL NOT NULL DEFAULT 2.5,
    logistics_fee_percent  REAL NOT NULL DEFAULT 2.5,
    updated_at             TEXT DEFAULT (datetime('now')),
    updated_by             TEXT
);

-- ─── ESCROW LEDGER ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS escrow_ledger (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_id            TEXT    NOT NULL UNIQUE DEFAULT (upper(substr(hex(randomblob(8)),1,12))),
    farmer_phone      TEXT    NOT NULL REFERENCES farmers(phone),
    buyer_phone       TEXT    NOT NULL,
    crop              TEXT    NOT NULL,
    quantity_bags     INTEGER NOT NULL DEFAULT 1,
    amount            REAL    NOT NULL,   -- legacy alias, kept in sync with product_amount
    service_fee       REAL    NOT NULL,   -- legacy alias, kept in sync with seller_platform_fee
    -- Three-sided fee model — see app/services/fee_service.py
    product_amount               REAL,
    buyer_platform_fee           REAL,
    seller_platform_fee          REAL,
    logistics_amount             REAL DEFAULT 0,
    logistics_platform_fee       REAL DEFAULT 0,
    buyer_total                  REAL,   -- what the buyer actually pays (product+buyer_fee+logistics)
    farmer_settlement_amount     REAL,   -- what the farmer actually receives
    logistics_settlement_amount  REAL DEFAULT 0,
    sowtrust_total_revenue       REAL,   -- buyer_fee + seller_fee + logistics_fee
    product_amount_kobo               INTEGER,
    buyer_platform_fee_kobo           INTEGER,
    seller_platform_fee_kobo          INTEGER,
    logistics_amount_kobo             INTEGER DEFAULT 0,
    logistics_platform_fee_kobo       INTEGER DEFAULT 0,
    buyer_total_kobo                  INTEGER,
    farmer_settlement_amount_kobo     INTEGER,
    logistics_settlement_amount_kobo  INTEGER DEFAULT 0,
    sowtrust_total_revenue_kobo       INTEGER,
    amount_paid_kobo                  INTEGER,
    buyer_name        TEXT,
    delivery_address  TEXT,
    delivery_city     TEXT,
    delivery_state    TEXT,
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

-- ─── LOGISTICS PROVIDERS ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS logistics_providers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_uuid   TEXT NOT NULL UNIQUE DEFAULT (lower(hex(randomblob(16)))),
    name            TEXT NOT NULL,
    business_name   TEXT,
    phone           TEXT NOT NULL UNIQUE,
    normalized_phone TEXT UNIQUE,
    registration_channel TEXT DEFAULT 'LEGACY',
    verification_status TEXT DEFAULT 'UNVERIFIED',
    account_status TEXT DEFAULT 'ACTIVE',
    phone_verified INTEGER DEFAULT 0,
    email           TEXT,
    address         TEXT,
    operating_area  TEXT,
    pin_hash        TEXT NOT NULL,
    vehicle_type         TEXT,
    vehicle_registration TEXT,
    vehicle_capacity_kg  REAL,
    bank_code            TEXT,
    bank_account_number  TEXT,
    bank_account_name    TEXT,
    bank_verified_at     TEXT,
    kyc_status      TEXT DEFAULT 'PENDING',   -- PENDING | VERIFIED | SUSPENDED
    is_active       INTEGER DEFAULT 1,
    completed_jobs  INTEGER DEFAULT 0,
    rating          REAL DEFAULT 0.0,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_logistics_providers_phone ON logistics_providers(phone);

-- ─── UNIFIED CROSS-CHANNEL IDENTITY ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_phone           TEXT NOT NULL UNIQUE,
    full_name                  TEXT,
    password_hash              TEXT,
    account_status             TEXT NOT NULL DEFAULT 'ACTIVE',
    phone_verified             INTEGER NOT NULL DEFAULT 0,
    first_registration_channel TEXT NOT NULL DEFAULT 'LEGACY',
    last_login_at              TEXT,
    created_at                 TEXT DEFAULT (datetime('now')),
    updated_at                 TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_users_account_status ON users(account_status);
CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at);

CREATE TABLE IF NOT EXISTS user_roles (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL REFERENCES users(id),
    role                 TEXT NOT NULL,
    profile_table        TEXT NOT NULL,
    profile_record_id    INTEGER,
    registration_channel TEXT NOT NULL DEFAULT 'LEGACY',
    verification_status  TEXT NOT NULL DEFAULT 'UNVERIFIED',
    account_status       TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at           TEXT DEFAULT (datetime('now')),
    updated_at           TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, role)
);
CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_roles(role);
CREATE INDEX IF NOT EXISTS idx_user_roles_channel ON user_roles(registration_channel);
CREATE INDEX IF NOT EXISTS idx_user_roles_verification ON user_roles(verification_status);

CREATE TABLE IF NOT EXISTS auth_otps (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id),
    role           TEXT NOT NULL,
    purpose        TEXT NOT NULL,
    code_hash      TEXT NOT NULL,
    attempts       INTEGER NOT NULL DEFAULT 0,
    max_attempts   INTEGER NOT NULL DEFAULT 5,
    requested_ip   TEXT,
    expires_at     TEXT NOT NULL,
    consumed_at    TEXT,
    created_at     TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_auth_otps_lookup ON auth_otps(user_id, role, purpose, created_at);

CREATE TABLE IF NOT EXISTS identity_migration_issues (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_type       TEXT NOT NULL,
    role             TEXT,
    table_name       TEXT,
    record_id        INTEGER,
    raw_phone        TEXT,
    normalized_phone TEXT,
    details          TEXT,
    resolved_at      TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS staff_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'ADMIN',
    is_active     INTEGER NOT NULL DEFAULT 1,
    last_login_at TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS request_rate_limits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_key  TEXT NOT NULL,
    occurred_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_request_rate_limits_bucket
    ON request_rate_limits(bucket_key, occurred_at);

CREATE TABLE IF NOT EXISTS listing_moderation_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    farmer_phone    TEXT NOT NULL,
    previous_status TEXT,
    new_status      TEXT NOT NULL,
    reason          TEXT,
    actor           TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_listing_moderation_farmer
    ON listing_moderation_log(farmer_phone, created_at);

-- ─── DELIVERY CODE ATTEMPTS (audit trail for every verification try) ───────
CREATE TABLE IF NOT EXISTS delivery_code_attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    logistics_id  TEXT,
    txn_id        TEXT,
    attempted_by  TEXT,
    success       INTEGER NOT NULL DEFAULT 0,
    reason        TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

-- ─── LOGISTICS LOG ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS logistics_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    logistics_id        TEXT    NOT NULL UNIQUE DEFAULT (upper(substr(hex(randomblob(6)),1,8))),
    txn_id              TEXT    REFERENCES escrow_ledger(txn_id),
    provider_id         INTEGER REFERENCES logistics_providers(id),
    courier_name        TEXT,   -- legacy, superseded by provider_id
    courier_phone       TEXT,   -- legacy, superseded by provider_id
    origin              TEXT,
    destination         TEXT,
    status              TEXT    DEFAULT 'PENDING',
                                -- PENDING | QUOTED | ASSIGNED | IN_TRANSIT | DELIVERED
    quote_amount        REAL,
    platform_fee        REAL,
    settlement_amount   REAL,
    quote_amount_kobo   INTEGER,
    platform_fee_kobo   INTEGER,
    settlement_amount_kobo INTEGER,
    delivery_code_hash     TEXT,
    delivery_code_used_at  TEXT,
    payout_reference       TEXT,
    payout_status          TEXT,
    confirmed_at           TEXT,
    dispatched_at       TEXT,
    delivery_timestamp  TEXT,
    created_at          TEXT    DEFAULT (datetime('now'))
);

-- ─── USSD SESSIONS (must be shared across gunicorn workers) ────────────────
-- Logistics quote source of truth: quote-before-payment workflow
CREATE TABLE IF NOT EXISTS logistics_quotes (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id              TEXT NOT NULL UNIQUE REFERENCES escrow_ledger(txn_id),
    pickup_location       TEXT NOT NULL,
    delivery_location     TEXT NOT NULL,
    product_name          TEXT NOT NULL,
    quantity              INTEGER NOT NULL DEFAULT 1,
    logistics_provider_id INTEGER REFERENCES logistics_providers(id),
    quoted_amount         REAL,
    commission_rate       REAL DEFAULT 2.5,
    commission_amount     REAL,
    provider_net_amount   REAL,
    quoted_amount_kobo    INTEGER,
    commission_amount_kobo INTEGER,
    provider_net_amount_kobo INTEGER,
    status                TEXT NOT NULL DEFAULT 'PENDING',
    quoted_by             TEXT,
    created_at            TEXT DEFAULT (datetime('now')),
    accepted_at           TEXT,
    expires_at            TEXT,
    buyer_accepted_at     TEXT,
    locked_at             TEXT
);
CREATE INDEX IF NOT EXISTS idx_logistics_quotes_order ON logistics_quotes(order_id);
CREATE INDEX IF NOT EXISTS idx_logistics_quotes_status ON logistics_quotes(status);

CREATE TABLE IF NOT EXISTS logistics_quote_replacements (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id                 TEXT NOT NULL REFERENCES escrow_ledger(txn_id),
    quote_id                 INTEGER NOT NULL REFERENCES logistics_quotes(id),
    proposed_provider_id     INTEGER NOT NULL REFERENCES logistics_providers(id),
    proposed_amount          REAL NOT NULL,
    proposed_amount_kobo     INTEGER NOT NULL,
    status                   TEXT NOT NULL,
    requested_by             TEXT NOT NULL,
    reason                   TEXT,
    requested_at             TEXT DEFAULT (datetime('now')),
    buyer_approved_at        TEXT,
    applied_at               TEXT
);
CREATE INDEX IF NOT EXISTS idx_quote_replacements_order
    ON logistics_quote_replacements(order_id, status);

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
CREATE INDEX IF NOT EXISTS idx_payment_webhook_status ON payment_webhook_events(status, received_at);

CREATE TABLE IF NOT EXISTS ussd_sessions (
    phone        TEXT PRIMARY KEY,
    data         TEXT NOT NULL,
    last_active  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_last_active ON ussd_sessions(last_active);

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
CREATE INDEX IF NOT EXISTS idx_farmers_listing_status ON farmers(listing_status);
CREATE INDEX IF NOT EXISTS idx_farmers_crop_status ON farmers(crop, listing_status);
CREATE INDEX IF NOT EXISTS idx_escrow_status    ON escrow_ledger(status);
CREATE INDEX IF NOT EXISTS idx_escrow_buyer     ON escrow_ledger(buyer_phone);
CREATE INDEX IF NOT EXISTS idx_escrow_farmer    ON escrow_ledger(farmer_phone);
CREATE INDEX IF NOT EXISTS idx_requests_status  ON buyer_requests(status);

-- Notifications generated by central NotificationService
CREATE TABLE IF NOT EXISTS notifications (
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
);
CREATE INDEX IF NOT EXISTS idx_notifications_recipient ON notifications(recipient_type, recipient_id);
CREATE INDEX IF NOT EXISTS idx_notifications_event ON notifications(event_type);

-- Buyer/admin dispute workflow
CREATE TABLE IF NOT EXISTS disputes (
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
);
CREATE INDEX IF NOT EXISTS idx_disputes_txn ON disputes(txn_id);
CREATE INDEX IF NOT EXISTS idx_disputes_status ON disputes(status);
"""


def init_db():
    db_path = config.DATABASE_PATH
    print(f"[Sowtrust] Initialising database at: {db_path}")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.execute("""
            INSERT OR IGNORE INTO platform_config (id, buyer_fee_percent, seller_fee_percent, logistics_fee_percent)
            VALUES (1, 2.5, 2.5, 2.5)
        """)
    print("[Sowtrust] ✅ Database schema created successfully.")


if __name__ == "__main__":
    init_db()
