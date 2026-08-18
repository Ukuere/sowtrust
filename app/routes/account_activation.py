"""OTP activation and passwordless login for existing cross-channel accounts."""
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.services import buyer_service, identity_service


account_activation_bp = Blueprint(
    "account_activation", __name__, template_folder="templates"
)


ROLE_CONFIG = {
    "BUYER": {
        "title": "Buyer account access",
        "session_key": "buyer_phone",
        "destination": "buyer_web.browse",
    },
    "AGENT": {
        "title": "Agent account activation",
        "session_key": "agent_phone",
        "destination": "agent_web.dashboard",
    },
    "LOGISTICS": {
        "title": "Logistics account activation",
        "session_key": "provider_phone",
        "destination": "logistics_web.dashboard",
    },
}


def _activate(role):
    cfg = ROLE_CONFIG[role]
    phone = request.form.get("phone", "") or request.args.get("phone", "")
    stage = "request"

    if request.method == "POST" and request.form.get("action") == "request":
        result = identity_service.request_otp(
            phone, role, "ACTIVATE",
            request.remote_addr or "",
        )
        if not result["ok"]:
            flash(result["error"], "error")
        else:
            phone = result["phone"]
            session[f"activation_phone_{role}"] = phone
            stage = "verify"
            flash(f"A six-digit code was sent to {result['masked_phone']}.", "success")

    elif request.method == "POST" and request.form.get("action") == "verify":
        phone = session.get(f"activation_phone_{role}") or phone
        password = request.form.get("password", "")
        if role == "BUYER" and password and len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            stage = "verify"
        else:
            result = identity_service.verify_otp(
                phone, role, request.form.get("otp", ""), "ACTIVATE"
            )
            if not result["ok"]:
                flash(result["error"], "error")
                stage = "verify"
            else:
                if role == "BUYER" and password:
                    password_result = buyer_service.set_buyer_password(result["phone"], password)
                    if not password_result["ok"]:
                        flash(password_result["error"], "error")
                        stage = "verify"
                        return render_template(
                            "shared/activate.html", role=role, config=cfg,
                            stage=stage, phone=phone,
                        ), 400
                session[cfg["session_key"]] = result["phone"]
                session.permanent = True
                session.pop(f"activation_phone_{role}", None)
                flash("Phone verified. Welcome to your SowTrust account.", "success")
                return redirect(url_for(cfg["destination"]))

    elif session.get(f"activation_phone_{role}"):
        phone = session[f"activation_phone_{role}"]
        stage = "verify"

    return render_template(
        "shared/activate.html", role=role, config=cfg, stage=stage, phone=phone
    )


@account_activation_bp.route("/buyers/activate", methods=["GET", "POST"])
def activate_buyer():
    return _activate("BUYER")


@account_activation_bp.route("/agents/activate", methods=["GET", "POST"])
def activate_agent():
    return _activate("AGENT")


@account_activation_bp.route("/logistics/activate", methods=["GET", "POST"])
def activate_logistics():
    return _activate("LOGISTICS")
