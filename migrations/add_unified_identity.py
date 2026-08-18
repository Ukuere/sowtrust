"""Additive migration for cross-channel identity, OTP and listing moderation.

Existing role records remain in place. The migration links them to one
canonical ``users`` record by normalized phone and records unsafe merge
cases for operations rather than deleting or combining data automatically.
"""
from collections import defaultdict
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.settings import config


ROLE_TABLES = {
    "FARMER": "farmers",
    "BUYER": "buyers",
    "AGENT": "agents",
    "LOGISTICS": "logistics_providers",
}


def normalize_phone(value):
    """Migration-local normalizer so schema upgrades do not import Flask."""
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        digits = "234" + digits[1:]
    elif len(digits) == 10 and digits[0] in "789":
        digits = "234" + digits
    if not re.fullmatch(r"234[789]\d{9}", digits):
        return None
    return f"+{digits}"


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn, table, name, definition):
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        print(f"  + added {table}.{name}")


def _table_exists(conn, table):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def _verification_status(row, columns):
    raw = (row["kyc_status"] if "kyc_status" in columns else "") or ""
    raw = str(raw).upper()
    if raw == "VERIFIED":
        return "VERIFIED"
    if raw in {"REJECTED", "SUSPENDED"}:
        return raw
    return "PENDING" if raw in {"PENDING", "UNDER_REVIEW", "KYC_PENDING"} else "UNVERIFIED"


def _registration_channel(conn, phone, role):
    actions = {
        "FARMER": "FARMER_REGISTERED",
        "AGENT": "AGENT_REGISTERED",
        "LOGISTICS": "LOGISTICS_PROVIDER_REGISTERED",
        "BUYER": "BUYER_REGISTERED",
    }
    if not _table_exists(conn, "audit_log"):
        return "LEGACY"
    found = conn.execute(
        "SELECT 1 FROM audit_log WHERE actor=? AND action=? LIMIT 1",
        (phone, actions[role]),
    ).fetchone()
    return "USSD" if found else "LEGACY"


def _record_issue(conn, issue_type, role, table_name, record_id, raw_phone,
                  normalized_phone, details):
    conn.execute(
        """INSERT INTO identity_migration_issues
           (issue_type, role, table_name, record_id, raw_phone,
            normalized_phone, details)
           SELECT ?, ?, ?, ?, ?, ?, ?
           WHERE NOT EXISTS (
             SELECT 1 FROM identity_migration_issues
             WHERE issue_type=? AND table_name=? AND record_id=?
               AND resolved_at IS NULL
           )""",
        (
            issue_type, role, table_name, record_id, raw_phone,
            normalized_phone, details, issue_type, table_name, record_id,
        ),
    )


def _prepare_role_table(conn, table):
    _add_column(conn, table, "normalized_phone", "TEXT")
    _add_column(conn, table, "registration_channel", "TEXT DEFAULT 'LEGACY'")
    _add_column(conn, table, "verification_status", "TEXT DEFAULT 'UNVERIFIED'")
    _add_column(conn, table, "account_status", "TEXT DEFAULT 'ACTIVE'")
    _add_column(conn, table, "phone_verified", "INTEGER DEFAULT 0")
    _add_column(conn, table, "updated_at", "TEXT")


def _backfill_role(conn, role, table):
    columns = _columns(conn, table)
    select_columns = ["id", "phone"]
    for optional in ("name", "kyc_status", "is_active", "created_at"):
        if optional in columns:
            select_columns.append(optional)
    rows = conn.execute(
        f"SELECT {', '.join(select_columns)} FROM {table} ORDER BY id ASC"
    ).fetchall()

    grouped = defaultdict(list)
    for row in rows:
        normalized = normalize_phone(row["phone"])
        if not normalized:
            _record_issue(
                conn, "INVALID_PHONE", role, table, row["id"], row["phone"],
                None, "Phone could not be normalized; record was not linked.",
            )
            continue
        grouped[normalized].append(row)

    for normalized, matches in grouped.items():
        primary = matches[0]
        if len(matches) > 1:
            for duplicate in matches[1:]:
                _record_issue(
                    conn, "DUPLICATE_ROLE_PHONE", role, table, duplicate["id"],
                    duplicate["phone"], normalized,
                    f"Primary {table} record is id={primary['id']}; manual merge required.",
                )

        channel = _registration_channel(conn, primary["phone"], role)
        verified = 1 if channel == "USSD" else 0
        verification = _verification_status(primary, columns)
        is_active = primary["is_active"] if "is_active" in columns else 1
        account_status = "ACTIVE" if is_active else "SUSPENDED"
        full_name = primary["name"] if "name" in columns else None

        conn.execute(
            f"""UPDATE {table}
                SET normalized_phone=?, registration_channel=COALESCE(registration_channel, ?),
                    verification_status=?, account_status=?,
                    phone_verified=MAX(COALESCE(phone_verified, 0), ?),
                    updated_at=COALESCE(updated_at, datetime('now'))
                WHERE id=?""",
            (normalized, channel, verification, account_status, verified, primary["id"]),
        )

        conn.execute(
            """INSERT INTO users
               (normalized_phone, full_name, account_status, phone_verified,
                first_registration_channel, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, COALESCE(?, datetime('now')), datetime('now'))
               ON CONFLICT(normalized_phone) DO UPDATE SET
                 full_name=COALESCE(users.full_name, excluded.full_name),
                 phone_verified=MAX(users.phone_verified, excluded.phone_verified),
                 updated_at=datetime('now')""",
            (normalized, full_name, account_status, verified, channel,
             primary["created_at"] if "created_at" in columns else None),
        )
        user_id = conn.execute(
            "SELECT id FROM users WHERE normalized_phone=?", (normalized,)
        ).fetchone()[0]
        conn.execute(
            """INSERT INTO user_roles
               (user_id, role, profile_table, profile_record_id,
                registration_channel, verification_status, account_status,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
               ON CONFLICT(user_id, role) DO UPDATE SET
                 verification_status=excluded.verification_status,
                 account_status=excluded.account_status,
                 updated_at=datetime('now')""",
            (user_id, role, table, primary["id"], channel,
             verification, account_status),
        )

    duplicates = conn.execute(
        f"""SELECT normalized_phone FROM {table}
            WHERE normalized_phone IS NOT NULL
            GROUP BY normalized_phone HAVING COUNT(*) > 1 LIMIT 1"""
    ).fetchone()
    if not duplicates:
        conn.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_normalized_phone_unique "
            f"ON {table}(normalized_phone) WHERE normalized_phone IS NOT NULL"
        )
    else:
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{table}_normalized_phone "
            f"ON {table}(normalized_phone)"
        )


def migrate():
    print(f"[Sowtrust] Migrating unified identity at: {config.DATABASE_PATH}")
    with sqlite3.connect(config.DATABASE_PATH) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
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
            CREATE INDEX IF NOT EXISTS idx_auth_otps_lookup
                ON auth_otps(user_id, role, purpose, created_at);

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
            CREATE INDEX IF NOT EXISTS idx_identity_issues_status
                ON identity_migration_issues(resolved_at, issue_type);

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
            """
        )

        for role, table in ROLE_TABLES.items():
            if _table_exists(conn, table):
                _prepare_role_table(conn, table)
                _backfill_role(conn, role, table)

        if _table_exists(conn, "farmers"):
            _add_column(conn, "farmers", "image_uploaded_by", "TEXT")
            _add_column(conn, "farmers", "image_uploaded_at", "TEXT")
            conn.execute(
                """UPDATE farmers
                   SET listing_status='PUBLISHED',
                       listing_published_at=COALESCE(listing_published_at, datetime('now')),
                       listing_updated_at=COALESCE(listing_updated_at, datetime('now'))
                   WHERE is_active=1 AND price > 0
                     AND COALESCE(listing_status, 'DRAFT') IN ('DRAFT', 'PENDING_REVIEW')"""
            )

        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS listing_moderation_log (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                farmer_phone   TEXT NOT NULL,
                previous_status TEXT,
                new_status     TEXT NOT NULL,
                reason         TEXT,
                actor          TEXT NOT NULL,
                created_at     TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_listing_moderation_farmer
                ON listing_moderation_log(farmer_phone, created_at);
            """
        )

        issue_count = conn.execute(
            "SELECT COUNT(*) FROM identity_migration_issues WHERE resolved_at IS NULL"
        ).fetchone()[0]
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        role_count = conn.execute("SELECT COUNT(*) FROM user_roles").fetchone()[0]

    print(f"[Sowtrust] Unified identity ready: {user_count} users, {role_count} roles, {issue_count} issue(s).")


if __name__ == "__main__":
    migrate()
