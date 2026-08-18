"""Shared session and role authorization for staff-only routes."""
from functools import wraps
from flask import abort, redirect, request, session, url_for


def require_admin(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("staff_user_id") and session.get("staff_role") in {
            "ADMIN", "OPERATIONS", "REVIEWER"
        }:
            return view(*args, **kwargs)
        if any(session.get(key) for key in (
            "buyer_phone", "agent_phone", "provider_phone", "farmer_phone"
        )):
            abort(403)
        return redirect(url_for("staff_auth.login", next=request.full_path.rstrip("?")))
    return wrapped
