"""
AgriHub — Escrow Service.
Handles the full lifecycle: lock → verify → release / expire / dispute.
"""
from app.models.database import get_db, fetchone
from app.utils.security import generate_release_code, hash_release_code, verify_release_code
from app.services.sms_service import (
    notify_escrow_locked, notify_release_code,
    notify_payment_released, notify_logistics
)
from config.settings import config


def lock_escrow(buyer_phone: str, farmer_phone: str,
                crop: str, quantity_bags: int, amount: float) -> dict:
    """
    Lock funds in escrow.
    Returns { "ok": True, "txn_id": ..., "release_code": ... }
    or      { "ok": False, "error": ... }
    """
    fee = round(amount * config.SERVICE_FEE_PERCENT / 100, 2)
    release_code = generate_release_code()
    code_hash = hash_release_code(release_code)

    try:
        with get_db() as conn:
            # Ensure buyer row exists
            conn.execute(
                "INSERT OR IGNORE INTO buyers (phone) VALUES (?)", (buyer_phone,)
            )
            # Create escrow record
            conn.execute(
                """INSERT INTO escrow_ledger
                   (farmer_phone, buyer_phone, crop, quantity_bags, amount, service_fee, release_code_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (farmer_phone, buyer_phone, crop, quantity_bags, amount, fee, code_hash),
            )
            row = conn.execute(
                "SELECT txn_id FROM escrow_ledger WHERE release_code_hash = ?",
                (code_hash,),
            ).fetchone()
            txn_id = row["txn_id"]

            # Audit
            conn.execute(
                "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
                (buyer_phone, "ESCROW_LOCKED", f"TXN:{txn_id} AMT:{amount}"),
            )

        # Notify both parties
        notify_escrow_locked(farmer_phone, buyer_phone, crop, amount, txn_id)
        notify_release_code(buyer_phone, release_code, txn_id)

        return {"ok": True, "txn_id": txn_id, "release_code": release_code}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def release_escrow(farmer_phone: str, txn_id: str, release_code: str) -> dict:
    """
    Farmer enters release code → funds move to farmer wallet.
    """
    row = fetchone(
        "SELECT * FROM escrow_ledger WHERE txn_id = ? AND farmer_phone = ?",
        (txn_id, farmer_phone),
    )
    if not row:
        return {"ok": False, "error": "Transaction not found."}
    if row["status"] != "ESCROW_LOCKED":
        return {"ok": False, "error": f"Transaction is already {row['status']}."}
    if not verify_release_code(release_code, row["release_code_hash"]):
        return {"ok": False, "error": "Invalid release code."}

    net_payout = row["amount"] - row["service_fee"]

    try:
        with get_db() as conn:
            conn.execute(
                "UPDATE escrow_ledger SET status='DELIVERED', released_at=datetime('now') WHERE txn_id=?",
                (txn_id,),
            )
            conn.execute(
                "UPDATE farmers SET balance = balance + ?, credit_score = credit_score + 1 WHERE phone = ?",
                (net_payout, farmer_phone),
            )
            conn.execute(
                "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
                (farmer_phone, "ESCROW_RELEASED", f"TXN:{txn_id} NET:{net_payout}"),
            )

        notify_payment_released(farmer_phone, net_payout, txn_id)
        return {"ok": True, "net_payout": net_payout}

    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_active_escrow(farmer_phone: str):
    """Return the latest locked escrow for a farmer."""
    return fetchone(
        """SELECT * FROM escrow_ledger
           WHERE farmer_phone = ? AND status = 'ESCROW_LOCKED'
           ORDER BY locked_at DESC LIMIT 1""",
        (farmer_phone,),
    )


def get_farmer_history(farmer_phone: str):
    from app.models.database import fetchall
    return fetchall(
        "SELECT * FROM escrow_ledger WHERE farmer_phone = ? ORDER BY locked_at DESC LIMIT 10",
        (farmer_phone,),
    )
