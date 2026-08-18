"""Database-backed staff authentication for administration routes."""
import hmac

from app.models.database import fetchone, get_db
from app.utils.security import hash_password, verify_password
from config.settings import config


def bootstrap_admin_if_needed():
    """Create the first admin from environment credentials, once."""
    count = fetchone("SELECT COUNT(*) AS n FROM staff_users")
    if count and count["n"]:
        return
    if not config.DASHBOARD_USERNAME or config.DASHBOARD_PASSWORD == "changeme":
        return
    with get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO staff_users
               (username, password_hash, role, is_active)
               VALUES (?, ?, 'ADMIN', 1)""",
            (config.DASHBOARD_USERNAME, hash_password(config.DASHBOARD_PASSWORD)),
        )


def authenticate(username: str, password: str) -> dict:
    bootstrap_admin_if_needed()
    row = fetchone(
        "SELECT * FROM staff_users WHERE username=? AND is_active=1",
        ((username or "").strip(),),
    )
    if not row or not verify_password(password or "", row["password_hash"]):
        hmac.compare_digest("invalid", "invalid")
        return {"ok": False, "error": "Invalid staff username or password."}
    with get_db() as conn:
        conn.execute(
            "UPDATE staff_users SET last_login_at=datetime('now') WHERE id=?",
            (row["id"],),
        )
        conn.execute(
            "INSERT INTO audit_log(actor, action, details) VALUES (?, 'STAFF_LOGIN', ?)",
            (row["username"], f"ROLE:{row['role']}"),
        )
    return {"ok": True, "staff": dict(row)}
