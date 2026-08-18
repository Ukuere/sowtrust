"""
Sowtrust — Buyer Account Service (web).

Registration, authentication, lookup, and email verification for buyer
web accounts. Phone number is the identity key shared with the USSD side
(escrow_ledger, sms_service notifications) — a buyer who has ordered
over USSD already has a row in `buyers`, this service just adds a
password + KYC profile to it rather than creating a second, disconnected
identity.

Spec section 7 fields: name/business name, phone, email (+ verification),
delivery address, city, state, buyer type, KYC info, verification status.
"""
import re
import secrets
from app.models.database import get_db, fetchone, fetchall
from app.utils.security import hash_password, verify_password
from app.utils.phone import normalize_phone

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

BUYER_TYPES = ["Individual", "Retailer", "Wholesaler", "Restaurant/Hospitality", "Processor", "Other"]

# See DESIGN NOTE in migrations/add_kyc_verification_system.py — this is a
# merged vocabulary covering both the spec's onboarding sequence and its
# terminal record states, since checkout needs one field to gate on.
BUYER_KYC_STATUSES = (
    "REGISTERED", "PHONE_VERIFIED", "PROFILE_COMPLETED", "KYC_PENDING",
    "UNDER_REVIEW", "VERIFIED", "REJECTED", "SUSPENDED", "EXPIRED",
)

ID_TYPES = ["National ID (NIN)", "International Passport", "Driver's Licence", "Voter's Card"]

BUSINESS_BUYER_TYPES = {"Retailer", "Wholesaler", "Restaurant/Hospitality", "Processor"}


def ensure_ussd_buyer(phone: str) -> dict:
    """Persist a buyer as soon as they enter the USSD buyer portal."""
    norm_phone = normalize_phone(phone)
    if not norm_phone:
        return {"ok": False, "error": "Africa's Talking did not supply a valid phone number."}
    existing = fetchone(
        "SELECT * FROM buyers WHERE normalized_phone=? OR phone=?",
        (norm_phone, norm_phone),
    )
    created = not existing
    with get_db() as conn:
        conn.execute(
            """INSERT INTO buyers
               (phone, normalized_phone, registration_channel,
                verification_status, account_status, phone_verified,
                kyc_status, is_active, created_at, updated_at)
               VALUES (?, ?, 'USSD', 'UNVERIFIED', 'ACTIVE', 1,
                       'REGISTERED', 1, datetime('now'), datetime('now'))
               ON CONFLICT(phone) DO UPDATE SET
                 normalized_phone=excluded.normalized_phone,
                 phone_verified=1,
                 updated_at=datetime('now')""",
            (norm_phone, norm_phone),
        )
        buyer_id = conn.execute(
            "SELECT id FROM buyers WHERE normalized_phone=?", (norm_phone,)
        ).fetchone()[0]
        if created:
            conn.execute(
                "INSERT INTO audit_log(actor, action, details) VALUES (?, 'BUYER_REGISTERED', 'CHANNEL:USSD')",
                (norm_phone,),
            )
    from app.services import identity_service
    linked = identity_service.ensure_user_role(
        norm_phone, "BUYER", "", "USSD", True, buyer_id
    )
    return {"ok": linked["ok"], "phone": norm_phone,
            "created": created, "error": linked.get("error")}


def register_buyer(phone: str, password: str, name: str, email: str,
                    delivery_address: str, city: str, state: str, buyer_type: str,
                    business_name: str = "") -> dict:
    """
    Spec section 7 — structured buyer KYC, not just name+phone+password.
    Email is now required (not optional) since it drives verification;
    business_name stays optional since many buyers are individuals.
    """
    norm_phone = normalize_phone(phone)
    if not norm_phone:
        return {"ok": False, "error": "Enter a valid phone number, e.g. 08011112222."}
    if not password or len(password) < 8:
        return {"ok": False, "error": "Password must be at least 8 characters."}
    if not name or len(name.strip()) < 2:
        return {"ok": False, "error": "Enter your full name."}
    if not email or not _EMAIL_RE.match(email.strip()):
        return {"ok": False, "error": "Enter a valid email address."}
    if not delivery_address or len(delivery_address.strip()) < 5:
        return {"ok": False, "error": "Enter your delivery address."}
    if not city or not city.strip():
        return {"ok": False, "error": "Enter your city."}
    if not state or not state.strip():
        return {"ok": False, "error": "Enter your state."}
    if buyer_type not in BUYER_TYPES:
        return {"ok": False, "error": "Select a valid buyer type."}

    existing = fetchone(
        "SELECT password_hash FROM buyers WHERE normalized_phone = ? OR phone = ?",
        (norm_phone, norm_phone),
    )
    if existing and existing["password_hash"]:
        return {"ok": False, "error": "An account with this phone number already exists. Log in instead."}
    if existing:
        return {
            "ok": False,
            "activation_required": True,
            "phone": norm_phone,
            "error": "Your SowTrust account already exists. Verify your phone number to access the portal.",
        }

    existing_email = fetchone(
        "SELECT phone FROM buyers WHERE email = ? AND phone != ?", (email.strip(), norm_phone)
    )
    if existing_email:
        return {"ok": False, "error": "That email address is already registered."}

    pw_hash = hash_password(password)
    verification_token = secrets.token_urlsafe(24)

    with get_db() as conn:
        conn.execute(
            """INSERT INTO buyers
               (phone, normalized_phone, registration_channel, verification_status,
                account_status, phone_verified,
                name, business_name, email, password_hash,
                delivery_address, city, state, buyer_type,
                kyc_status, email_verified, email_verification_token,
                email_verification_sent_at)
               VALUES (?, ?, 'WEB', 'UNVERIFIED', 'ACTIVE', 0,
                       ?, ?, ?, ?, ?, ?, ?, ?, 'PROFILE_COMPLETED', 0, ?, datetime('now'))
               ON CONFLICT(phone) DO UPDATE SET
                 normalized_phone = excluded.normalized_phone,
                 name = excluded.name,
                 business_name = excluded.business_name,
                 email = excluded.email,
                 password_hash = excluded.password_hash,
                 delivery_address = excluded.delivery_address,
                 city = excluded.city,
                 state = excluded.state,
                 buyer_type = excluded.buyer_type,
                 email_verification_token = excluded.email_verification_token,
                 email_verification_sent_at = excluded.email_verification_sent_at""",
            (norm_phone, norm_phone, name.strip(), business_name.strip() or None, email.strip(), pw_hash,
             delivery_address.strip(), city.strip(), state.strip(), buyer_type,
             verification_token),
        )
        buyer_id = conn.execute(
            "SELECT id FROM buyers WHERE phone=?", (norm_phone,)
        ).fetchone()[0]

    from app.services import identity_service
    linked = identity_service.ensure_user_role(
        norm_phone, "BUYER", name, "WEB", False, buyer_id
    )
    if not linked["ok"]:
        return linked
    return {"ok": True, "phone": norm_phone, "email": email.strip(),
            "verification_token": verification_token}


def verify_email(token: str) -> dict:
    if not token:
        return {"ok": False, "error": "Missing verification token."}
    row = fetchone("SELECT phone FROM buyers WHERE email_verification_token = ?", (token,))
    if not row:
        return {"ok": False, "error": "Invalid or already-used verification link."}
    with get_db() as conn:
        conn.execute(
            """UPDATE buyers SET email_verified = 1, email_verification_token = NULL
               WHERE phone = ?""",
            (row["phone"],),
        )
    return {"ok": True, "phone": row["phone"]}


def authenticate_buyer(phone: str, password: str) -> dict:
    norm_phone = normalize_phone(phone)
    if not norm_phone:
        return {"ok": False, "error": "Enter a valid phone number."}

    row = fetchone(
        "SELECT * FROM buyers WHERE normalized_phone = ? OR phone = ?",
        (norm_phone, norm_phone),
    )
    if row and not row["password_hash"]:
        return {
            "ok": False,
            "activation_required": True,
            "phone": norm_phone,
            "error": "Your SowTrust account already exists. Verify your phone number to access the portal.",
        }
    if not row:
        return {"ok": False, "error": "No web account found for that phone number."}
    if not verify_password(password, row["password_hash"]):
        return {"ok": False, "error": "Incorrect password."}
    if not row["is_active"]:
        return {"ok": False, "error": "This account has been deactivated. Contact support."}
    if not row["phone_verified"]:
        return {
            "ok": False,
            "activation_required": True,
            "phone": norm_phone,
            "error": "Verify your phone number before signing in.",
        }

    from app.services import identity_service
    identity_service.ensure_user_role(
        norm_phone, "BUYER", row["name"] or "",
        row["registration_channel"] or "LEGACY", True, row["id"],
    )
    return {"ok": True, "buyer": dict(row), "phone": norm_phone}


def set_buyer_password(phone: str, password: str) -> dict:
    """Attach web credentials after successful phone OTP activation."""
    norm_phone = normalize_phone(phone)
    if not norm_phone:
        return {"ok": False, "error": "Enter a valid phone number."}
    if not password or len(password) < 8:
        return {"ok": False, "error": "Password must be at least 8 characters."}
    row = fetchone(
        "SELECT id FROM buyers WHERE normalized_phone=? OR phone=?",
        (norm_phone, norm_phone),
    )
    if not row:
        return {"ok": False, "error": "Buyer account not found."}
    password_hash = hash_password(password)
    with get_db() as conn:
        conn.execute(
            """UPDATE buyers SET password_hash=?, phone_verified=1,
                   updated_at=datetime('now') WHERE id=?""",
            (password_hash, row["id"]),
        )
        conn.execute(
            """UPDATE users SET password_hash=?, phone_verified=1,
                   updated_at=datetime('now') WHERE normalized_phone=?""",
            (password_hash, norm_phone),
        )
    return {"ok": True, "phone": norm_phone}


def mark_phone_verified_for_test(phone: str) -> None:
    """Test harness hook; production verification always uses a valid OTP."""
    norm_phone = normalize_phone(phone)
    with get_db() as conn:
        conn.execute(
            "UPDATE buyers SET phone_verified=1, updated_at=datetime('now') "
            "WHERE normalized_phone=? OR phone=?",
            (norm_phone, norm_phone),
        )
        conn.execute(
            "UPDATE users SET phone_verified=1, updated_at=datetime('now') "
            "WHERE normalized_phone=?",
            (norm_phone,),
        )


def get_buyer(phone: str) -> dict | None:
    normalized = normalize_phone(phone)
    row = fetchone(
        "SELECT * FROM buyers WHERE normalized_phone = ? OR phone = ?",
        (normalized, normalized),
    ) if normalized else None
    return dict(row) if row else None


def is_checkout_eligible(phone: str) -> bool:
    """Single source of truth for the checkout gate — spec section 1.
    Called both when rendering checkout and again on submit, server-side
    only (never trust a frontend check for this)."""
    buyer = get_buyer(phone)
    return bool(buyer) and buyer.get("kyc_status") == "VERIFIED"


def submit_kyc(phone: str, id_type: str, id_number: str, id_document_path: str,
                business_reg_number: str = "", business_reg_document_path: str = "",
                authorized_rep_name: str = "", authorized_rep_id_number: str = "") -> dict:
    """
    Spec section 2 — moves a buyer from PROFILE_COMPLETED to KYC_PENDING
    and opens a kyc_verifications record for manual review. Business
    buyer types additionally require CAC registration info.
    """
    buyer = get_buyer(phone)
    if not buyer:
        return {"ok": False, "error": "Account not found."}
    if buyer["kyc_status"] in ("KYC_PENDING", "UNDER_REVIEW"):
        return {"ok": False, "error": "Your verification is already under review."}
    if buyer["kyc_status"] == "VERIFIED":
        return {"ok": False, "error": "Your account is already verified."}

    if id_type not in ID_TYPES:
        return {"ok": False, "error": "Select a valid ID type."}
    if not id_number or len(id_number.strip()) < 4:
        return {"ok": False, "error": "Enter a valid ID number."}
    if not id_document_path:
        return {"ok": False, "error": "Upload a copy of your ID document."}

    is_business = buyer.get("buyer_type") in BUSINESS_BUYER_TYPES
    if is_business:
        if not business_reg_number or not business_reg_number.strip():
            return {"ok": False, "error": "Enter your CAC registration number."}
        if not business_reg_document_path:
            return {"ok": False, "error": "Upload your CAC registration document."}
        if not authorized_rep_name or not authorized_rep_name.strip():
            return {"ok": False, "error": "Enter the authorized representative's name."}

    with get_db() as conn:
        conn.execute(
            """UPDATE buyers SET
                 kyc_status = 'KYC_PENDING', verification_status='PENDING',
                 id_type = ?, id_number = ?, id_document_path = ?,
                 business_reg_number = ?, business_reg_document_path = ?,
                 authorized_rep_name = ?, authorized_rep_id_number = ?,
                 kyc_submitted_at = datetime('now')
               WHERE phone = ?""",
            (id_type, id_number.strip(), id_document_path,
             business_reg_number.strip() or None, business_reg_document_path or None,
             authorized_rep_name.strip() or None, authorized_rep_id_number.strip() or None,
             phone),
        )
        conn.execute(
            """INSERT INTO kyc_verifications
               (user_type, user_id, verification_type, status, submitted_at)
               VALUES ('buyer', ?, ?, 'PENDING', datetime('now'))""",
            (phone, "business" if is_business else "identity"),
        )
    return {"ok": True}


def get_pending_kyc_verifications() -> list[dict]:
    """For the admin review queue — oldest submission first."""
    rows = fetchall(
        """SELECT v.*, b.name, b.business_name, b.email, b.buyer_type,
                  b.id_type, b.id_number, b.id_document_path,
                  b.business_reg_number, b.business_reg_document_path,
                  b.authorized_rep_name
           FROM kyc_verifications v
           JOIN buyers b ON b.phone = v.user_id
           WHERE v.user_type = 'buyer' AND v.status IN ('PENDING', 'UNDER_REVIEW')
           ORDER BY v.submitted_at ASC"""
    )
    return [dict(r) for r in rows]


def admin_review_kyc(verification_id: int, decision: str, reviewed_by: str,
                      rejection_reason: str = "") -> dict:
    """
    decision must be 'VERIFIED' or 'REJECTED'. Updates both the audit
    record and the buyer's own kyc_status — the latter is what checkout
    actually reads.
    """
    if decision not in ("VERIFIED", "REJECTED"):
        return {"ok": False, "error": "Invalid decision."}
    if decision == "REJECTED" and not rejection_reason.strip():
        return {"ok": False, "error": "A rejection reason is required."}

    record = fetchone("SELECT * FROM kyc_verifications WHERE id = ?", (verification_id,))
    if not record:
        return {"ok": False, "error": "Verification record not found."}
    if record["status"] not in ("PENDING", "UNDER_REVIEW"):
        return {"ok": False, "error": "This record has already been reviewed."}

    with get_db() as conn:
        conn.execute(
            """UPDATE kyc_verifications
               SET status = ?, verified_at = datetime('now'),
                   reviewed_by = ?, rejection_reason = ?
               WHERE id = ?""",
            (decision, reviewed_by, rejection_reason.strip() or None, verification_id),
        )
        conn.execute(
            """UPDATE buyers SET kyc_status = ?, kyc_reviewed_at = datetime('now'),
                   verification_status=?, kyc_rejection_reason = ?
               WHERE phone = ?""",
            (decision, decision, rejection_reason.strip() or None, record["user_id"]),
        )
        conn.execute(
            """UPDATE user_roles SET verification_status=?, updated_at=datetime('now')
               WHERE role='BUYER' AND user_id=(
                 SELECT id FROM users WHERE normalized_phone=?)""",
            (decision, record["user_id"]),
        )
    return {"ok": True}
