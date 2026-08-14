"""
Sowtrust — Flask Application Factory
"""
import time
from collections import defaultdict, deque

from flask import Flask, abort, request
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


def create_app() -> Flask:
    production_readiness.enforce_if_configured()
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY
    rate_buckets = defaultdict(deque)

    @app.before_request
    def _basic_rate_limit():
        watched = (
            request.path.startswith("/admin")
            or request.path.startswith("/buyer/login")
            or request.path.startswith("/buyer/register")
            or request.path.startswith("/agent/login")
            or request.path.startswith("/ussd")
        )
        if not watched:
            return None
        now = time.time()
        key = (request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown").split(",")[0].strip()
        bucket = rate_buckets[key]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= config.RATE_LIMIT_PER_MINUTE:
            abort(429)
        bucket.append(now)
        return None

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
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

    @app.get("/health")
    def health():
        return {"status": "ok", "platform": "Sowtrust Global v6.0"}, 200

    @app.get("/health/ready")
    def readiness():
        status = production_readiness.check_readiness()
        return status, 200 if status["ready"] else 503

    return app
