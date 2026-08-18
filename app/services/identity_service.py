"""Unified identity and OTP activation for USSD and web channels."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets

from app.models.database import fetchone, get_db
from app.services import notification_service
from app.utils.phone import mask_phone, normalize_phone
from app.utils.security import verify_and_upgrade_pin
from config.settings import config


ROLE_TABLES = {
    "FARMER": "farmers",
    "BUYER": "buyers",
    "AGENT": "agents",
    "LOGISTICS": "logistics_providers",
}
ROLE_SESSION_KEYS = {
    "BUYER": "buyer_phone",
    "AGENT": "agent_phone",
    "LOGISTICS": "provider_phone",
}
VALID_CHANNELS = {"USSD", "WEB", "ADMIN", "API", "LEGACY"}


def _role(role: str) -> str:
    value = (role or "").strip().upper()
    if value not in ROLE_TABLES:
        raise ValueError("Unknown SowTrust role.")
    return value


def _utc_sql(seconds_from_now: int = 0) -> str:
    value = datetime.now(timezone.utc) + timedelta(seconds=seconds_from_now)
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _otp_hash(user_id: int, role: str, purpose: str, code: str) -> str:
    payload = f"{user_id}|{role}|{purpose}|{code}".encode()
    return hmac.new(config.SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()


def _profile_for_normalized_phone(role: str, normalized_phone: str):
    table = ROLE_TABLES[_role(role)]
    return fetchone(
        f"SELECT * FROM {table} WHERE normalized_phone=? ORDER BY id ASC LIMIT 1",
        (normalized_phone,),
    )


def get_user_by_phone(phone: str):
    normalized = normalize_phone(phone)
    if not normalized:
        return None
    row = fetchone("SELECT * FROM users WHERE normalized_phone=?", (normalized,))
    return dict(row) if row else None


def ensure_user_role(phone: str, role: str, full_name: str = "",
                     registration_channel: str = "WEB",
                     phone_verified: bool = False,
                     profile_record_id: int | None = None) -> dict:
    """Create or link one role without creating a duplicate user."""
    normalized = normalize_phone(phone)
    if not normalized:
        return {"ok": False, "error": "Enter a valid Nigerian phone number."}
    role = _role(role)
    channel = registration_channel.upper()
    if channel not in VALID_CHANNELS:
        return {"ok": False, "error": "Invalid registration channel."}

    profile = _profile_for_normalized_phone(role, normalized)
    if not profile and profile_record_id is None:
        return {"ok": False, "error": f"No {role.lower()} profile exists for that phone number."}
    profile_record_id = profile_record_id or profile["id"]
    verification = "UNVERIFIED"
    account_status = "ACTIVE"
    if profile:
        keys = profile.keys()
        verification = profile["verification_status"] if "verification_status" in keys else verification
        account_status = profile["account_status"] if "account_status" in keys else account_status

    with get_db() as conn:
        conn.execute(
            """INSERT INTO users
               (normalized_phone, full_name, account_status, phone_verified,
                first_registration_channel, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
               ON CONFLICT(normalized_phone) DO UPDATE SET
                 full_name=COALESCE(NULLIF(users.full_name, ''), excluded.full_name),
                 phone_verified=MAX(users.phone_verified, excluded.phone_verified),
                 updated_at=datetime('now')""",
            (normalized, full_name.strip() or None, account_status,
             1 if phone_verified else 0, channel),
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
                 profile_record_id=COALESCE(user_roles.profile_record_id, excluded.profile_record_id),
                 verification_status=excluded.verification_status,
                 account_status=excluded.account_status,
                 updated_at=datetime('now')""",
            (user_id, role, ROLE_TABLES[role], profile_record_id, channel,
             verification or "UNVERIFIED", account_status or "ACTIVE"),
        )
        conn.execute(
            f"""UPDATE {ROLE_TABLES[role]}
                SET normalized_phone=?, registration_channel=COALESCE(registration_channel, ?),
                    phone_verified=MAX(COALESCE(phone_verified, 0), ?),
                    updated_at=datetime('now')
                WHERE id=?""",
            (normalized, channel, 1 if phone_verified else 0, profile_record_id),
        )

    return {"ok": True, "user_id": user_id, "phone": normalized, "role": role}


def request_otp(phone: str, role: str, purpose: str = "ACTIVATE",
                requested_ip: str = "") -> dict:
    normalized = normalize_phone(phone)
    if not normalized:
        return {"ok": False, "error": "Enter a valid phone number, e.g. 08012345678."}
    role = _role(role)
    purpose = (purpose or "ACTIVATE").upper()
    profile = _profile_for_normalized_phone(role, normalized)
    if not profile:
        return {"ok": False, "error": f"No {role.lower()} account was found for that number."}

    linked = ensure_user_role(
        normalized, role, profile["name"] if "name" in profile.keys() else "",
        profile["registration_channel"] if "registration_channel" in profile.keys() else "LEGACY",
        bool(profile["phone_verified"]) if "phone_verified" in profile.keys() else False,
        profile["id"],
    )
    if not linked["ok"]:
        return linked

    recent = fetchone(
        """SELECT created_at FROM auth_otps
           WHERE user_id=? AND role=? AND purpose=? AND consumed_at IS NULL
             AND created_at > datetime('now', ?)
           ORDER BY id DESC LIMIT 1""",
        (linked["user_id"], role, purpose, f"-{config.OTP_RESEND_SECONDS} seconds"),
    )
    if recent:
        return {"ok": False, "error": "A code was sent recently. Please wait before requesting another."}

    code = f"{secrets.randbelow(1_000_000):06d}"
    with get_db() as conn:
        conn.execute(
            """UPDATE auth_otps SET consumed_at=datetime('now')
               WHERE user_id=? AND role=? AND purpose=? AND consumed_at IS NULL""",
            (linked["user_id"], role, purpose),
        )
        conn.execute(
            """INSERT INTO auth_otps
               (user_id, role, purpose, code_hash, max_attempts,
                requested_ip, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (linked["user_id"], role, purpose,
             _otp_hash(linked["user_id"], role, purpose, code),
             config.OTP_MAX_ATTEMPTS, requested_ip or None,
             _utc_sql(config.OTP_TTL_SECONDS)),
        )

    delivered = notification_service.notify_sms(
        role.lower(), str(linked["user_id"]), normalized,
        "ACCOUNT_OTP",
        f"SowTrust verification code: {code}. It expires in {config.OTP_TTL_SECONDS // 60} minutes. Do not share it.",
        {"purpose": purpose},
    )
    if not delivered and config.ENV == "production":
        with get_db() as conn:
            conn.execute(
                "UPDATE auth_otps SET consumed_at=datetime('now') "
                "WHERE user_id=? AND role=? AND purpose=? AND consumed_at IS NULL",
                (linked["user_id"], role, purpose),
            )
        return {"ok": False, "error": "We could not deliver the code. Please try again shortly."}

    result = {"ok": True, "phone": normalized, "masked_phone": mask_phone(normalized)}
    if config.ENV in {"development", "testing"}:
        result["debug_otp"] = code
    return result


def verify_otp(phone: str, role: str, code: str,
               purpose: str = "ACTIVATE") -> dict:
    normalized = normalize_phone(phone)
    if not normalized:
        return {"ok": False, "error": "Enter a valid phone number."}
    role = _role(role)
    purpose = (purpose or "ACTIVATE").upper()
    user = fetchone("SELECT * FROM users WHERE normalized_phone=?", (normalized,))
    if not user:
        return {"ok": False, "error": "Account not found."}

    otp = fetchone(
        """SELECT * FROM auth_otps
           WHERE user_id=? AND role=? AND purpose=? AND consumed_at IS NULL
             AND expires_at > datetime('now')
           ORDER BY id DESC LIMIT 1""",
        (user["id"], role, purpose),
    )
    if not otp:
        return {"ok": False, "error": "The code has expired. Request a new one."}
    if otp["attempts"] >= otp["max_attempts"]:
        return {"ok": False, "error": "Too many incorrect attempts. Request a new code."}

    expected = _otp_hash(user["id"], role, purpose, (code or "").strip())
    if not hmac.compare_digest(expected, otp["code_hash"]):
        with get_db() as conn:
            conn.execute("UPDATE auth_otps SET attempts=attempts+1 WHERE id=?", (otp["id"],))
        return {"ok": False, "error": "Incorrect verification code."}

    profile = _profile_for_normalized_phone(role, normalized)
    with get_db() as conn:
        conn.execute("UPDATE auth_otps SET consumed_at=datetime('now') WHERE id=?", (otp["id"],))
        conn.execute(
            """UPDATE users SET phone_verified=1, last_login_at=datetime('now'),
                   updated_at=datetime('now') WHERE id=?""",
            (user["id"],),
        )
        if profile:
            conn.execute(
                f"""UPDATE {ROLE_TABLES[role]}
                    SET phone_verified=1, updated_at=datetime('now') WHERE id=?""",
                (profile["id"],),
            )
        conn.execute(
            """INSERT INTO audit_log(actor, action, details)
               VALUES (?, 'PHONE_OTP_VERIFIED', ?)""",
            (normalized, f"ROLE:{role} PURPOSE:{purpose}"),
        )
    return {"ok": True, "phone": normalized, "role": role,
            "session_key": ROLE_SESSION_KEYS.get(role)}


def authenticate_role_pin(phone: str, role: str, pin: str) -> dict:
    """Normalize web input before authenticating an existing USSD PIN."""
    normalized = normalize_phone(phone)
    if not normalized:
        return {"ok": False, "error": "Enter a valid phone number."}
    role = _role(role)
    profile = _profile_for_normalized_phone(role, normalized)
    if not profile or not profile["is_active"]:
        return {"ok": False, "error": "Account not found or inactive."}
    if "pin_hash" not in profile.keys() or not verify_and_upgrade_pin(
        ROLE_TABLES[role], profile["phone"], pin, profile["pin_hash"]
    ):
        return {"ok": False, "error": "Incorrect phone number or PIN."}

    linked = ensure_user_role(
        normalized, role, profile["name"] if "name" in profile.keys() else "",
        profile["registration_channel"] if "registration_channel" in profile.keys() else "LEGACY",
        True, profile["id"],
    )
    if linked["ok"]:
        with get_db() as conn:
            conn.execute(
                "UPDATE users SET phone_verified=1, last_login_at=datetime('now') WHERE id=?",
                (linked["user_id"],),
            )
    return {"ok": True, "phone": normalized, "profile": dict(profile)}
