"""
Protected admin access to uploaded verification documents.
"""
from pathlib import Path

from flask import Blueprint, abort, redirect, request, send_file

from app.routes.admin_auth import require_admin
from app.services.document_storage import _upload_root, object_download_url

admin_documents_bp = Blueprint(
    "admin_documents", __name__, url_prefix="/admin/documents"
)


@admin_documents_bp.route("/view")
@require_admin
def view_document():
    document_path = request.args.get("path", "").strip()
    if not document_path:
        abort(404)

    if document_path.startswith("s3://"):
        signed_url = object_download_url(document_path, expires_seconds=60)
        if not signed_url:
            abort(404)
        return redirect(signed_url, code=302)

    upload_root = Path(_upload_root()).resolve()
    requested = Path(document_path)
    requested = requested if requested.is_absolute() else requested.resolve()

    try:
        requested.relative_to(upload_root)
    except ValueError:
        abort(403)

    if not requested.is_file():
        abort(404)

    return send_file(requested, as_attachment=False)
