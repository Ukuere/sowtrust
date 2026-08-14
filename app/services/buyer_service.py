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

_PHONE_RE = re.compile(r"^\+?[0-9]{10,15}$")
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


def normalize_phone(raw: str) -> str | None:
    """
    Accepts Nigerian local (0801...) or E.164 (+2348011112222) input,
    returns E.164 or None if it doesn't look like a real phone number.
    """
    if not raw:
        return None
    cleaned = re.sub(r"[\s\-]", "", raw.strip())
    if not _PHONE_RE.match(cleaned):
        return None
    if cleaned.startswith("+"):
        return cleaned
    if cleaned.startswith("0"):
        return "+234" + cleaned[1:]
    return "+" + cleaned


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
    if not password or len(password) < 6:
        return {"ok": False, "error": "Password must be at least 6 characters."}
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

    existing = fetchone("SELECT password_hash FROM buyers WHERE phone = ?", (norm_phone,))
    if existing and existing["password_hash"]:
        return {"ok": False, "error": "An account with this phone number already exists. Log in instead."}

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
               (phone, name, business_name, email, password_hash,
                delivery_address, city, state, buyer_type,
                kyc_status, email_verified, email_verification_token,
                email_verification_sent_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PROFILE_COMPLETED', 0, ?, datetime('now'))
               ON CONFLICT(phone) DO UPDATE SET
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
            (norm_phone, name.strip(), business_name.strip() or None, email.strip(), pw_hash,
             delivery_address.strip(), city.strip(), state.strip(), buyer_type,
             verification_token),
        )
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

    row = fetchone("SELECT * FROM buyers WHERE phone = ?", (norm_phone,))
    if not row or not row["password_hash"]:
        return {"ok": False, "error": "No web account found for that phone number."}
    if not verify_password(password, row["password_hash"]):
        return {"ok": False, "error": "Incorrect password."}
    if not row["is_active"]:
        return {"ok": False, "error": "This account has been deactivated. Contact support."}

    return {"ok": True, "buyer": dict(row)}


def get_buyer(phone: str) -> dict | None:
    row = fetchone("SELECT * FROM buyers WHERE phone = ?", (phone,))
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
                 kyc_status = 'KYC_PENDING',
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
                   kyc_rejection_reason = ?
               WHERE phone = ?""",
            (decision, rejection_reason.strip() or None, record["user_id"]),
        )
    return {"ok": True}
