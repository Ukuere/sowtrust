"""
AgriHub — Database connection manager.
Uses a per-request connection pattern safe for Flask + Gunicorn.
"""
import sqlite3
from contextlib import contextmanager
from config.settings import config


@contextmanager
def get_db():
    """Context manager: yields an open DB connection, commits on success, rolls back on error."""
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetchone(query: str, params: tuple = ()):
    with get_db() as conn:
        return conn.execute(query, params).fetchone()


def fetchall(query: str, params: tuple = ()):
    with get_db() as conn:
        return conn.execute(query, params).fetchall()


def execute(query: str, params: tuple = ()):
    with get_db() as conn:
        conn.execute(query, params)
