"""
Sowtrust Agent Web Portal.

MVP purpose: field agents assist verified farmers by capturing product
media and submitting listings for admin publication review.
"""
from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.models.database import fetchone
from app.services import document_storage, product_service
from app.utils.security import verify_and_upgrade_pin

agent_web_bp = Blueprint(
    "agent_web", __name__, url_prefix="/agent", template_folder="templates"
)


def agent_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("agent_phone"):
            return redirect(url_for("agent_web.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@agent_web_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        pin = request.form.get("pin", "").strip()
        agent = fetchone("SELECT * FROM agents WHERE phone=? AND is_active=1", (phone,))
        if not agent or not verify_and_upgrade_pin("agents", phone, pin, agent["pin_hash"]):
            flash("Invalid agent phone or PIN.", "error")
            return render_template("agent/login.html"), 400
        session["agent_phone"] = phone
        return redirect(request.args.get("next") or url_for("agent_web.dashboard"))
    return render_template("agent/login.html")


@agent_web_bp.route("/logout")
def logout():
    session.pop("agent_phone", None)
    flash("Logged out.", "success")
    return redirect(url_for("agent_web.login"))


@agent_web_bp.route("/")
@agent_login_required
def dashboard():
    agent = fetchone("SELECT * FROM agents WHERE phone=?", (session["agent_phone"],))
    return render_template("agent/dashboard.html", agent=dict(agent))


@agent_web_bp.route("/listings/new", methods=["GET", "POST"])
@agent_login_required
def new_listing():
    if request.method == "POST":
        image = document_storage.save_product_image(request.files.get("product_image"))
        if not image["ok"]:
            flash(image["error"], "error")
            return render_template("agent/new_listing.html", form=request.form), 400

        try:
            price = float(request.form.get("price", "0").replace(",", ""))
        except ValueError:
            price = 0
        try:
            quantity = int(request.form.get("quantity_available", "0"))
        except ValueError:
            quantity = 0

        result = product_service.submit_agent_product_listing(
            agent_phone=session["agent_phone"],
            farmer_phone=request.form.get("farmer_phone", "").strip(),
            crop=request.form.get("crop", ""),
            price=price,
            location=request.form.get("location", ""),
            description=request.form.get("description", ""),
            quantity_available=quantity,
            image_path=image["path"],
        )
        if not result["ok"]:
            flash(result["error"], "error")
            return render_template("agent/new_listing.html", form=request.form), 400

        flash("Product listing submitted for admin review.", "success")
        return redirect(url_for("agent_web.dashboard"))

    return render_template("agent/new_listing.html", form={})
