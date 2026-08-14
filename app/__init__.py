"""
Sowtrust — Flask Application Factory
"""
from flask import Flask
from config.settings import config
from app.routes.ussd import ussd_bp
from app.routes.webhooks import webhooks_bp
from app.routes.buyer_web import buyer_web_bp
from app.routes.admin_kyc import admin_kyc_bp
from app.routes.logistics_web import logistics_web_bp
from app.routes.admin_logistics import admin_logistics_bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY

    # Register blueprints
    app.register_blueprint(ussd_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(buyer_web_bp)
    app.register_blueprint(admin_kyc_bp)
    app.register_blueprint(logistics_web_bp)
    app.register_blueprint(admin_logistics_bp)

    @app.get("/health")
    def health():
        return {"status": "ok", "platform": "Sowtrust Global v6.0"}, 200

    return app
