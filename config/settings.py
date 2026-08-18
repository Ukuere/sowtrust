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
    USSD_MODE: str = os.getenv("USSD_MODE", "SANDBOX").upper()
    USSD_PUBLIC_CODE: str = os.getenv("USSD_PUBLIC_CODE", "")

    # Flask
    SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
    PORT: int = int(os.getenv("FLASK_PORT", 5000))
    DEBUG: bool = os.getenv("FLASK_ENV", "production") == "development"
    ENV: str = os.getenv("FLASK_ENV", "production")
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "")
    CANONICAL_HOST: str = os.getenv("CANONICAL_HOST", "")
    ENFORCE_PRODUCTION_CONFIG: bool = os.getenv("ENFORCE_PRODUCTION_CONFIG", "0") == "1"
    RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "120"))
    SESSION_COOKIE_DOMAIN: str | None = os.getenv("SESSION_COOKIE_DOMAIN") or None
    SESSION_LIFETIME_HOURS: int = int(os.getenv("SESSION_LIFETIME_HOURS", "8"))

    # Database
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "sowtrust.db")
    UPLOAD_FOLDER: str = os.getenv("UPLOAD_FOLDER", "uploads")
    STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "local")
    OBJECT_STORAGE_BUCKET: str = os.getenv("OBJECT_STORAGE_BUCKET", "")
    OBJECT_STORAGE_REGION: str = os.getenv("OBJECT_STORAGE_REGION", "auto")
    OBJECT_STORAGE_ENDPOINT: str = os.getenv("OBJECT_STORAGE_ENDPOINT", "")
    OBJECT_STORAGE_ACCESS_KEY: str = os.getenv("OBJECT_STORAGE_ACCESS_KEY", "")
    OBJECT_STORAGE_SECRET_KEY: str = os.getenv("OBJECT_STORAGE_SECRET_KEY", "")
    OBJECT_STORAGE_PREFIX: str = os.getenv("OBJECT_STORAGE_PREFIX", "sowtrust")

    # Shared account activation / passwordless login
    OTP_TTL_SECONDS: int = int(os.getenv("OTP_TTL_SECONDS", "600"))
    OTP_RESEND_SECONDS: int = int(os.getenv("OTP_RESEND_SECONDS", "60"))
    OTP_MAX_ATTEMPTS: int = int(os.getenv("OTP_MAX_ATTEMPTS", "5"))

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
    DASHBOARD_PASSWORD: str = os.getenv(
        "DASHBOARD_ADMIN_PASSWORD", os.getenv("DASHBOARD_PASSWORD", "changeme")
    )
    DASHBOARD_USERNAME: str = os.getenv(
        "DASHBOARD_ADMIN_USERNAME", os.getenv("DASHBOARD_USERNAME", "")
    )
    DASHBOARD_API_TOKEN: str = os.getenv("DASHBOARD_API_TOKEN", "")
    BACKEND_API_URL: str = os.getenv("BACKEND_API_URL", "http://127.0.0.1:5000")
    SUPPORT_EMAIL: str = os.getenv("SUPPORT_EMAIL", "support@sowtrust.com")
    SUPPORT_PHONE: str = os.getenv("SUPPORT_PHONE", "")
    CEO_CONSOLE_URL: str = os.getenv("CEO_CONSOLE_URL", "https://ops.sowtrust.com")
    SENTRY_DSN: str = os.getenv("SENTRY_DSN", "")
    SENTRY_TRACES_SAMPLE_RATE: float = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.05"))
    BACKUP_DIR: str = os.getenv("BACKUP_DIR", "backups")
    BACKUP_RETENTION_COUNT: int = int(os.getenv("BACKUP_RETENTION_COUNT", "14"))
    BACKUP_TO_OBJECT_STORAGE: bool = os.getenv("BACKUP_TO_OBJECT_STORAGE", "0") == "1"


config = Config()
