"""
Sowtrust Agent Web Portal.

MVP purpose: field agents assist verified farmers by capturing product
media and submitting listings for admin publication review.
"""
from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.models.database import fetchone, get_db
from app.services import document_storage, product_service, identity_service
from app.utils.phone import normalize_phone
from app.utils.security import hash_pin

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


@agent_web_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = normalize_phone(request.form.get("phone", ""))
        location = request.form.get("location", "").strip()
        pin = request.form.get("pin", "").strip()
        if len(name) < 2 or not phone or not location or len(pin) != 4 or not pin.isdigit():
            flash("Enter your name, location, valid phone number, and a four-digit PIN.", "error")
            return render_template("agent/register.html"), 400

        existing = fetchone(
            "SELECT id FROM agents WHERE normalized_phone=? OR phone=?",
            (phone, phone),
        )
        if existing:
            flash(
                "Your SowTrust agent account already exists. Verify your phone number to access the portal.",
                "success",
            )
            return redirect(url_for("account_activation.activate_agent", phone=phone))

        with get_db() as conn:
            cursor = conn.execute(
                """INSERT INTO agents
                   (name, phone, normalized_phone, registration_channel,
                    verification_status, account_status, phone_verified,
                    pin_hash, location, is_active, created_at, updated_at)
                   VALUES (?, ?, ?, 'WEB', 'PENDING', 'ACTIVE', 0,
                           ?, ?, 1, datetime('now'), datetime('now'))""",
                (name, phone, phone, hash_pin(pin), location),
            )
            agent_id = cursor.lastrowid
            conn.execute(
                "INSERT INTO audit_log(actor, action, details) "
                "VALUES (?, 'AGENT_REGISTERED', 'CHANNEL:WEB')",
                (phone,),
            )
        identity_service.ensure_user_role(phone, "AGENT", name, "WEB", False, agent_id)
        otp = identity_service.request_otp(
            phone, "AGENT", "ACTIVATE",
            request.remote_addr or "",
        )
        if not otp["ok"]:
            flash(otp["error"], "error")
            return redirect(url_for("account_activation.activate_agent", phone=phone))
        session["activation_phone_AGENT"] = phone
        flash(f"Account created. A verification code was sent to {otp['masked_phone']}.", "success")
        return redirect(url_for("account_activation.activate_agent"))
    return render_template("agent/register.html")


@agent_web_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        pin = request.form.get("pin", "").strip()
        result = identity_service.authenticate_role_pin(phone, "AGENT", pin)
        if not result["ok"]:
            flash(result["error"], "error")
            return render_template("agent/login.html"), 400
        session["agent_phone"] = result["phone"]
        session.permanent = True
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
        upload = request.files.get("product_image")
        image = {"ok": True, "path": None}
        if upload and upload.filename:
            image = document_storage.save_product_image(upload)
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

        flash("Product listing published. Operations can verify or update it later.", "success")
        return redirect(url_for("agent_web.dashboard"))

    return render_template("agent/new_listing.html", form={})
