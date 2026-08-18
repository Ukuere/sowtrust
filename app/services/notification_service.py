"""
Central notification service for Sowtrust MVP.

All product, payment, logistics, KYC, and dispute events should pass
through this module over time. It records each notification before
attempting delivery, so operations has an audit trail even when an SMS
provider fails.
"""
import json

from app.models.database import get_db, fetchall, fetchone
from app.services.sms_service import send_sms


def record_notification(recipient_type: str, recipient_id: str, phone: str,
                        email: str, event_type: str, channel: str,
                        message: str, subject: str = "",
                        metadata: dict | None = None) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO notifications
               (recipient_type, recipient_id, phone, email, event_type,
                channel, subject, message, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (recipient_type, recipient_id, phone, email, event_type,
             channel, subject or None, message,
             json.dumps(metadata or {}, sort_keys=True)),
        )
        return cur.lastrowid


def mark_notification(notification_id: int, status: str, error: str = ""):
    with get_db() as conn:
        conn.execute(
            """UPDATE notifications
               SET status=?, error=?, sent_at=CASE WHEN ?='SENT' THEN datetime('now') ELSE sent_at END
               WHERE id=?""",
            (status, error or None, status, notification_id),
        )


def notify_sms(recipient_type: str, recipient_id: str, phone: str,
               event_type: str, message: str, metadata: dict | None = None) -> bool:
    notification_id = record_notification(
        recipient_type=recipient_type,
        recipient_id=recipient_id,
        phone=phone,
        email="",
        event_type=event_type,
        channel="SMS",
        message=message,
        metadata=metadata,
    )
    sent = send_sms(phone, message)
    mark_notification(notification_id, "SENT" if sent else "FAILED",
                      "" if sent else "SMS provider failed")
    return sent


def notify_new_product_listing(listing: dict) -> dict:
    """
    Notify buyers who previously requested this product. This is the MVP
    version of buyer interest matching; a later recommendation engine can
    replace this without changing the publication workflow.
    """
    crop = listing.get("crop", "")
    if not crop:
        return {"notified": 0}

    requests = fetchall(
        """SELECT br.*, b.name, b.email
           FROM buyer_requests br
           LEFT JOIN buyers b ON b.phone = br.buyer_phone
           WHERE br.status='OPEN' AND LOWER(br.crop)=LOWER(?)
           ORDER BY br.created_at ASC
           LIMIT 100""",
        (crop,),
    )
    notified = 0
    for req in requests:
        message = (
            f"SowTrust: {crop} is now available from a published farmer listing in "
            f"{listing.get('location') or 'your market'}. Log in to request a quote."
        )
        ok = notify_sms(
            "buyer",
            req["buyer_phone"],
            req["buyer_phone"],
            "NEW_PRODUCT_AVAILABLE",
            message,
            {"farmer_phone": listing.get("phone"), "crop": crop},
        )
        if ok:
            notified += 1

    with get_db() as conn:
        conn.execute(
            """UPDATE buyer_requests
               SET status='MATCHED'
               WHERE status='OPEN' AND LOWER(crop)=LOWER(?)""",
            (crop,),
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            ("notification_service", "NEW_PRODUCT_NOTIFICATIONS",
             f"PRODUCT:{crop} COUNT:{notified} FARMER:{listing.get('phone')}"),
        )
    return {"notified": notified}


def get_recent_notifications(limit: int = 50) -> list[dict]:
    rows = fetchall(
        "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    return [dict(r) for r in rows]


def get_notification(notification_id: int) -> dict | None:
    row = fetchone("SELECT * FROM notifications WHERE id=?", (notification_id,))
    return dict(row) if row else None
