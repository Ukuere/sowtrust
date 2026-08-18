"""Create a consistent SQLite backup and optionally copy it off-volume."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import config


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _object_client():
    import boto3

    return boto3.client(
        "s3",
        region_name=config.OBJECT_STORAGE_REGION or None,
        endpoint_url=config.OBJECT_STORAGE_ENDPOINT or None,
        aws_access_key_id=config.OBJECT_STORAGE_ACCESS_KEY or None,
        aws_secret_access_key=config.OBJECT_STORAGE_SECRET_KEY or None,
    )


def main():
    source = Path(config.DATABASE_PATH)
    if not source.exists():
        raise SystemExit(f"Database not found: {source}")

    backup_dir = Path(config.BACKUP_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{source.stem}-{stamp}{source.suffix or '.db'}"

    with sqlite3.connect(source) as source_conn, sqlite3.connect(target) as target_conn:
        source_conn.backup(target_conn)
        integrity = target_conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        target.unlink(missing_ok=True)
        raise SystemExit(f"Backup integrity check failed: {integrity}")

    digest = _sha256(target)
    print(f"[Sowtrust] Backup created: {target} sha256={digest}")

    if config.BACKUP_TO_OBJECT_STORAGE:
        if not config.OBJECT_STORAGE_BUCKET:
            raise SystemExit("BACKUP_TO_OBJECT_STORAGE=1 but OBJECT_STORAGE_BUCKET is empty")
        key = f"{config.OBJECT_STORAGE_PREFIX.strip('/')}/backups/{target.name}"
        _object_client().upload_file(str(target), config.OBJECT_STORAGE_BUCKET, key)
        print(f"[Sowtrust] Off-volume backup uploaded: s3://{config.OBJECT_STORAGE_BUCKET}/{key}")

    backups = sorted(
        backup_dir.glob(f"{source.stem}-*{source.suffix or '.db'}"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for expired in backups[max(1, config.BACKUP_RETENTION_COUNT):]:
        expired.unlink()


if __name__ == "__main__":
    main()
