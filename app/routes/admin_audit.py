"""
Admin audit and readiness dashboard.
"""
from flask import Blueprint, render_template

from app.models.database import fetchall
from app.routes.admin_auth import require_admin
from app.services import production_readiness

admin_audit_bp = Blueprint(
    "admin_audit", __name__, url_prefix="/admin/audit", template_folder="templates"
)


@admin_audit_bp.route("/")
@require_admin
def dashboard():
    audit_rows = fetchall(
        "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 100"
    )
    return render_template(
        "admin/audit.html",
        readiness=production_readiness.check_readiness(),
        audit_log=[dict(r) for r in audit_rows],
    )
