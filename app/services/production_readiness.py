"""
Production readiness checks for MVP launch.
"""
from pathlib import Path

from config.settings import config


def check_readiness() -> dict:
    issues = []
    warnings = []

    if config.SECRET_KEY == "dev-secret-change-me":
        issues.append("FLASK_SECRET_KEY is still using the development default.")
    if config.DASHBOARD_PASSWORD == "changeme":
        issues.append("DASHBOARD_ADMIN_PASSWORD is still using the development default.")
    if not config.PAYSTACK_SECRET_KEY:
        issues.append("PAYSTACK_SECRET_KEY is not configured.")
    if not config.PAYSTACK_PUBLIC_KEY:
        warnings.append("PAYSTACK_PUBLIC_KEY is not configured.")
    if not config.AT_API_KEY:
        warnings.append("AT_API_KEY is not configured; SMS delivery will fail outside stubs/sandbox.")
    if config.STORAGE_BACKEND == "local":
        upload_root = Path(config.UPLOAD_FOLDER)
        warnings.append(
            f"STORAGE_BACKEND=local; ensure {upload_root} is on persistent storage or migrate to object storage."
        )
    if not config.PUBLIC_BASE_URL:
        warnings.append("PUBLIC_BASE_URL is not set; generated external links may be wrong in production.")

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
