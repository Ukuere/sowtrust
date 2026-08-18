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
import logging
from pathlib import PurePosixPath
import uuid
from config.settings import config


logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png"}
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5MB
MIME_BY_EXTENSION = {
    "pdf": "application/pdf",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}


def _upload_root() -> str:
    return config.UPLOAD_FOLDER


def _detected_mime(file_storage, ext: str):
    header = file_storage.stream.read(16)
    file_storage.stream.seek(0)
    if header.startswith(b"%PDF-"):
        detected = "application/pdf"
    elif header.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif header.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    else:
        return None
    return detected if detected == MIME_BY_EXTENSION.get(ext) else None


def _object_client():
    import boto3

    return boto3.client(
        "s3",
        region_name=config.OBJECT_STORAGE_REGION,
        endpoint_url=config.OBJECT_STORAGE_ENDPOINT or None,
        aws_access_key_id=config.OBJECT_STORAGE_ACCESS_KEY,
        aws_secret_access_key=config.OBJECT_STORAGE_SECRET_KEY,
    )


def _save(file_storage, subfolder: str, allowed_extensions: set[str], label: str) -> dict:
    if not file_storage or not file_storage.filename:
        return {"ok": False, "error": f"Upload a {label}."}
    ext = file_storage.filename.rsplit(".", 1)[-1].lower() if "." in file_storage.filename else ""
    if ext not in allowed_extensions:
        allowed = ", ".join(sorted(value.upper() for value in allowed_extensions))
        return {"ok": False, "error": f"{label.title()} must be one of: {allowed}."}

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_FILE_SIZE_BYTES:
        return {"ok": False, "error": f"{label.title()} must be under 5MB."}
    if size == 0:
        return {"ok": False, "error": f"That {label} appears to be empty."}
    content_type = _detected_mime(file_storage, ext)
    if not content_type:
        return {"ok": False, "error": f"The uploaded {label} content does not match its file type."}

    filename = f"{uuid.uuid4().hex}.{ext}"
    if config.STORAGE_BACKEND.lower() in {"s3", "r2", "object"}:
        if not config.OBJECT_STORAGE_BUCKET:
            return {"ok": False, "error": "Object storage is not configured."}
        key = str(PurePosixPath(config.OBJECT_STORAGE_PREFIX) / subfolder / filename)
        try:
            _object_client().upload_fileobj(
                file_storage.stream,
                config.OBJECT_STORAGE_BUCKET,
                key,
                ExtraArgs={"ContentType": content_type},
            )
        except Exception:
            logger.exception("Object storage upload failed")
            return {"ok": False, "error": "Secure file storage is temporarily unavailable."}
        return {"ok": True, "path": f"s3://{config.OBJECT_STORAGE_BUCKET}/{key}"}

    folder = os.path.join(_upload_root(), subfolder)
    os.makedirs(folder, exist_ok=True)
    full_path = os.path.join(folder, filename)
    file_storage.save(full_path)
    return {"ok": True, "path": full_path}


def object_download_url(path: str, expires_seconds: int = 120) -> str | None:
    prefix = f"s3://{config.OBJECT_STORAGE_BUCKET}/"
    if not path or not path.startswith(prefix):
        return None
    key = path[len(prefix):]
    expected_prefix = f"{config.OBJECT_STORAGE_PREFIX.strip('/')}/"
    if not key.startswith(expected_prefix):
        return None
    try:
        return _object_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": config.OBJECT_STORAGE_BUCKET, "Key": key},
            ExpiresIn=expires_seconds,
        )
    except Exception:
        logger.exception("Object storage signed URL generation failed")
        return None


def save_kyc_document(file_storage, subfolder: str = "kyc") -> dict:
    """
    file_storage: a Werkzeug FileStorage object from request.files.
    Returns {"ok": True, "path": "..."} or {"ok": False, "error": "..."}.
    """
    return _save(file_storage, subfolder, ALLOWED_EXTENSIONS, "verification document")


def save_product_image(file_storage, subfolder: str = "product_media") -> dict:
    """
    Save product listing images uploaded by agents/operations. Farmers do
    not upload images over USSD; this supports the assisted media workflow.
    """
    return _save(file_storage, subfolder, ALLOWED_IMAGE_EXTENSIONS, "product image")
