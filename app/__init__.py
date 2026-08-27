"""
Sowtrust — Flask Application Factory
"""
import time
from datetime import timedelta

from flask import Flask, abort, redirect, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix
from config.settings import config
from app.services import production_readiness
from app.routes.ussd import ussd_bp
from app.routes.webhooks import webhooks_bp
from app.routes.buyer_web import buyer_web_bp
from app.routes.admin_kyc import admin_kyc_bp
from app.routes.logistics_web import logistics_web_bp
from app.routes.admin_logistics import admin_logistics_bp
from app.routes.admin_documents import admin_documents_bp
from app.routes.admin_products import admin_products_bp
from app.routes.admin_disputes import admin_disputes_bp
from app.routes.admin_audit import admin_audit_bp
from app.routes.agent_web import agent_web_bp
from app.routes.product_media import product_media_bp
from app.routes.public_web import public_web_bp
from app.routes.account_activation import account_activation_bp
from app.routes.staff_auth import staff_auth_bp
from app.routes.internal_dashboard import internal_dashboard_bp
from app.routes.admin_operations import admin_operations_bp
from app.routes.admin_incentives import admin_incentives_bp
from app.utils.csrf import csrf_token, protect_request
from app.models.database import get_db


def create_app() -> Flask:
    production_readiness.enforce_if_configured()
    if config.SENTRY_DSN:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration

        sentry_sdk.init(
            dsn=config.SENTRY_DSN,
            environment=config.ENV,
            integrations=[FlaskIntegration()],
            traces_sample_rate=config.SENTRY_TRACES_SAMPLE_RATE,
            send_default_pii=False,
        )
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.secret_key = config.SECRET_KEY
    app.permanent_session_lifetime = timedelta(hours=config.SESSION_LIFETIME_HOURS)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=config.ENV == "production",
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_DOMAIN=config.SESSION_COOKIE_DOMAIN,
        CSRF_ENABLED=True,
    )
    @app.before_request
    def _canonical_domain():
        if not config.CANONICAL_HOST:
            return None
        forwarded_host = request.headers.get("X-Forwarded-Host", request.host)
        host = forwarded_host.split(",")[0].strip().split(":")[0].lower()
        canonical = config.CANONICAL_HOST.lower()
        if host == f"www.{canonical}":
            return redirect(
                f"https://{canonical}{request.full_path.rstrip('?')}", code=308
            )
        return None

    @app.before_request
    def _basic_rate_limit():
        watched = (
            request.path.startswith("/admin")
            or request.path.startswith("/buyer/login")
            or request.path.startswith("/buyer/register")
            or request.path.startswith("/agent/login")
            or request.path.startswith("/agents/activate")
            or request.path.startswith("/buyers/activate")
            or request.path.startswith("/logistics/activate")
            or request.path.startswith("/ussd")
        )
        if not watched:
            return None
        now = time.time()
        client_ip = request.remote_addr or "unknown"
        bucket_key = f"{client_ip}:{request.path}"
        with get_db() as conn:
            conn.execute(
                "DELETE FROM request_rate_limits WHERE occurred_at < ?",
                (now - 60,),
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM request_rate_limits WHERE bucket_key=? AND occurred_at>=?",
                (bucket_key, now - 60),
            ).fetchone()[0]
            if count >= config.RATE_LIMIT_PER_MINUTE:
                abort(429)
            conn.execute(
                "INSERT INTO request_rate_limits(bucket_key, occurred_at) VALUES (?, ?)",
                (bucket_key, now),
            )
        return None

    app.before_request(protect_request)
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'",
        )
        if config.ENV == "production" and request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if request.path.startswith(("/admin", "/staff", "/api/internal")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    # Register blueprints
    app.register_blueprint(ussd_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(buyer_web_bp)
    app.register_blueprint(admin_kyc_bp)
    app.register_blueprint(logistics_web_bp)
    app.register_blueprint(admin_logistics_bp)
    app.register_blueprint(admin_documents_bp)
    app.register_blueprint(admin_products_bp)
    app.register_blueprint(admin_disputes_bp)
    app.register_blueprint(admin_audit_bp)
    app.register_blueprint(agent_web_bp)
    app.register_blueprint(product_media_bp)
    app.register_blueprint(account_activation_bp)
    app.register_blueprint(staff_auth_bp)
    app.register_blueprint(internal_dashboard_bp)
    app.register_blueprint(admin_operations_bp)
    app.register_blueprint(admin_incentives_bp)
    app.register_blueprint(public_web_bp)

    @app.get("/health")
    def health():
        return {"status": "ok", "platform": "Sowtrust Global v6.0"}, 200

    @app.get("/health/ready")
    def readiness():
        status = production_readiness.check_readiness()
        return status, 200 if status["ready"] else 503

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("public/404.html"), 404

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template(
            "public/error.html", title="Access forbidden",
            message="Your account does not have permission to open this resource.",
        ), 403

    @app.errorhandler(400)
    def bad_request(error):
        return render_template(
            "public/error.html", title="Request could not be completed",
            message=getattr(error, "description", "Check the form and try again."),
        ), 400

    return app
