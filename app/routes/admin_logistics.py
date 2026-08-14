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
from flask import Blueprint, render_template, request, redirect, url_for, flash

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
    providers = logistics_service.get_verified_providers()
    return render_template(
        "admin/logistics_queue.html",
        pending=pending,
        quote_requests=quote_requests,
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
        reviewed_by=request.authorization.username or "admin",
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
        quoted_by=request.authorization.username or "admin",
        expires_at=request.form.get("expires_at", "").strip() or None,
    )
    if not result["ok"]:
        flash(result["error"], "error")
    else:
        flash("Logistics quote locked. Buyer can now accept before payment.", "success")
    return redirect(url_for("admin_logistics.queue"))
