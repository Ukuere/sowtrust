"""
Sowtrust — Flask Application Factory
"""
from flask import Flask
from config.settings import config
from app.routes.ussd import ussd_bp
from app.routes.webhooks import webhooks_bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY

    # Register blueprints
    app.register_blueprint(ussd_bp)
    app.register_blueprint(webhooks_bp)

    @app.get("/health")
    def health():
        return {"status": "ok", "platform": "Sowtrust Global v6.0"}, 200

    return app
