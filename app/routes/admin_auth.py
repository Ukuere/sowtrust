"""
Sowtrust — Shared Admin Auth.

Factored out of admin_kyc.py so admin_logistics.py (and any future admin
route) doesn't duplicate the same decorator. Still HTTP Basic Auth against
DASHBOARD_PASSWORD — see admin_kyc.py's docstring for why that's the
right call for a single-operator MVP.
"""
from functools import wraps
from flask import request, Response

from config.settings import config


def _unauthorized():
    return Response(
        "Authentication required.", 401,
        {"WWW-Authenticate": 'Basic realm="Sowtrust Admin"'},
    )


def require_admin(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        auth = request.authorization
        username_ok = (
            not config.DASHBOARD_USERNAME
            or auth and auth.username == config.DASHBOARD_USERNAME
        )
        if not auth or not username_ok or auth.password != config.DASHBOARD_PASSWORD:
            return _unauthorized()
        return view(*args, **kwargs)
    return wrapped
