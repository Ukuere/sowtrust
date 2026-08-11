"""
Sowtrust — USSD Session Store (database-backed).

WHY THIS EXISTS: sessions were previously a module-level Python dict.
That works fine on one process, but the Procfile runs `gunicorn -w 4`
— four separate OS processes, each with its own memory. A farmer's
USSD request can be routed to any worker, so a session created on
worker 1 is invisible to workers 2-4. In production that means roughly
a 75% chance of "Session expired" on every single step of a multi-step
flow: registration, buying, adding a bank account.

Storing sessions in the shared database fixes this — every worker sees
the same state.

Note: Africa's Talking sends a `sessionId` per USSD dial. We key on
phone number (not sessionId) to stay compatible with the existing call
sites, which is fine because a phone can only hold one live USSD
session at a time anyway.
"""
import json
import time
from app.models.database import get_db, fetchone
from config.settings import config


def get_session(phone: str) -> dict:
    row = fetchone(
        "SELECT data, last_active FROM ussd_sessions WHERE phone = ?", (phone,)
    )
    if not row:
        return {}

    if (time.time() - row["last_active"]) > config.USSD_SESSION_TTL:
        clear_session(phone)
        return {}

    # Touch last_active so an in-progress session doesn't expire mid-flow.
    with get_db() as conn:
        conn.execute(
            "UPDATE ussd_sessions SET last_active = ? WHERE phone = ?",
            (time.time(), phone),
        )

    try:
        return json.loads(row["data"])
    except (json.JSONDecodeError, TypeError):
        # Corrupt session data — treat as no session rather than crashing
        # a farmer's call.
        clear_session(phone)
        return {}


def set_session(phone: str, data: dict):
    payload = json.dumps(data)
    now = time.time()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO ussd_sessions (phone, data, last_active)
               VALUES (?, ?, ?)
               ON CONFLICT(phone) DO UPDATE SET data = ?, last_active = ?""",
            (phone, payload, now, payload, now),
        )


def clear_session(phone: str):
    with get_db() as conn:
        conn.execute("DELETE FROM ussd_sessions WHERE phone = ?", (phone,))


def purge_expired_sessions() -> int:
    """
    Housekeeping — called by the expiry cron job so dead sessions don't
    accumulate forever. Returns how many were removed.
    """
    cutoff = time.time() - config.USSD_SESSION_TTL
    with get_db() as conn:
        cur = conn.execute("DELETE FROM ussd_sessions WHERE last_active < ?", (cutoff,))
        return cur.rowcount
