"""
Admin product listing review queue.
"""
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.routes.admin_auth import require_admin
from app.services import document_storage, notification_service, product_service

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
        reviewed_by=session.get("staff_username", "admin"),
        rejection_reason=reason,
    )
    if not result["ok"]:
        flash(result["error"], "error")
    else:
        flash(f"Listing marked {decision}.", "success")
    return redirect(url_for("admin_products.queue"))


@admin_products_bp.route("/<farmer_phone>/image", methods=["POST"])
@require_admin
def update_image(farmer_phone):
    saved = document_storage.save_product_image(request.files.get("product_image"))
    if not saved["ok"]:
        flash(saved["error"], "error")
    else:
        result = product_service.update_listing_image(
            farmer_phone, saved["path"], session.get("staff_username", "admin")
        )
        flash(result.get("error", "Product image updated."),
              "success" if result["ok"] else "error")
    return redirect(url_for("admin_products.queue"))
