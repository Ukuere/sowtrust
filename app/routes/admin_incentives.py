"""Staff review and payout controls for the agent incentive ledger."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.models.database import fetchall
from app.routes.admin_auth import require_admin
from app.services.agent_incentive_service import AgentIncentiveService
from app.services.agent_payout_service import AgentPayoutService


admin_incentives_bp = Blueprint(
    "admin_incentives", __name__, url_prefix="/admin/incentives",
    template_folder="templates",
)


def _actor():
    return session.get("staff_username", "admin")


def _can_manage_payouts():
    return session.get("staff_role") in {"ADMIN", "OPERATIONS"}


def _require_payout_role():
    if not _can_manage_payouts():
        return {"ok": False, "error": "Only administrators and operations staff can manage payouts."}
    return None


def _to_kobo(value):
    try:
        amount = Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError("Enter a valid policy amount.")
    if amount < 0:
        raise ValueError("Policy amount cannot be negative.")
    return int(amount * 100)


def _naira(kobo):
    return f"NGN {(int(kobo or 0) / 100):,.2f}"


@admin_incentives_bp.get("/")
@require_admin
def dashboard():
    agent_id = request.args.get("agent_id", type=int)
    farmer_id = request.args.get("farmer_id", type=int)
    status = request.args.get("status", "").strip().upper()
    incentive_code = request.args.get("incentive_code", "").strip().upper()
    flagged_only = request.args.get("flagged") == "1"
    return render_template(
        "admin/incentives.html",
        entries=AgentIncentiveService.list_entries(
            agent_id=agent_id, farmer_id=farmer_id, status=status,
            incentive_code=incentive_code, flagged_only=flagged_only,
        ),
        policies=[dict(row) for row in fetchall(
            "SELECT * FROM agent_incentive_policies ORDER BY id"
        )],
        batches=AgentPayoutService.list_batches(),
        agents=[dict(row) for row in fetchall(
            "SELECT id, name, phone FROM agents ORDER BY name"
        )],
        farmers=[dict(row) for row in fetchall(
            "SELECT id, name, phone FROM farmers ORDER BY name"
        )],
        filters={
            "agent_id": agent_id, "farmer_id": farmer_id, "status": status,
            "incentive_code": incentive_code, "flagged": flagged_only,
        },
        can_manage_payouts=_can_manage_payouts(),
        naira=_naira,
    )


@admin_incentives_bp.post("/entries/<int:entry_id>/approve")
@require_admin
def approve_entry(entry_id):
    result = AgentIncentiveService.approve_earning(entry_id, _actor())
    flash(result.get("error", "Incentive approved."), "success" if result["ok"] else "error")
    return redirect(url_for("admin_incentives.dashboard"))


@admin_incentives_bp.post("/entries/<int:entry_id>/reject")
@require_admin
def reject_entry(entry_id):
    result = AgentIncentiveService.reject_earning(
        entry_id, _actor(), request.form.get("reason", "")
    )
    flash(result.get("error", "Incentive rejected."), "success" if result["ok"] else "error")
    return redirect(url_for("admin_incentives.dashboard"))


@admin_incentives_bp.post("/entries/<int:entry_id>/reverse")
@require_admin
def reverse_entry(entry_id):
    denied = _require_payout_role()
    result = denied or AgentIncentiveService.reverse_earning(
        entry_id, _actor(), request.form.get("reason", "")
    )
    flash(result.get("error", "Incentive reversed."), "success" if result["ok"] else "error")
    return redirect(url_for("admin_incentives.dashboard"))


@admin_incentives_bp.post("/policies/<int:policy_id>")
@require_admin
def update_policy(policy_id):
    denied = _require_payout_role()
    if denied:
        result = denied
    else:
        try:
            result = AgentIncentiveService.update_policy(
                policy_id, _actor(),
                amount_kobo=_to_kobo(request.form.get("amount")),
                enabled=1 if request.form.get("enabled") == "1" else 0,
                requires_admin_review=(
                    1 if request.form.get("requires_admin_review") == "1" else 0
                ),
                qualifying_transaction_count=(
                    int(request.form["qualifying_transaction_count"])
                    if request.form.get("qualifying_transaction_count") else None
                ),
                qualifying_period_days=(
                    int(request.form["qualifying_period_days"])
                    if request.form.get("qualifying_period_days") else None
                ),
            )
        except (ValueError, InvalidOperation):
            result = {"ok": False, "error": "Enter valid non-negative policy values."}
    flash(result.get("error", "Policy updated."), "success" if result["ok"] else "error")
    return redirect(url_for("admin_incentives.dashboard"))


@admin_incentives_bp.post("/batches")
@require_admin
def create_batch():
    denied = _require_payout_role()
    result = denied or AgentPayoutService.create_batch(
        request.form.get("period_start", ""), request.form.get("period_end", ""),
        request.form.get("frequency", "WEEKLY"), _actor(),
        request.form.get("notes", ""),
    )
    flash(result.get("error", "Payout batch created."), "success" if result["ok"] else "error")
    return redirect(url_for("admin_incentives.dashboard"))


@admin_incentives_bp.post("/batches/<int:batch_id>/approve")
@require_admin
def approve_batch(batch_id):
    denied = _require_payout_role()
    result = denied or AgentPayoutService.approve_batch(batch_id, _actor())
    flash(result.get("error", "Payout batch approved."), "success" if result["ok"] else "error")
    return redirect(url_for("admin_incentives.dashboard"))


@admin_incentives_bp.post("/batches/<int:batch_id>/paid")
@require_admin
def mark_batch_paid(batch_id):
    denied = _require_payout_role()
    result = denied or AgentPayoutService.mark_paid(
        batch_id, _actor(), request.form.get("payment_reference", "")
    )
    flash(result.get("error", "Payout payment confirmed."), "success" if result["ok"] else "error")
    return redirect(url_for("admin_incentives.dashboard"))


@admin_incentives_bp.post("/batches/<int:batch_id>/failed")
@require_admin
def mark_batch_failed(batch_id):
    denied = _require_payout_role()
    result = denied or AgentPayoutService.mark_failed(
        batch_id, _actor(), request.form.get("reason", "")
    )
    flash(result.get("error", "Payout batch marked failed."), "success" if result["ok"] else "error")
    return redirect(url_for("admin_incentives.dashboard"))
