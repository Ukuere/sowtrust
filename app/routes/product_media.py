"""
Public product image delivery with upload-root safety checks.
"""
from pathlib import Path

from flask import Blueprint, abort, redirect, request, send_file

from app.services.document_storage import _upload_root, object_download_url

product_media_bp = Blueprint("product_media", __name__, url_prefix="/media/products")


@product_media_bp.route("/view")
def view_product_image():
    image_path = request.args.get("path", "").strip()
    if not image_path:
        abort(404)

    if image_path.startswith("s3://"):
        if "/product_media/" not in image_path:
            abort(403)
        signed_url = object_download_url(image_path)
        if not signed_url:
            abort(404)
        return redirect(signed_url, code=302)

    product_root = (Path(_upload_root()) / "product_media").resolve()
    requested = Path(image_path)
    requested = requested if requested.is_absolute() else requested.resolve()

    try:
        requested.relative_to(product_root)
    except ValueError:
        abort(403)

    if not requested.is_file():
        abort(404)

    return send_file(requested, as_attachment=False)
