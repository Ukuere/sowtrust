"""
Sowtrust — Product Catalog Service.

Replaces the old hardcoded CROPS dict. Farmers can list ANY agricultural
product by typing its name — the catalog grows organically. This service
normalizes names (so "maize", "Maize", "MAIZE " all collapse to one entry),
tracks every product ever listed, and surfaces the currently active ones
to buyers.
"""
import re
from app.models.database import get_db, fetchone, fetchall

# Basic sanity bounds — not a restriction on WHAT they can list,
# just protection against empty/garbage/absurdly long input on a USSD line.
_MIN_LEN = 2
_MAX_LEN = 40
_VALID_CHARS = re.compile(r"^[A-Za-z][A-Za-z\s\-']*$")


def normalize_product_name(raw: str) -> str | None:
    """
    Clean up free-text farmer input into a consistent display name.
    Returns None if the input doesn't look like a plausible product name.
    e.g. "  maize  " -> "Maize" | "sweet   potato" -> "Sweet Potato"
    """
    if not raw:
        return None
    cleaned = re.sub(r"\s+", " ", raw.strip())
    if not (_MIN_LEN <= len(cleaned) <= _MAX_LEN):
        return None
    if not _VALID_CHARS.match(cleaned):
        return None
    return cleaned.title()


def get_or_create_product(raw_name: str) -> str | None:
    """
    Normalize + ensure the product exists in the catalog.
    Returns the canonical (normalized) name, or None if input was invalid.
    Safe to call every time a farmer registers/updates a listing —
    INSERT OR IGNORE means existing products aren't duplicated.
    """
    name = normalize_product_name(raw_name)
    if not name:
        return None
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO products (name, name_lower) VALUES (?, ?)",
            (name, name.lower()),
        )
    return name


def list_active_products(limit: int = 8):
    """
    Products currently sold by at least one verified, active, priced farmer —
    ordered by how many farmers are selling it (most available first).
    This is what buyers see as the numbered browse list.
    """
    rows = fetchall(
        """SELECT crop AS name, COUNT(*) AS seller_count
           FROM   farmers
           WHERE  price > 0 AND kyc_status = 'VERIFIED' AND is_active = 1
           GROUP  BY crop
           ORDER  BY seller_count DESC, crop ASC
           LIMIT  ?""",
        (limit,),
    )
    return [dict(r) for r in rows]


def find_farmers_for_product(raw_name: str, limit: int = 5):
    """
    Buyer typed or picked a product name — find verified sellers.
    Tries an exact case-insensitive match first, then falls back to a
    partial match (so "tomato" still finds farmers listed as "Tomatoes").
    """
    name = re.sub(r"\s+", " ", (raw_name or "").strip())
    if not name:
        return [], None

    exact = fetchall(
        """SELECT name, location, price, phone, crop
           FROM   farmers
           WHERE  LOWER(crop) = LOWER(?)
             AND  price > 0 AND kyc_status = 'VERIFIED' AND is_active = 1
           ORDER  BY price ASC LIMIT ?""",
        (name, limit),
    )
    if exact:
        return [dict(r) for r in exact], exact[0]["crop"]

    partial = fetchall(
        """SELECT name, location, price, phone, crop
           FROM   farmers
           WHERE  LOWER(crop) LIKE LOWER(?)
             AND  price > 0 AND kyc_status = 'VERIFIED' AND is_active = 1
           ORDER  BY price ASC LIMIT ?""",
        (f"%{name}%", limit),
    )
    if partial:
        return [dict(r) for r in partial], partial[0]["crop"]

    return [], None
