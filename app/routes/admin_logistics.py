"""
Sowtrust — Admin Logistics Provider KYC Review.

Mirrors admin_kyc.py exactly, for the other side of the marketplace.
Section 5's "strict verification" requirement is really enforced by
assign_provider() in logistics_service.py (already checks kyc_status ==
'VERIFIED', built in the step 2 pass) — this queue is just what lets a
provider actually reach that status.

Routes:
  GET  /admin/logistics              -> pending provider verification queue
  POST /admin/logistics/<id>/decide  -> approve or reject one record
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from app.services import logistics_service
from app.routes.admin_auth import require_admin

admin_logistics_bp = Blueprint(
    "admin_logistics", __name__, url_prefix="/admin/logistics", template_folder="templates"
)


@admin_logistics_bp.route("/")
@require_admin
def queue():
    pending = logistics_service.get_pending_provider_kyc_verifications()
    quote_requests = logistics_service.get_pending_quote_requests()
    locked_quotes = logistics_service.get_locked_quotes_for_operations()
    providers = logistics_service.get_verified_providers()
    return render_template(
        "admin/logistics_queue.html",
        pending=pending,
        quote_requests=quote_requests,
        locked_quotes=locked_quotes,
        providers=providers,
    )


@admin_logistics_bp.route("/<int:verification_id>/decide", methods=["POST"])
@require_admin
def decide(verification_id):
    decision = request.form.get("decision", "")
    reason = request.form.get("rejection_reason", "")
    result = logistics_service.admin_review_provider_kyc(
        verification_id=verification_id,
        decision=decision,
        reviewed_by=session.get("staff_username", "admin"),
        rejection_reason=reason,
    )
    if not result["ok"]:
        flash(result["error"], "error")
    else:
        flash(f"Marked {decision}.", "success")
    return redirect(url_for("admin_logistics.queue"))


@admin_logistics_bp.route("/quotes/<txn_id>/lock", methods=["POST"])
@require_admin
def lock_quote(txn_id):
    try:
        quote_amount = float(request.form.get("quoted_amount", "0").replace(",", ""))
    except ValueError:
        quote_amount = 0
    result = logistics_service.record_quote(
        txn_id=txn_id,
        quote_amount=quote_amount,
        origin=request.form.get("pickup_location", "").strip(),
        destination=request.form.get("delivery_location", "").strip(),
        logistics_provider_id=request.form.get("logistics_provider_id", "").strip() or None,
        quoted_by=session.get("staff_username", "admin"),
        expires_at=request.form.get("expires_at", "").strip() or None,
    )
    if not result["ok"]:
        flash(result["error"], "error")
    else:
        flash("Logistics quote locked. Buyer can now accept before payment.", "success")
    return redirect(url_for("admin_logistics.queue"))


@admin_logistics_bp.post("/quotes/<txn_id>/replace-provider")
@require_admin
def replace_provider(txn_id):
    try:
        amount = float(request.form.get("proposed_amount", "0").replace(",", ""))
    except ValueError:
        amount = 0
    result = logistics_service.request_provider_replacement(
        txn_id=txn_id,
        provider_ref=request.form.get("logistics_provider_id", "").strip(),
        proposed_amount=amount,
        requested_by=session.get("staff_username", "admin"),
        reason=request.form.get("reason", "").strip(),
    )
    if not result["ok"]:
        flash(result["error"], "error")
    elif result["buyer_approval_required"]:
        flash("Higher replacement quote recorded. Buyer approval is required before payment.", "success")
    else:
        flash("Replacement provider applied without changing the locked buyer price.", "success")
    return redirect(url_for("admin_logistics.queue"))
