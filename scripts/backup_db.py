"""
Create a timestamped SQLite backup.

Run from cron/Railway scheduled job:
  python scripts/backup_db.py
"""
from datetime import datetime
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import config


def main():
    source = Path(config.DATABASE_PATH)
    if not source.exists():
        raise SystemExit(f"Database not found: {source}")

    backup_dir = Path("backups")
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"{source.stem}-{stamp}{source.suffix or '.db'}"
    shutil.copy2(source, target)
    print(f"[Sowtrust] Backup created: {target}")


if __name__ == "__main__":
    main()
