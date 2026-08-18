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
from app.utils.phone import normalize_phone

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
    Products currently sold by at least one active, published farmer —
    ordered by how many farmers are selling it (most available first).
    This is what buyers see as the numbered browse list.
    """
    rows = fetchall(
        """SELECT crop AS name,
                  COUNT(*) AS seller_count,
                  MIN(price) AS min_price,
                  MAX(product_image_path) AS product_image_path,
                  SUM(CASE WHEN verification_status='VERIFIED' OR kyc_status='VERIFIED'
                           THEN 1 ELSE 0 END) AS verified_seller_count
           FROM   farmers
           WHERE  price > 0
             AND  is_active = 1
             AND  COALESCE(listing_status, 'PUBLISHED') = 'PUBLISHED'
           GROUP  BY crop
           ORDER  BY seller_count DESC, crop ASC
           LIMIT  ?""",
        (limit,),
    )
    return [dict(r) for r in rows]


def find_farmers_for_product(raw_name: str, limit: int = 5):
    """
    Buyer typed or picked a product name — find published sellers.
    Tries an exact case-insensitive match first, then falls back to a
    partial match (so "tomato" still finds farmers listed as "Tomatoes").
    """
    name = re.sub(r"\s+", " ", (raw_name or "").strip())
    if not name:
        return [], None

    exact = fetchall(
        """SELECT name, location, price, phone, crop, product_description,
                  quantity_available, product_image_path, listing_status,
                  verification_status, kyc_status
           FROM   farmers
           WHERE  LOWER(crop) = LOWER(?)
             AND  price > 0 AND is_active = 1
             AND  COALESCE(listing_status, 'PUBLISHED') = 'PUBLISHED'
           ORDER  BY price ASC LIMIT ?""",
        (name, limit),
    )
    if exact:
        return [dict(r) for r in exact], exact[0]["crop"]

    partial = fetchall(
        """SELECT name, location, price, phone, crop, product_description,
                  quantity_available, product_image_path, listing_status,
                  verification_status, kyc_status
           FROM   farmers
           WHERE  LOWER(crop) LIKE LOWER(?)
             AND  price > 0 AND is_active = 1
             AND  COALESCE(listing_status, 'PUBLISHED') = 'PUBLISHED'
           ORDER  BY price ASC LIMIT ?""",
        (f"%{name}%", limit),
    )
    if partial:
        return [dict(r) for r in partial], partial[0]["crop"]

    return [], None


def submit_agent_product_listing(agent_phone: str, farmer_phone: str, crop: str,
                                 price: float, location: str, description: str,
                                 quantity_available: int, image_path: str) -> dict:
    """
    Agent-assisted product listing workflow. Farmers can still use USSD
    without uploading images; agents/operations add media and submit the
    listing for admin publication review.
    """
    agent_normalized = normalize_phone(agent_phone)
    farmer_normalized = normalize_phone(farmer_phone)
    agent = fetchone(
        """SELECT * FROM agents
           WHERE (normalized_phone=? OR phone=?) AND is_active=1""",
        (agent_normalized, agent_normalized),
    )
    if not agent:
        return {"ok": False, "error": "Agent account not found."}

    farmer = fetchone(
        """SELECT * FROM farmers
           WHERE (normalized_phone=? OR phone=?) AND is_active=1""",
        (farmer_normalized, farmer_normalized),
    )
    if not farmer:
        return {"ok": False, "error": "Farmer account not found."}
    product_name = get_or_create_product(crop)
    if not product_name:
        return {"ok": False, "error": "Enter a valid product name."}
    if price <= 0:
        return {"ok": False, "error": "Price must be greater than zero."}
    if quantity_available <= 0:
        return {"ok": False, "error": "Quantity available must be greater than zero."}
    with get_db() as conn:
        conn.execute(
            """UPDATE farmers
               SET crop=?, price=?, location=?,
                   product_description=?, quantity_available=?,
                   product_image_path=COALESCE(NULLIF(?, ''), product_image_path),
                   listing_status='PUBLISHED',
                   verification_status=CASE
                     WHEN verification_status='VERIFIED' THEN 'VERIFIED'
                     ELSE 'PENDING' END,
                   listed_by_agent_phone=?, listing_submitted_at=datetime('now'),
                   listing_published_at=COALESCE(listing_published_at, datetime('now')),
                   image_uploaded_by=CASE WHEN ?!='' THEN ? ELSE image_uploaded_by END,
                   image_uploaded_at=CASE WHEN ?!='' THEN datetime('now') ELSE image_uploaded_at END,
                   listing_rejection_reason=NULL,
                   listing_updated_at=datetime('now')
               WHERE id=?""",
            (product_name, price, location.strip().title(),
             description.strip() or None, quantity_available, image_path,
             agent_normalized, image_path, agent_normalized, image_path, farmer["id"]),
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (agent_normalized, "PRODUCT_LISTING_PUBLISHED",
             f"FARMER:{farmer_normalized} PRODUCT:{product_name} PRICE:{price}"),
        )
        conn.execute(
            """INSERT INTO listing_moderation_log
               (farmer_phone, previous_status, new_status, reason, actor)
               VALUES (?, ?, 'PUBLISHED', 'Agent-assisted listing submission', ?)""",
            (farmer["phone"], farmer["listing_status"], agent_normalized),
        )
    published = dict(farmer)
    published.update({"crop": product_name, "location": location.strip().title(),
                      "phone": farmer["phone"], "verification_status": "PENDING"})
    from app.services import notification_service
    notification_service.notify_new_product_listing(published)
    return {"ok": True}


def get_pending_product_listings(limit: int = 50) -> list[dict]:
    rows = fetchall(
        """SELECT f.*
           FROM farmers f
           WHERE f.is_active = 1 AND f.price > 0
             AND (f.verification_status IN ('UNVERIFIED', 'PENDING', 'REJECTED')
                  OR f.listing_status IN ('SUSPENDED', 'REJECTED'))
           ORDER BY COALESCE(f.listing_submitted_at, f.created_at) ASC
           LIMIT ?""",
        (limit,),
    )
    return [dict(r) for r in rows]


def update_listing_image(farmer_phone: str, image_path: str, uploaded_by: str) -> dict:
    normalized = normalize_phone(farmer_phone)
    if not normalized or not image_path:
        return {"ok": False, "error": "A valid farmer and product image are required."}
    listing = fetchone(
        "SELECT * FROM farmers WHERE normalized_phone=? OR phone=?",
        (normalized, normalized),
    )
    if not listing:
        return {"ok": False, "error": "Listing not found."}
    with get_db() as conn:
        conn.execute(
            """UPDATE farmers SET product_image_path=?, image_uploaded_by=?,
                   image_uploaded_at=datetime('now'), listing_updated_at=datetime('now')
               WHERE id=?""",
            (image_path, uploaded_by, listing["id"]),
        )
        conn.execute(
            "INSERT INTO audit_log(actor, action, details) VALUES (?, 'PRODUCT_IMAGE_UPDATED', ?)",
            (uploaded_by, f"FARMER:{listing['phone']} PRODUCT:{listing['crop']}"),
        )
    return {"ok": True}


def review_product_listing(farmer_phone: str, decision: str, reviewed_by: str,
                           rejection_reason: str = "") -> dict:
    if decision not in ("VERIFIED", "PUBLISHED", "SUSPENDED", "REJECTED"):
        return {"ok": False, "error": "Invalid product listing decision."}
    if decision in ("REJECTED", "SUSPENDED") and not rejection_reason.strip():
        return {"ok": False, "error": "A moderation reason is required."}

    normalized = normalize_phone(farmer_phone)
    listing = fetchone(
        """SELECT * FROM farmers
           WHERE (normalized_phone=? OR phone=?) AND is_active=1""",
        (normalized, normalized),
    )
    if not listing:
        return {"ok": False, "error": "Listing not found."}
    previous_status = listing["listing_status"]
    new_listing_status = "SUSPENDED" if decision in ("SUSPENDED", "REJECTED") else "PUBLISHED"
    new_verification = (
        "VERIFIED" if decision == "VERIFIED"
        else "REJECTED" if decision == "REJECTED"
        else listing["verification_status"]
    )

    with get_db() as conn:
        conn.execute(
            """UPDATE farmers
               SET listing_status=?, verification_status=?,
                   listing_published_at=CASE WHEN ?='PUBLISHED' THEN COALESCE(listing_published_at, datetime('now')) ELSE listing_published_at END,
                   listing_reviewed_by=?,
                   listing_rejection_reason=?,
                   listing_updated_at=datetime('now')
               WHERE id=?""",
            (new_listing_status, new_verification, new_listing_status, reviewed_by,
             rejection_reason.strip() or None, listing["id"]),
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (reviewed_by, f"PRODUCT_LISTING_{decision}",
             f"FARMER:{listing['phone']} PRODUCT:{listing['crop']}"),
        )
        conn.execute(
            """INSERT INTO listing_moderation_log
               (farmer_phone, previous_status, new_status, reason, actor)
               VALUES (?, ?, ?, ?, ?)""",
            (listing["phone"], previous_status, new_listing_status,
             rejection_reason.strip() or decision, reviewed_by),
        )

    return {"ok": True}


def create_buyer_product_interest(buyer_phone: str, crop: str,
                                  quantity: int = 1, location: str = "") -> dict:
    product_name = get_or_create_product(crop)
    if not product_name:
        return {"ok": False, "error": "Enter a valid product name."}
    quantity = max(1, int(quantity or 1))
    with get_db() as conn:
        conn.execute(
            """INSERT INTO buyer_requests
               (buyer_phone, crop, qty_bags, location, status)
               VALUES (?, ?, ?, ?, 'OPEN')""",
            (buyer_phone, product_name, quantity, location.strip() or None),
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (buyer_phone, "BUYER_PRODUCT_INTEREST",
             f"PRODUCT:{product_name} QTY:{quantity}"),
        )
    return {"ok": True}
