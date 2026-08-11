"""
Sowtrust — Security utilities.
"""
import hashlib
import random
import string
import time
from config.settings import config

# In-memory USSD session store  { phone: { "data": {}, "last_active": timestamp } }
_sessions: dict = {}


# ── PIN HASHING ────────────────────────────────────────────────────────────
def hash_pin(pin: str) -> str:
    """SHA-256 hash of a PIN string."""
    return hashlib.sha256(pin.strip().encode()).hexdigest()


def verify_pin(plain_pin: str, stored_hash: str) -> bool:
    return hash_pin(plain_pin) == stored_hash


# ── RELEASE CODE ───────────────────────────────────────────────────────────
def generate_release_code() -> str:
    """Returns a 6-character alphanumeric release token."""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def hash_release_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def verify_release_code(plain_code: str, stored_hash: str) -> bool:
    return hash_release_code(plain_code) == stored_hash


# ── USSD SESSION MANAGEMENT ────────────────────────────────────────────────
def get_session(phone: str) -> dict:
    now = time.time()
    session = _sessions.get(phone)
    if session and (now - session["last_active"]) > config.USSD_SESSION_TTL:
        del _sessions[phone]
        return {}
    if session:
        session["last_active"] = now
        return session["data"]
    return {}


def set_session(phone: str, data: dict):
    _sessions[phone] = {"data": data, "last_active": time.time()}


def clear_session(phone: str):
    _sessions.pop(phone, None)
