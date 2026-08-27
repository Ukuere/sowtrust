"""Authenticated backend API consumed by the separate Streamlit console."""
from functools import wraps
import hmac

from flask import Blueprint, abort, jsonify, request

from app.models.database import fetchall, fetchone, get_db
from app.services import escrow_service
from app.services.agent_incentive_service import AgentIncentiveService
from app.utils.phone import normalize_phone
from config.settings import config


internal_dashboard_bp = Blueprint("internal_dashboard", __name__)


def require_dashboard_token(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        supplied = request.headers.get("Authorization", "")
        expected = f"Bearer {config.DASHBOARD_API_TOKEN}"
        if not config.DASHBOARD_API_TOKEN or not hmac.compare_digest(supplied, expected):
            abort(401)
        return view(*args, **kwargs)
    return wrapped


def _rows(sql, params=()):
    return [dict(row) for row in fetchall(sql, params)]


@internal_dashboard_bp.get("/api/internal/dashboard/snapshot")
@require_dashboard_token
def snapshot():
    roles = _rows(
        """SELECT u.id AS user_id, u.normalized_phone, u.full_name,
                  u.phone_verified, u.last_login_at, u.created_at,
                  ur.role, ur.registration_channel, ur.verification_status,
                  ur.account_status
           FROM users u JOIN user_roles ur ON ur.user_id=u.id
           ORDER BY u.created_at DESC"""
    )
    payload = {
        "users": roles,
        "farmers": _rows(
            """SELECT id, member_uuid, name, normalized_phone AS phone, crop,
                      location, price, quantity_available, product_image_path,
                      listing_status, verification_status, account_status,
                      phone_verified, registration_channel, kyc_status,
                      is_active, credit_score, created_at, updated_at
               FROM farmers ORDER BY created_at DESC"""
        ),
        "buyers": _rows(
            """SELECT id, name, business_name, normalized_phone AS phone, email,
                      city, state, buyer_type, kyc_status, verification_status,
                      account_status, phone_verified, registration_channel,
                      is_active, created_at, updated_at
               FROM buyers ORDER BY created_at DESC"""
        ),
        "agents": _rows(
            """SELECT id, name, normalized_phone AS phone, location, recruits,
                      verification_status, account_status, phone_verified,
                      registration_channel, is_active, created_at, updated_at
               FROM agents ORDER BY created_at DESC"""
        ),
        "providers": _rows(
            """SELECT id, provider_uuid, name, business_name,
                      normalized_phone AS phone, operating_area, vehicle_type,
                      kyc_status, verification_status, account_status,
                      phone_verified, registration_channel, is_active,
                      completed_jobs, rating, created_at, updated_at
               FROM logistics_providers ORDER BY created_at DESC"""
        ),
        "escrow": _rows(
            """SELECT txn_id, farmer_phone, buyer_phone, crop, quantity_bags,
                      amount, service_fee, product_amount,
                      buyer_platform_fee, seller_platform_fee,
                      logistics_amount, logistics_platform_fee, buyer_total,
                      farmer_settlement_amount, logistics_settlement_amount,
                      sowtrust_total_revenue, status, payment_confirmed_at,
                      payout_status, locked_at, released_at, expires_at
               FROM escrow_ledger ORDER BY locked_at DESC LIMIT 500"""
        ),
        "requests": _rows(
            """SELECT id, buyer_phone, crop, qty_bags, max_price, location,
                      status, created_at FROM buyer_requests
               ORDER BY created_at DESC LIMIT 500"""
        ),
        "logistics": _rows(
            """SELECT logistics_id, txn_id, provider_id, origin, destination,
                      status, quote_amount, platform_fee, settlement_amount,
                      confirmed_at, dispatched_at, delivery_timestamp, created_at
               FROM logistics_log ORDER BY created_at DESC LIMIT 500"""
        ),
        "audit": _rows(
            """SELECT id, actor, action, details, ip_address, created_at
               FROM audit_log ORDER BY created_at DESC LIMIT 500"""
        ),
        "identity_issues": _rows(
            """SELECT id, issue_type, role, table_name, record_id,
                      normalized_phone, details, created_at
               FROM identity_migration_issues
               WHERE resolved_at IS NULL ORDER BY created_at DESC"""
        ),
    }
    return jsonify(payload)


@internal_dashboard_bp.post("/api/internal/dashboard/farmers/<path:phone>/action")
@require_dashboard_token
def farmer_action(phone):
    normalized = normalize_phone(phone)
    action = (request.get_json(silent=True) or {}).get("action", "").upper()
    reason = ((request.get_json(silent=True) or {}).get("reason") or "").strip()
    farmer = fetchone(
        "SELECT * FROM farmers WHERE normalized_phone=? OR phone=?",
        (normalized, normalized),
    ) if normalized else None
    if not farmer:
        return jsonify({"ok": False, "error": "Farmer not found."}), 404
    if action in {"SUSPEND_PROFILE", "REJECT_PROFILE"} and not reason:
        return jsonify({"ok": False, "error": "A reason is required."}), 400

    with get_db() as conn:
        if action == "VERIFY_PROFILE":
            conn.execute(
                """UPDATE farmers SET kyc_status='VERIFIED',
                       verification_status='VERIFIED', updated_at=datetime('now')
                   WHERE id=?""", (farmer["id"],),
            )
            verification, account, active = "VERIFIED", "ACTIVE", 1
        elif action in {"SUSPEND_PROFILE", "REJECT_PROFILE"}:
            verification = "REJECTED" if action == "REJECT_PROFILE" else farmer["verification_status"]
            account, active = "SUSPENDED", 0
            conn.execute(
                """UPDATE farmers SET verification_status=?, account_status='SUSPENDED',
                       is_active=0, listing_status='SUSPENDED',
                       listing_rejection_reason=?, updated_at=datetime('now')
                   WHERE id=?""", (verification, reason, farmer["id"]),
            )
        elif action == "REACTIVATE_PROFILE":
            verification, account, active = farmer["verification_status"], "ACTIVE", 1
            conn.execute(
                """UPDATE farmers SET account_status='ACTIVE', is_active=1,
                       updated_at=datetime('now') WHERE id=?""", (farmer["id"],),
            )
        else:
            return jsonify({"ok": False, "error": "Unsupported action."}), 400

        conn.execute(
            """UPDATE user_roles SET verification_status=?, account_status=?,
                   updated_at=datetime('now')
               WHERE role='FARMER' AND user_id=(
                   SELECT id FROM users WHERE normalized_phone=?)""",
            (verification, account, normalized),
        )
        conn.execute(
            """UPDATE users SET account_status=?, updated_at=datetime('now')
               WHERE normalized_phone=?""", (account, normalized),
        )
        conn.execute(
            """INSERT INTO audit_log(actor, action, details)
               VALUES ('CEO_CONSOLE', ?, ?)""",
            (action, f"PHONE:{normalized} ACTIVE:{active} REASON:{reason}"),
        )
    if action == "VERIFY_PROFILE":
        AgentIncentiveService.evaluate_event(
            "FARMER_VERIFIED", farmer["id"],
            source_reference=f"CEO_CONSOLE:FARMER:{farmer['id']}",
            metadata={"verification_actor": "CEO_CONSOLE"},
        )
    return jsonify({"ok": True})


@internal_dashboard_bp.post("/api/internal/dashboard/escrow/expire")
@require_dashboard_token
def expire_escrow():
    return jsonify({"ok": True, "result": escrow_service.expire_stale_escrows()})
