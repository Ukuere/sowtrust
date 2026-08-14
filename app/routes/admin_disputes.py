"""
Admin dispute review queue.
"""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.routes.admin_auth import require_admin
from app.services import dispute_service

admin_disputes_bp = Blueprint(
    "admin_disputes", __name__, url_prefix="/admin/disputes", template_folder="templates"
)


@admin_disputes_bp.route("/")
@require_admin
def queue():
    disputes = dispute_service.get_disputes()
    return render_template("admin/dispute_queue.html", disputes=disputes)


@admin_disputes_bp.route("/<dispute_id>/resolve", methods=["POST"])
@require_admin
def resolve(dispute_id):
    result = dispute_service.resolve_dispute(
        dispute_id=dispute_id,
        resolution_status=request.form.get("resolution_status", ""),
        resolution=request.form.get("resolution", ""),
        resolved_by=request.authorization.username or "admin",
    )
    if not result["ok"]:
        flash(result["error"], "error")
    else:
        flash("Dispute resolved.", "success")
    return redirect(url_for("admin_disputes.queue"))
