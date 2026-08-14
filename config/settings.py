"""
Sowtrust Global — Centralised Configuration
All settings are loaded from environment variables (via .env).
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Africa's Talking
    AT_USERNAME: str = os.getenv("AT_USERNAME", "sandbox")
    AT_API_KEY: str = os.getenv("AT_API_KEY", "")  # no default — must be set in .env, never committed

    # Flask
    SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    PORT: int = int(os.getenv("FLASK_PORT", 5000))
    DEBUG: bool = os.getenv("FLASK_ENV", "production") == "development"
    ENV: str = os.getenv("FLASK_ENV", "production")
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "")
    ENFORCE_PRODUCTION_CONFIG: bool = os.getenv("ENFORCE_PRODUCTION_CONFIG", "0") == "1"
    RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))

    # Database
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "agrihub.db")
    UPLOAD_FOLDER: str = os.getenv("UPLOAD_FOLDER", "uploads")
    STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "local")

    # Business Rules
    SERVICE_FEE_PERCENT: float = float(os.getenv("SERVICE_FEE_PERCENT", 2.5))
    ESCROW_EXPIRY_HOURS: int = int(os.getenv("ESCROW_EXPIRY_HOURS", 72))
    USSD_SESSION_TTL: int = int(os.getenv("USSD_SESSION_TTL_SECONDS", 120))

    # NOTE: crops/products are now farmer-entered and stored dynamically
    # in the `products` table (see app/services/product_service.py) —
    # no longer a hardcoded list here.

    # Paystack (real payment collection + settlement)
    PAYSTACK_SECRET_KEY: str = os.getenv("PAYSTACK_SECRET_KEY", "")
    PAYSTACK_PUBLIC_KEY: str = os.getenv("PAYSTACK_PUBLIC_KEY", "")

    # How long a buyer has to complete their bank transfer before the
    # order is auto-cancelled (no money ever arrived, so no refund needed).
    PAYMENT_PENDING_TIMEOUT_MINUTES: int = int(os.getenv("PAYMENT_PENDING_TIMEOUT_MINUTES", "60"))

    # Common banks + digital wallets farmers/agents can pick from when
    # adding a payout account. Digital wallets (OPay, Kuda, PalmPay,
    # Moniepoint) are included deliberately — they're how a farmer with
    # NO traditional bank account still gets a real NUBAN to be paid into,
    # typically opened in minutes with just BVN or NIN.
    #
    # ⚠️ VERIFY BEFORE GOING LIVE: traditional bank codes below are
    # standard CBN codes and stable. The digital-wallet codes are more
    # likely to change/vary — before production use, pull the authoritative,
    # current list from Paystack directly: GET https://api.paystack.co/bank
    # and replace this dict with confirmed values.
    BANKS: dict = {
        "1":  {"name": "OPay",           "code": "999992"},
        "2":  {"name": "PalmPay",        "code": "999991"},
        "3":  {"name": "Kuda Bank",      "code": "50211"},
        "4":  {"name": "Moniepoint MFB", "code": "50515"},
        "5":  {"name": "Access Bank",    "code": "044"},
        "6":  {"name": "GTBank",         "code": "058"},
        "7":  {"name": "Zenith Bank",    "code": "057"},
        "8":  {"name": "UBA",            "code": "033"},
        "9":  {"name": "First Bank",     "code": "011"},
        "10": {"name": "Fidelity Bank",  "code": "070"},
    }

    # Dashboard
    DASHBOARD_PASSWORD: str = os.getenv("DASHBOARD_ADMIN_PASSWORD", "changeme")
    DASHBOARD_USERNAME: str = os.getenv("DASHBOARD_ADMIN_USERNAME", "")


config = Config()
