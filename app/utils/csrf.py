"""Small session-backed CSRF protection for browser form submissions."""
import hmac
import secrets

from flask import abort, current_app, request, session


EXEMPT_PREFIXES = (
    "/ussd",
    "/webhooks/",
    "/api/internal/",
)


def csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def protect_request():
    if request.method in {"GET", "HEAD", "OPTIONS", "TRACE"}:
        return None
    if current_app.config.get("TESTING") or not current_app.config.get("CSRF_ENABLED", True):
        return None
    if request.path == "/ussd" or request.path.startswith(EXEMPT_PREFIXES[1:]):
        return None

    expected = session.get("_csrf_token", "")
    supplied = request.form.get("_csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        abort(400, description="Your form session expired. Refresh the page and try again.")
    return None
