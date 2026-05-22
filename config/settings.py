"""
AgriHub Global — Centralised Configuration
All settings are loaded from environment variables (via .env).
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Africa's Talking
    AT_USERNAME: str = os.getenv("AT_USERNAME", "sandbox")
    AT_API_KEY: str = os.getenv("AT_API_KEY", "")

    # Flask
    SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    PORT: int = int(os.getenv("FLASK_PORT", 5000))
    DEBUG: bool = os.getenv("FLASK_ENV", "production") == "development"

    # Database
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "agrihub.db")

    # Business Rules
    SERVICE_FEE_PERCENT: float = float(os.getenv("SERVICE_FEE_PERCENT", 2.5))
    ESCROW_EXPIRY_HOURS: int = int(os.getenv("ESCROW_EXPIRY_HOURS", 72))
    USSD_SESSION_TTL: int = int(os.getenv("USSD_SESSION_TTL_SECONDS", 120))

    # Crops catalogue
    CROPS: dict = {
        "1": "Maize",
        "2": "Rice",
        "3": "Cassava",
        "4": "Yam",
        "5": "Soybeans",
        "6": "Palm Oil",
        "7": "Groundnut",
    }

    # Dashboard
    DASHBOARD_PASSWORD: str = os.getenv("DASHBOARD_ADMIN_PASSWORD", "changeme")


config = Config()
