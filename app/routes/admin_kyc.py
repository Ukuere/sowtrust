"""
Sowtrust — Admin KYC Review.

Spec section 2: "manual verification initially, with the ability to
integrate a professional third-party KYC/identity verification provider
later." This is the manual half — a deliberately minimal queue, not a
full admin dashboard (you may already have one elsewhere given
DASHBOARD_PASSWORD exists in your .env; I don't have visibility
into it since it predates what I reconstructed, so this is self-contained
and doesn't assume anything about it).

Auth: HTTP Basic Auth against DASHBOARD_PASSWORD — same credential
your existing admin tooling already uses, no new secret to manage. This
is fine for a single-operator MVP; if multiple reviewers need distinct
identities (so `reviewed_by` means something more specific than
"admin"), that needs real accounts — flagging, not building, since you
didn't ask for that yet.

Routes:
  GET  /admin/kyc              -> pending buyer verification queue
  POST /admin/kyc/<id>/decide  -> approve or reject one record
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash

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
        reviewed_by=request.authorization.username or "admin",
        rejection_reason=reason,
    )
    if not result["ok"]:
        flash(result["error"], "error")
    else:
        flash(f"Marked {decision}.", "success")
    return redirect(url_for("admin_kyc.queue"))
