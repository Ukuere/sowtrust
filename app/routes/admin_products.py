"""
Admin product listing review queue.
"""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.routes.admin_auth import require_admin
from app.services import notification_service, product_service

admin_products_bp = Blueprint(
    "admin_products", __name__, url_prefix="/admin/products", template_folder="templates"
)


@admin_products_bp.route("/")
@require_admin
def queue():
    listings = product_service.get_pending_product_listings()
    notifications = notification_service.get_recent_notifications(limit=20)
    return render_template(
        "admin/product_queue.html",
        listings=listings,
        notifications=notifications,
    )


@admin_products_bp.route("/<farmer_phone>/decide", methods=["POST"])
@require_admin
def decide(farmer_phone):
    decision = request.form.get("decision", "")
    reason = request.form.get("rejection_reason", "")
    result = product_service.review_product_listing(
        farmer_phone=farmer_phone,
        decision=decision,
        reviewed_by=request.authorization.username or "admin",
        rejection_reason=reason,
    )
    if not result["ok"]:
        flash(result["error"], "error")
    else:
        flash(f"Listing marked {decision}.", "success")
    return redirect(url_for("admin_products.queue"))
