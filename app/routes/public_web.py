"""Public SowTrust homepage, marketplace and unified portal navigation."""
from flask import Blueprint, redirect, render_template, session, url_for

from app.services import product_service
from config.settings import config


public_web_bp = Blueprint(
    "public_web", __name__, template_folder="templates"
)


def _public_context():
    live_ussd = config.USSD_MODE == "LIVE" and bool(config.USSD_PUBLIC_CODE)
    return {
        "ussd_is_live": live_ussd,
        "ussd_code": config.USSD_PUBLIC_CODE if live_ussd else "",
        "ussd_mode": config.USSD_MODE,
        "support_email": config.SUPPORT_EMAIL,
        "support_phone": config.SUPPORT_PHONE,
    }


@public_web_bp.get("/")
def home():
    return render_template(
        "public/home.html",
        products=product_service.list_active_products(limit=6),
        **_public_context(),
    )


@public_web_bp.get("/marketplace")
def marketplace():
    return render_template(
        "public/marketplace.html",
        products=product_service.list_active_products(limit=48),
        **_public_context(),
    )


@public_web_bp.get("/farmers")
def farmers():
    return render_template("public/farmers.html", **_public_context())


@public_web_bp.get("/support")
def support():
    return render_template("public/support.html", **_public_context())


@public_web_bp.get("/privacy")
def privacy():
    return render_template("public/legal.html", document="privacy", **_public_context())


@public_web_bp.get("/terms")
def terms():
    return render_template("public/legal.html", document="terms", **_public_context())


@public_web_bp.get("/faq")
def faq():
    return render_template("public/faq.html", **_public_context())


# Stable, user-facing aliases. Existing singular routes remain compatible.
@public_web_bp.get("/buyers/register")
def buyers_register():
    return redirect(url_for("buyer_web.register"), code=302)


@public_web_bp.get("/buyers")
def buyers_portal():
    target = "buyer_web.browse" if session.get("buyer_phone") else "buyer_web.login"
    return redirect(url_for(target), code=302)


@public_web_bp.get("/buyers/login")
def buyers_login():
    return redirect(url_for("buyer_web.login"), code=302)


@public_web_bp.get("/buyers/dashboard")
def buyers_dashboard():
    return redirect(url_for("buyer_web.browse"), code=302)


@public_web_bp.get("/agents/register")
def agents_register():
    return redirect(url_for("agent_web.register"), code=302)


@public_web_bp.get("/agents")
def agents_portal():
    target = "agent_web.dashboard" if session.get("agent_phone") else "agent_web.login"
    return redirect(url_for(target), code=302)


@public_web_bp.get("/agents/login")
def agents_login():
    return redirect(url_for("agent_web.login"), code=302)


@public_web_bp.get("/agents/dashboard")
def agents_dashboard():
    return redirect(url_for("agent_web.dashboard"), code=302)


@public_web_bp.get("/logistics/dashboard")
def logistics_dashboard():
    return redirect(url_for("logistics_web.dashboard"), code=302)


@public_web_bp.get("/logistics")
def logistics_portal():
    target = "logistics_web.dashboard" if session.get("provider_phone") else "logistics_web.login"
    return redirect(url_for(target), code=302)


@public_web_bp.get("/track-orders")
def track_orders():
    target = "buyer_web.orders" if session.get("buyer_phone") else "buyer_web.login"
    return redirect(url_for(target), code=302)
