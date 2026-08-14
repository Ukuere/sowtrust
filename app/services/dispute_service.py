"""
Sowtrust dispute workflow.

An active dispute freezes the order in DISPUTED until operations resolves
it. This prevents silent settlement while a buyer complaint is open.
"""
import uuid

from app.models.database import fetchall, fetchone, get_db
from app.services import notification_service

DISPUTE_REASONS = [
    "Delivery issue",
    "Product quality issue",
    "Wrong quantity",
    "Payment/refund issue",
    "Logistics provider issue",
    "Other",
]

ACTIVE_ORDER_STATUSES = (
    "ESCROW_LOCKED",
    "LOGISTICS_ASSIGNED",
    "PICKED_UP",
    "IN_TRANSIT",
    "DELIVERED_PENDING_CONFIRMATION",
)


def _dispute_id() -> str:
    return f"DSP-{uuid.uuid4().hex[:10].upper()}"


def create_buyer_dispute(txn_id: str, buyer_phone: str,
                         reason: str, details: str = "") -> dict:
    if reason not in DISPUTE_REASONS:
        return {"ok": False, "error": "Select a valid dispute reason."}

    order = fetchone(
        "SELECT * FROM escrow_ledger WHERE txn_id=? AND buyer_phone=?",
        (txn_id, buyer_phone),
    )
    if not order:
        return {"ok": False, "error": "Order not found."}
    if order["status"] not in ACTIVE_ORDER_STATUSES:
        return {"ok": False, "error": f"This order cannot be disputed while it is {order['status']}."}

    existing = fetchone(
        "SELECT * FROM disputes WHERE txn_id=? AND status IN ('OPEN', 'UNDER_REVIEW')",
        (txn_id,),
    )
    if existing:
        return {"ok": False, "error": "A dispute is already open for this order."}

    dispute_id = _dispute_id()
    with get_db() as conn:
        conn.execute(
            """INSERT INTO disputes
               (dispute_id, txn_id, raised_by_type, raised_by_id, reason, details)
               VALUES (?, ?, 'buyer', ?, ?, ?)""",
            (dispute_id, txn_id, buyer_phone, reason, details.strip() or None),
        )
        conn.execute(
            "UPDATE escrow_ledger SET status='DISPUTED' WHERE txn_id=?",
            (txn_id,),
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (buyer_phone, "DISPUTE_OPENED", f"TXN:{txn_id} DISPUTE:{dispute_id}"),
        )

    notification_service.notify_sms(
        "buyer", buyer_phone, buyer_phone, "DISPUTE_OPENED",
        f"Sowtrust: Your dispute {dispute_id} for TXN {txn_id} is open. Operations will review it.",
        {"txn_id": txn_id, "dispute_id": dispute_id},
    )
    return {"ok": True, "dispute_id": dispute_id}


def get_disputes(statuses=("OPEN", "UNDER_REVIEW"), limit: int = 50) -> list[dict]:
    placeholders = ",".join("?" for _ in statuses)
    rows = fetchall(
        f"""SELECT d.*, e.crop, e.buyer_phone, e.farmer_phone,
                  e.buyer_total, e.status AS order_status
            FROM disputes d
            JOIN escrow_ledger e ON e.txn_id = d.txn_id
            WHERE d.status IN ({placeholders})
            ORDER BY d.created_at ASC
            LIMIT ?""",
        tuple(statuses) + (limit,),
    )
    return [dict(r) for r in rows]


def get_dispute_for_order(txn_id: str) -> dict | None:
    row = fetchone(
        """SELECT * FROM disputes
           WHERE txn_id=?
           ORDER BY created_at DESC LIMIT 1""",
        (txn_id,),
    )
    return dict(row) if row else None


def resolve_dispute(dispute_id: str, resolution_status: str,
                    resolution: str, resolved_by: str) -> dict:
    if resolution_status not in ("RESOLVED_BUYER", "RESOLVED_SELLER", "REFUND_REQUIRED", "CANCELLED"):
        return {"ok": False, "error": "Invalid resolution status."}
    if not resolution.strip():
        return {"ok": False, "error": "Resolution notes are required."}

    dispute = fetchone("SELECT * FROM disputes WHERE dispute_id=?", (dispute_id,))
    if not dispute:
        return {"ok": False, "error": "Dispute not found."}
    if dispute["status"] in ("RESOLVED_BUYER", "RESOLVED_SELLER", "REFUND_REQUIRED", "CANCELLED"):
        return {"ok": False, "error": "This dispute is already resolved."}

    order_status = "ESCROW_LOCKED" if resolution_status == "RESOLVED_SELLER" else "DISPUTED"
    if resolution_status == "CANCELLED":
        order_status = "CANCELLED"

    with get_db() as conn:
        conn.execute(
            """UPDATE disputes
               SET status=?, resolution=?, resolved_by=?, resolved_at=datetime('now')
               WHERE dispute_id=?""",
            (resolution_status, resolution.strip(), resolved_by, dispute_id),
        )
        conn.execute(
            "UPDATE escrow_ledger SET status=? WHERE txn_id=?",
            (order_status, dispute["txn_id"]),
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (resolved_by, "DISPUTE_RESOLVED",
             f"DISPUTE:{dispute_id} STATUS:{resolution_status}"),
        )
    notification_service.notify_sms(
        "buyer",
        dispute["raised_by_id"],
        dispute["raised_by_id"],
        "DISPUTE_RESOLVED",
        f"Sowtrust: Dispute {dispute_id} has been updated to {resolution_status}. Check your order for details.",
        {"txn_id": dispute["txn_id"], "dispute_id": dispute_id},
    )
    return {"ok": True}
