"""Protected staff views for users, payments, escrow and the CEO console."""
from flask import Blueprint, redirect, render_template

from app.models.database import fetchall
from app.routes.admin_auth import require_admin
from config.settings import config


admin_operations_bp = Blueprint("admin_operations", __name__)


def _rows(sql, params=()):
    return [dict(row) for row in fetchall(sql, params)]


@admin_operations_bp.get("/admin/users")
@require_admin
def users():
    rows = _rows(
        """SELECT u.normalized_phone AS phone, u.full_name, ur.role,
                  ur.registration_channel, ur.verification_status,
                  ur.account_status, u.phone_verified, u.last_login_at,
                  u.created_at
           FROM users u JOIN user_roles ur ON ur.user_id=u.id
           ORDER BY u.created_at DESC LIMIT 500"""
    )
    return render_template(
        "staff/records.html", title="Users", rows=rows,
        columns=("phone", "full_name", "role", "registration_channel",
                 "verification_status", "account_status", "phone_verified",
                 "last_login_at", "created_at"),
    )


@admin_operations_bp.get("/admin/payments")
@require_admin
def payments():
    rows = _rows(
        """SELECT txn_id, payment_reference, buyer_total, amount_paid_kobo,
                  status, payment_confirmed_at, payout_status,
                  locked_at AS created_at
           FROM escrow_ledger ORDER BY locked_at DESC LIMIT 500"""
    )
    return render_template(
        "staff/records.html", title="Payments", rows=rows,
        columns=("txn_id", "payment_reference", "buyer_total",
                 "amount_paid_kobo", "status", "payment_confirmed_at",
                 "payout_status", "created_at"),
    )


@admin_operations_bp.get("/admin/escrow")
@require_admin
def escrow():
    rows = _rows(
        """SELECT txn_id, crop, quantity_bags, buyer_total,
                  farmer_settlement_amount, logistics_settlement_amount,
                  sowtrust_total_revenue, status, locked_at, released_at,
                  expires_at
           FROM escrow_ledger ORDER BY locked_at DESC LIMIT 500"""
    )
    return render_template(
        "staff/records.html", title="Escrow", rows=rows,
        columns=("txn_id", "crop", "quantity_bags", "buyer_total",
                 "farmer_settlement_amount", "logistics_settlement_amount",
                 "sowtrust_total_revenue", "status", "locked_at",
                 "released_at", "expires_at"),
    )


@admin_operations_bp.get("/ceo-console")
@require_admin
def ceo_console():
    return redirect(config.CEO_CONSOLE_URL, code=302)
