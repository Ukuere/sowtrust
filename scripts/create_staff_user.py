"""Create or update an individual database-backed staff account."""
import argparse
import bcrypt
from getpass import getpass
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import config


ROLES = ("ADMIN", "OPERATIONS", "REVIEWER")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", choices=ROLES, default="OPERATIONS")
    args = parser.parse_args()
    username = args.username.strip()
    password = os.getenv("STAFF_CREATE_PASSWORD") or getpass("New staff password: ")
    if len(username) < 3:
        raise SystemExit("Username must contain at least 3 characters.")
    if len(password) < 12:
        raise SystemExit("Staff password must contain at least 12 characters.")

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with sqlite3.connect(config.DATABASE_PATH) as conn:
        conn.execute(
            """INSERT INTO staff_users(username, password_hash, role, is_active)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(username) DO UPDATE SET
                 password_hash=excluded.password_hash,
                 role=excluded.role,
                 is_active=1,
                 updated_at=datetime('now')""",
            (username, password_hash, args.role),
        )
        conn.execute(
            "INSERT INTO audit_log(actor, action, details) VALUES (?, 'STAFF_ACCOUNT_UPSERTED', ?)",
            ("staff-bootstrap", f"USERNAME:{username} ROLE:{args.role}"),
        )
    print(f"[Sowtrust] Staff account ready: {username} ({args.role})")


if __name__ == "__main__":
    main()
