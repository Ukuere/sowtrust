"""
Sowtrust — KYC Document Storage.

MVP STUB: saves uploaded ID/business documents to local disk. This works
fine for a single Railway instance with the persistent volume you already
set up for the database — point UPLOAD_FOLDER at a path on that same
volume so uploads survive redeploys the same way sowtrust.db does.

Before scaling past one instance (or if Railway's volume setup changes),
move this to object storage (S3, Cloudflare R2, etc.) — the function
signature here is deliberately narrow (bytes in, path out) so swapping
the implementation later doesn't require touching any caller.
"""
import os
import uuid

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB


def _upload_root() -> str:
    return os.environ.get("UPLOAD_FOLDER", "uploads")


def save_kyc_document(file_storage, subfolder: str = "kyc") -> dict:
    """
    file_storage: a Werkzeug FileStorage object from request.files.
    Returns {"ok": True, "path": "..."} or {"ok": False, "error": "..."}.
    """
    if not file_storage or not file_storage.filename:
        return {"ok": False, "error": "No file was uploaded."}

    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return {"ok": False, "error": "File must be a PDF, JPG, or PNG."}

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_FILE_SIZE_BYTES:
        return {"ok": False, "error": "File must be under 5MB."}
    if size == 0:
        return {"ok": False, "error": "That file appears to be empty."}

    folder = os.path.join(_upload_root(), subfolder)
    os.makedirs(folder, exist_ok=True)

    filename = f"{uuid.uuid4().hex}.{ext}"
    full_path = os.path.join(folder, filename)
    file_storage.save(full_path)

    return {"ok": True, "path": full_path}
