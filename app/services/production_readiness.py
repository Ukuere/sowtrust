"""
Production readiness checks for MVP launch.
"""
from pathlib import Path
import sqlite3

from config.settings import config


def check_readiness() -> dict:
    issues = []
    warnings = []

    if config.SECRET_KEY == "dev-secret-change-me":
        issues.append("FLASK_SECRET_KEY is still using the development default.")
    if config.DASHBOARD_PASSWORD == "changeme":
        issues.append("DASHBOARD_ADMIN_PASSWORD is still using the development default.")
    if not config.DASHBOARD_USERNAME:
        issues.append("DASHBOARD_ADMIN_USERNAME is not configured.")
    if not config.DASHBOARD_API_TOKEN:
        issues.append("DASHBOARD_API_TOKEN is not configured for the backend/console connection.")
    if not config.PAYSTACK_SECRET_KEY:
        issues.append("PAYSTACK_SECRET_KEY is not configured.")
    if not config.PAYSTACK_PUBLIC_KEY:
        warnings.append("PAYSTACK_PUBLIC_KEY is not configured.")
    if not config.AT_API_KEY:
        warnings.append("AT_API_KEY is not configured; SMS delivery will fail outside stubs/sandbox.")
    storage_backend = config.STORAGE_BACKEND.lower()
    if storage_backend == "local":
        upload_root = Path(config.UPLOAD_FOLDER)
        warnings.append(
            f"STORAGE_BACKEND=local; ensure {upload_root} is on persistent storage or migrate to object storage."
        )
    elif storage_backend in {"s3", "r2", "object"}:
        if not all((config.OBJECT_STORAGE_BUCKET, config.OBJECT_STORAGE_ACCESS_KEY,
                    config.OBJECT_STORAGE_SECRET_KEY)):
            issues.append("Object storage is selected but bucket/access credentials are incomplete.")
    else:
        issues.append("STORAGE_BACKEND must be local, s3, r2, or object.")
    if not config.SENTRY_DSN:
        warnings.append("SENTRY_DSN is not configured; production exception monitoring is disabled.")
    if not config.PUBLIC_BASE_URL:
        warnings.append("PUBLIC_BASE_URL is not set; generated external links may be wrong in production.")
    if not config.CANONICAL_HOST:
        warnings.append("CANONICAL_HOST is not set; www-to-apex redirect is disabled.")
    if config.USSD_MODE == "LIVE" and not config.USSD_PUBLIC_CODE:
        issues.append("USSD_MODE=LIVE but USSD_PUBLIC_CODE is empty.")

    db_path = Path(config.DATABASE_PATH)
    if not db_path.exists():
        issues.append(f"Database does not exist at configured path: {db_path}")
    else:
        warnings.append(
            "SQLite requires one backend service using one persistent volume; schedule encrypted off-volume backups."
        )
        required = {"users", "user_roles", "auth_otps", "staff_users", "logistics_quotes"}
        try:
            with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as conn:
                present = {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
            missing = sorted(required - present)
            if missing:
                issues.append("Required database migrations are missing: " + ", ".join(missing))
        except sqlite3.Error as exc:
            issues.append(f"Database readiness check failed: {exc}")

    return {
        "ready": not issues,
        "issues": issues,
        "warnings": warnings,
        "environment": config.ENV,
        "storage_backend": config.STORAGE_BACKEND,
    }


def enforce_if_configured():
    status = check_readiness()
    if config.ENFORCE_PRODUCTION_CONFIG and not status["ready"]:
        raise RuntimeError("Production readiness check failed: " + "; ".join(status["issues"]))
