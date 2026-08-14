"""
Sowtrust — Security utilities.
"""
import hashlib
import hmac
import random
import secrets
import string
import bcrypt

# Session functions now live in session_store.py (database-backed, so they
# work across gunicorn's multiple worker processes). Re-exported here so
# existing imports from app.utils.security keep working.
from app.utils.session_store import (  # noqa: F401
    get_session, set_session, clear_session, purge_expired_sessions
)


# ── PIN HASHING ────────────────────────────────────────────────────────────
# SECURITY NOTE: PINs were previously stored as unsalted SHA-256. A 4-digit
# PIN has only 10,000 possible values, so an attacker with a copy of the
# database could reverse EVERY user's PIN in well under a second using a
# precomputed table, then drain wallets over USSD.
#
# We now use bcrypt (slow + individually salted by design), which makes
# that attack computationally impractical.
#
# BACKWARD COMPATIBILITY: existing users still have SHA-256 hashes stored.
# verify_pin() accepts either format, and callers can use
# needs_pin_rehash() to transparently upgrade a user's stored hash the
# next time they successfully enter their PIN — so nobody gets locked out
# and no forced reset is needed.

def hash_pin(pin: str) -> str:
    """Hash a PIN with bcrypt (salted, deliberately slow)."""
    return bcrypt.hashpw(pin.strip().encode(), bcrypt.gensalt()).decode()


# ── WEB PASSWORDS (buyer web accounts) ──────────────────────────────────────
# Same bcrypt primitive as PINs, just not .strip()'d/uppercased the way a
# 4-digit USSD PIN is — a web password can contain leading/trailing-
# significant characters a user actually typed. Kept as separate named
# functions (not just an alias) so PIN and password call sites read clearly
# and can diverge later (e.g. password complexity rules) without touching
# PIN logic.

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain_password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode(), stored_hash.encode())
    except (ValueError, TypeError):
        return False


def _legacy_sha256(pin: str) -> str:
    return hashlib.sha256(pin.strip().encode()).hexdigest()


def verify_pin(plain_pin: str, stored_hash: str) -> bool:
    """
    Verify a PIN against either a modern bcrypt hash or a legacy
    SHA-256 hash, so existing accounts continue to work.
    """
    if not stored_hash:
        return False

    # bcrypt hashes always start with $2 (e.g. $2b$12$...)
    if stored_hash.startswith("$2"):
        try:
            return bcrypt.checkpw(plain_pin.strip().encode(), stored_hash.encode())
        except (ValueError, TypeError):
            return False

    # Legacy SHA-256 path — constant-time compare to avoid timing leaks.
    return hmac.compare_digest(_legacy_sha256(plain_pin), stored_hash)


def needs_pin_rehash(stored_hash: str) -> bool:
    """True if this hash is in the old weak format and should be upgraded."""
    return bool(stored_hash) and not stored_hash.startswith("$2")


def verify_and_upgrade_pin(table: str, phone: str, plain_pin: str, stored_hash: str) -> bool:
    """
    Verify a PIN and, if it's still stored in the legacy SHA-256 format,
    transparently re-hash it with bcrypt now that we have the plaintext.

    This is how existing accounts migrate to strong hashing without any
    forced reset — they upgrade silently the next time they log in.

    `table` must be one of the known account tables (whitelisted below —
    it's interpolated into SQL, so it must never come from user input).
    """
    if not verify_pin(plain_pin, stored_hash):
        return False

    if needs_pin_rehash(stored_hash):
        allowed = {"farmers", "agents", "logistics_providers"}
        if table not in allowed:
            raise ValueError(f"Refusing to upgrade PIN for unknown table: {table}")
        try:
            from app.models.database import get_db
            with get_db() as conn:
                conn.execute(
                    f"UPDATE {table} SET pin_hash = ? WHERE phone = ?",
                    (hash_pin(plain_pin), phone),
                )
        except Exception:
            # An upgrade failure must never block a legitimate login —
            # the PIN was correct, so let them through and try again next time.
            pass

    return True


# ── RELEASE / DELIVERY CODES ───────────────────────────────────────────────
# These control real money movement (releasing escrow to a farmer, and
# confirming delivery to pay a logistics provider), so they must not be
# guessable. `random` is a predictable PRNG and is not safe for this —
# `secrets` is the cryptographically secure equivalent.

def generate_release_code() -> str:
    """Returns a 6-character cryptographically-secure alphanumeric token."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


def hash_release_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def verify_release_code(plain_code: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    return hmac.compare_digest(hash_release_code(plain_code), stored_hash)
