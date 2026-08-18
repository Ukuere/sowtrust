"""
Sowtrust — Admin KYC Review.

Provides the manual-review half of the MVP verification workflow. Each
decision is attributed to an individual database-backed staff account,
and the authorization decorator limits access to eligible staff roles.

Routes:
  GET  /admin/kyc              -> pending buyer verification queue
  POST /admin/kyc/<id>/decide  -> approve or reject one record
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, session

from app.services import buyer_service
from app.routes.admin_auth import require_admin

admin_kyc_bp = Blueprint(
    "admin_kyc", __name__, url_prefix="/admin/kyc", template_folder="templates"
)


@admin_kyc_bp.route("/")
@require_admin
def queue():
    pending = buyer_service.get_pending_kyc_verifications()
    return render_template("admin/kyc_queue.html", pending=pending)


@admin_kyc_bp.route("/<int:verification_id>/decide", methods=["POST"])
@require_admin
def decide(verification_id):
    decision = request.form.get("decision", "")
    reason = request.form.get("rejection_reason", "")
    result = buyer_service.admin_review_kyc(
        verification_id=verification_id,
        decision=decision,
        reviewed_by=session.get("staff_username", "admin"),
        rejection_reason=reason,
    )
    if not result["ok"]:
        flash(result["error"], "error")
    else:
        flash(f"Marked {decision}.", "success")
    return redirect(url_for("admin_kyc.queue"))
