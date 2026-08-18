"""Staff login, logout and administration landing page."""
from urllib.parse import urlsplit

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.routes.admin_auth import require_admin
from app.services import staff_service


staff_auth_bp = Blueprint("staff_auth", __name__, template_folder="templates")


def _safe_next(value):
    parsed = urlsplit(value or "")
    return value if not parsed.scheme and not parsed.netloc and (value or "").startswith("/") else None


@staff_auth_bp.route("/staff/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        result = staff_service.authenticate(
            request.form.get("username", ""), request.form.get("password", "")
        )
        if not result["ok"]:
            flash(result["error"], "error")
            return render_template("staff/login.html"), 401
        staff = result["staff"]
        session.clear()
        session["staff_user_id"] = staff["id"]
        session["staff_username"] = staff["username"]
        session["staff_role"] = staff["role"]
        session.permanent = True
        return redirect(_safe_next(request.args.get("next")) or url_for("staff_auth.admin_home"))
    return render_template("staff/login.html")


@staff_auth_bp.post("/staff/logout")
def logout():
    session.clear()
    flash("Staff session ended.", "success")
    return redirect(url_for("staff_auth.login"))


@staff_auth_bp.get("/admin")
@staff_auth_bp.get("/admin/")
@require_admin
def admin_home():
    return render_template("staff/admin_home.html")
