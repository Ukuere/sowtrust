"""
Sowtrust — Migration: dynamic product catalog.

Adds a `products` table and backfills it with every crop name farmers
have already entered, so nothing existing is lost when we switch from
the old hardcoded CROPS list to farmer-entered product names.

Safe to run multiple times (idempotent) and safe to run against a live
database — it only ADDS a table, nothing is dropped or altered.

Run once:  python migrations/add_products_table.py
"""
import sqlite3
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    name_lower  TEXT    NOT NULL UNIQUE,
    created_at  TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_products_name_lower ON products(name_lower);
"""


def migrate():
    db_path = config.DATABASE_PATH
    print(f"[Sowtrust] Migrating database at: {db_path}")

    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)

        # Backfill: every distinct crop already entered by a farmer
        # becomes a catalog product, so existing listings keep working.
        existing_crops = conn.execute(
            "SELECT DISTINCT crop FROM farmers WHERE crop IS NOT NULL AND crop != ''"
        ).fetchall()

        inserted = 0
        for (crop,) in existing_crops:
            cur = conn.execute(
                "INSERT OR IGNORE INTO products (name, name_lower) VALUES (?, ?)",
                (crop.strip().title(), crop.strip().lower()),
            )
            inserted += cur.rowcount

        # Optional: seed a few common Nigerian staples so the buyer
        # browse list isn't empty on day one, before farmers start listing.
        seed_products = [
            "Maize", "Rice", "Cassava", "Yam", "Soybeans",
            "Palm Oil", "Groundnut", "Tomatoes", "Pepper", "Plantain",
        ]
        for name in seed_products:
            conn.execute(
                "INSERT OR IGNORE INTO products (name, name_lower) VALUES (?, ?)",
                (name, name.lower()),
            )

    print(f"[Sowtrust] ✅ products table ready. Backfilled {inserted} existing crop(s), "
          f"seeded {len(seed_products)} starter product(s).")


if __name__ == "__main__":
    migrate()
