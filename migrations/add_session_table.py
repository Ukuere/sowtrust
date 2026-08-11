"""
Sowtrust — Migration: production-safety fixes.

1. `ussd_sessions` table — moves USSD session state out of per-process
   memory and into the shared database. Required because the Procfile
   runs `gunicorn -w 4`: with in-memory sessions, a farmer's request
   can hit a worker that has never seen their session, causing random
   "Session expired" failures mid-registration or mid-purchase.

2. Flags existing weak PIN hashes. PINs were stored as unsalted
   SHA-256 (only 10,000 possible 4-digit values — reversible almost
   instantly from a database copy). New PINs use bcrypt. Existing
   users are NOT locked out: verify_pin() accepts both formats and
   their hash is silently upgraded the next time they enter their PIN
   correctly.

Safe to run multiple times and safe against a live database.

Run once:  python migrations/add_session_table.py
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
            CREATE TABLE IF NOT EXISTS ussd_sessions (
                phone        TEXT PRIMARY KEY,
                data         TEXT NOT NULL,
                last_active  REAL NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_last_active ON ussd_sessions(last_active)"
        )
        print("  + ussd_sessions table ready (multi-worker safe)")

        # Report on legacy PIN hashes so you know the scale of the upgrade.
        legacy = 0
        for table in ("farmers", "agents", "logistics_providers"):
            try:
                rows = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE pin_hash IS NOT NULL "
                    f"AND pin_hash NOT LIKE '$2%'"
                ).fetchone()
                if rows and rows[0]:
                    print(f"  · {table}: {rows[0]} account(s) still on legacy SHA-256 PIN hashes")
                    legacy += rows[0]
            except sqlite3.OperationalError:
                pass  # table may not exist yet in older databases

        if legacy:
            print(f"  → {legacy} account(s) will auto-upgrade to bcrypt on next successful PIN entry.")
            print("    No action needed, and nobody gets locked out.")
        else:
            print("  · No legacy PIN hashes found.")

    print("[Sowtrust] ✅ Production-safety migration complete.")


if __name__ == "__main__":
    migrate()
