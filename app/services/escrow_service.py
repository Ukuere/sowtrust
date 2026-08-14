"""
Sowtrust — Escrow Service.

Real money lifecycle (not simulated):
  initiate_escrow_payment  -> buyer gets a one-time account to transfer into
  confirm_payment_received -> [webhook] money actually landed -> ESCROW_LOCKED
  release_escrow           -> farmer enters code -> real Paystack transfer fires
  confirm_payout_success   -> [webhook] money actually landed in farmer's account
  mark_payout_failed       -> [webhook] transfer bounced -> needs retry/support
"""
import uuid
from app.models.database import get_db, fetchone
from app.utils.security import generate_release_code, hash_release_code, verify_release_code
from app.services.sms_service import (
    notify_escrow_locked, notify_release_code,
    notify_payment_released, notify_logistics
)
from app.services import payment_service
from app.services import fee_service
from config.settings import config


def _ref(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def create_order_awaiting_quote(buyer_phone: str, farmer_phone: str,
                                 crop: str, quantity_bags: int,
                                 product_amount: float,
                                 buyer_name: str = "",
                                 delivery_address: str = "",
                                 delivery_city: str = "",
                                 delivery_state: str = "") -> dict:
    """
    Create the order shell before any Paystack call is made.

    The MVP checkout flow is quote-before-payment: buyer confirms product,
    quantity, and delivery address; operations locks a logistics quote;
    the buyer accepts it; only then do we initiate payment.
    """
    if quantity_bags <= 0:
        return {"ok": False, "error": "Quantity must be greater than zero."}
    if product_amount <= 0:
        return {"ok": False, "error": "Product amount must be greater than zero."}

    fees = fee_service.calculate_full_order(product_amount, 0.0)

    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO buyers (phone) VALUES (?)", (buyer_phone,)
            )
            conn.execute(
                """INSERT INTO escrow_ledger
                   (farmer_phone, buyer_phone, crop, quantity_bags,
                    amount, service_fee,
                    product_amount, buyer_platform_fee, seller_platform_fee,
                    logistics_amount, logistics_platform_fee,
                    buyer_total, farmer_settlement_amount,
                    logistics_settlement_amount, sowtrust_total_revenue,
                    release_code_hash, status,
                    buyer_name, delivery_address, delivery_city, delivery_state)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, 0, ?, '',
                           'QUOTE_PENDING', ?, ?, ?, ?)""",
                (farmer_phone, buyer_phone, crop, quantity_bags,
                 fees["product_amount"], fees["seller_platform_fee"],
                 fees["product_amount"], fees["buyer_platform_fee"],
                 fees["seller_platform_fee"], fees["buyer_total"],
                 fees["farmer_settlement_amount"], fees["sowtrust_total_revenue"],
                 buyer_name or None, delivery_address or None,
                 delivery_city or None, delivery_state or None),
            )
            row = conn.execute(
                """SELECT txn_id FROM escrow_ledger
                   WHERE buyer_phone = ? AND farmer_phone = ?
                   ORDER BY id DESC LIMIT 1""",
                (buyer_phone, farmer_phone),
            ).fetchone()
            txn_id = row["txn_id"]
            conn.execute(
                "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
                (buyer_phone, "LOGISTICS_QUOTE_REQUESTED",
                 f"TXN:{txn_id} PRODUCT:{crop} QTY:{quantity_bags}"),
            )
        return {"ok": True, "txn_id": txn_id, **fees}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def initiate_payment_for_order(txn_id: str, buyer_phone: str) -> dict:
    """
    Initiate Paystack only after a buyer-accepted, locked logistics quote
    exists. This updates the existing order so quote and payment share a
    single audit trail.
    """
    row = fetchone(
        """SELECT * FROM escrow_ledger
           WHERE txn_id = ? AND buyer_phone = ?""",
        (txn_id, buyer_phone),
    )
    if not row:
        return {"ok": False, "error": "Order not found."}
    if row["status"] == "PENDING_PAYMENT" and row["payment_reference"]:
        return {
            "ok": True,
            "txn_id": row["txn_id"],
            "buyer_total": row["buyer_total"],
            "account_number": row["virtual_account_number"],
            "bank_name": row["virtual_account_bank"],
            "already_initialized": True,
        }
    if row["status"] != "BUYER_ACCEPTED_QUOTE":
        return {"ok": False, "error": "Accept the logistics quote before payment."}

    quote = fetchone(
        """SELECT * FROM logistics_quotes
           WHERE order_id = ? AND status = 'LOCKED'""",
        (txn_id,),
    )
    if not quote or not quote["buyer_accepted_at"]:
        return {"ok": False, "error": "A locked, buyer-accepted logistics quote is required before payment."}

    reference = _ref("PAY")
    pseudo_email = f"{buyer_phone.lstrip('+')}@sowtrust.com"
    charge = payment_service.initiate_bank_transfer_charge(
        pseudo_email, row["buyer_total"], reference
    )
    if not charge["ok"]:
        return {"ok": False, "error": charge["error"]}

    with get_db() as conn:
        conn.execute(
            """UPDATE escrow_ledger
               SET status='PENDING_PAYMENT',
                   payment_reference=?,
                   virtual_account_number=?,
                   virtual_account_bank=?
               WHERE txn_id=? AND status='BUYER_ACCEPTED_QUOTE'""",
            (reference, charge["account_number"], charge["bank_name"], txn_id),
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (buyer_phone, "PAYMENT_INITIATED",
             f"TXN:{txn_id} REF:{reference} BUYER_TOTAL:{row['buyer_total']}"),
        )

    return {
        "ok": True,
        "txn_id": txn_id,
        "buyer_total": row["buyer_total"],
        "account_number": charge["account_number"],
        "bank_name": charge["bank_name"],
    }


# ── PHASE 1: BUYER PAYMENT COLLECTION ────────────────────────────────────

def initiate_escrow_payment(buyer_phone: str, farmer_phone: str,
                             crop: str, quantity_bags: int, product_amount: float,
                             logistics_amount: float = 0.0) -> dict:
    """
    Buyer confirmed an order. We do NOT lock escrow yet — we generate a
    real one-time account for them to pay into first. Escrow only
    becomes real once Paystack confirms the money actually arrived
    (see confirm_payment_received, called from the webhook).

    Three-sided fee model: the buyer is charged product_amount +
    buyer_platform_fee (+ logistics_amount, once wired in) — NOT just
    the raw product_amount. See fee_service.calculate_full_order().
    """
    if logistics_amount:
        return {
            "ok": False,
            "error": "Logistics amount must come from a locked buyer-accepted quote before payment.",
        }

    fees = fee_service.calculate_full_order(product_amount, logistics_amount)
    buyer_total = fees["buyer_total"]

    reference = _ref("PAY")
    # USSD has no email field — Paystack just needs a unique identifier here.
    # Paystack validates email format strictly — ".ussd" isn't a real TLD
    # and gets rejected. Use a real, valid-format domain instead (this
    # email is never actually sent anything for the bank_transfer channel,
    # it's just an identifier Paystack's API requires).
    pseudo_email = f"{buyer_phone.lstrip('+')}@sowtrust.com"

    charge = payment_service.initiate_bank_transfer_charge(pseudo_email, buyer_total, reference)
    if not charge["ok"]:
        return {"ok": False, "error": charge["error"]}

    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO buyers (phone) VALUES (?)", (buyer_phone,)
            )
            conn.execute(
                """INSERT INTO escrow_ledger
                   (farmer_phone, buyer_phone, crop, quantity_bags,
                    amount, service_fee,
                    product_amount, buyer_platform_fee, seller_platform_fee,
                    logistics_amount, logistics_platform_fee,
                    buyer_total, farmer_settlement_amount,
                    logistics_settlement_amount, sowtrust_total_revenue,
                    release_code_hash, status, payment_reference,
                    virtual_account_number, virtual_account_bank)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING_PAYMENT', ?, ?, ?)""",
                (farmer_phone, buyer_phone, crop, quantity_bags,
                 # legacy columns, kept in sync for backward compatibility
                 fees["product_amount"], fees["seller_platform_fee"],
                 # new split fields
                 fees["product_amount"], fees["buyer_platform_fee"], fees["seller_platform_fee"],
                 fees["logistics_amount"], fees["logistics_platform_fee"],
                 fees["buyer_total"], fees["farmer_settlement_amount"],
                 fees["logistics_settlement_amount"], fees["sowtrust_total_revenue"],
                 "",  # release code generated only once payment is confirmed
                 reference, charge["account_number"], charge["bank_name"]),
            )
            row = conn.execute(
                "SELECT txn_id FROM escrow_ledger WHERE payment_reference = ?", (reference,)
            ).fetchone()
            txn_id = row["txn_id"]
            conn.execute(
                "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
                (buyer_phone, "PAYMENT_INITIATED",
                 f"TXN:{txn_id} REF:{reference} BUYER_TOTAL:{buyer_total} "
                 f"PRODUCT:{fees['product_amount']} BUYER_FEE:{fees['buyer_platform_fee']} "
                 f"SELLER_FEE:{fees['seller_platform_fee']}"),
            )

        return {
            "ok": True, "txn_id": txn_id, "buyer_total": buyer_total,
            "account_number": charge["account_number"],
            "bank_name": charge["bank_name"],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def confirm_payment_received(payment_reference: str, amount_paid: float) -> dict:
    """
    [Called from Paystack webhook ONLY — this is the real money-received event.]
    Transitions PENDING_PAYMENT -> ESCROW_LOCKED and generates the release
    code only now, since only now is there actually money to release.
    """
    row = fetchone(
        "SELECT * FROM escrow_ledger WHERE payment_reference = ?", (payment_reference,)
    )
    if not row:
        return {"ok": False, "error": "Unknown payment reference"}
    if row["status"] != "PENDING_PAYMENT":
        return {"ok": True, "note": f"Already {row['status']}, ignoring duplicate webhook"}

    if amount_paid < row["buyer_total"] - 1:  # tolerate rounding, not underpayment
        # Underpaid — do not lock escrow. Flag for manual review rather than
        # silently accepting a short payment.
        with get_db() as conn:
            conn.execute(
                "UPDATE escrow_ledger SET status='DISPUTED' WHERE txn_id=?", (row["txn_id"],)
            )
        return {"ok": False, "error": f"Underpaid: expected {row['buyer_total']}, got {amount_paid}"}

    release_code = generate_release_code()
    code_hash = hash_release_code(release_code)

    with get_db() as conn:
        conn.execute(
            """UPDATE escrow_ledger
               SET status='ESCROW_LOCKED', release_code_hash=?, payment_confirmed_at=datetime('now')
               WHERE txn_id=?""",
            (code_hash, row["txn_id"]),
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (row["buyer_phone"], "ESCROW_LOCKED", f"TXN:{row['txn_id']} AMT:{amount_paid}"),
        )

    notify_escrow_locked(row["farmer_phone"], row["buyer_phone"], row["crop"], amount_paid, row["txn_id"])
    notify_release_code(row["buyer_phone"], release_code, row["txn_id"])
    return {"ok": True, "txn_id": row["txn_id"]}


# ── PHASE 2: FARMER SETTLEMENT ───────────────────────────────────────────

def release_escrow(farmer_phone: str, txn_id: str, release_code: str) -> dict:
    """
    Farmer enters release code (given to them by the buyer on delivery).
    This now fires a REAL Paystack transfer — requires the farmer to have
    a verified payout account first (see payment_service.resolve_account_number,
    wired into the "Add Bank Account" USSD step).
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

    farmer = fetchone("SELECT * FROM farmers WHERE phone = ?", (farmer_phone,))
    if not farmer["bank_account_number"] or not farmer["bank_verified_at"]:
        return {
            "ok": False,
            "error": "No verified payout account on file. Dial *709# > 4 > 3 to add your bank/wallet account first."
        }

    net_payout = row["farmer_settlement_amount"]
    payout_ref = _ref("PAYOUT")

    recipient = payment_service.create_transfer_recipient(
        farmer["bank_account_name"], farmer["bank_account_number"], farmer["bank_code"]
    )
    if not recipient["ok"]:
        return {"ok": False, "error": f"Could not set up payout: {recipient['error']}"}

    transfer = payment_service.initiate_transfer(
        recipient["recipient_code"], net_payout, payout_ref,
        f"Sowtrust escrow payout — {row['crop']} — TXN:{txn_id}"
    )
    if not transfer["ok"]:
        with get_db() as conn:
            conn.execute(
                "UPDATE escrow_ledger SET payout_status='failed' WHERE txn_id=?", (txn_id,)
            )
        return {"ok": False, "error": f"Transfer failed: {transfer['error']}"}

    # IMPORTANT: Paystack's transfer webhooks report back THEIR transfer_code,
    # not the `reference` we supplied — store transfer_code as the lookup key
    # or confirm_payout_success/mark_payout_failed will never match the webhook.
    with get_db() as conn:
        conn.execute(
            """UPDATE escrow_ledger
               SET status='DELIVERED', payout_reference=?, payout_status='pending',
                   released_at=datetime('now')
               WHERE txn_id=?""",
            (transfer["transfer_code"], txn_id),
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (farmer_phone, "PAYOUT_INITIATED", f"TXN:{txn_id} REF:{payout_ref} NET:{net_payout}"),
        )

    return {"ok": True, "net_payout": net_payout, "status": "processing"}


def confirm_payout_success(transfer_code: str) -> dict:
    """[Called from Paystack webhook] Transfer actually landed."""
    row = fetchone("SELECT * FROM escrow_ledger WHERE payout_reference = ?", (transfer_code,))
    if not row:
        return {"ok": False, "error": "Unknown transfer reference"}

    net_payout = row["farmer_settlement_amount"]
    with get_db() as conn:
        conn.execute(
            "UPDATE escrow_ledger SET payout_status='success' WHERE txn_id=?", (row["txn_id"],)
        )
        conn.execute(
            "UPDATE farmers SET credit_score = credit_score + 1 WHERE phone = ?",
            (row["farmer_phone"],),
        )
    notify_payment_released(row["farmer_phone"], net_payout, row["txn_id"])
    return {"ok": True}


def mark_payout_failed(transfer_code: str, reason: str) -> dict:
    """[Called from Paystack webhook] Transfer bounced — money never left Paystack, safe to retry."""
    row = fetchone("SELECT * FROM escrow_ledger WHERE payout_reference = ?", (transfer_code,))
    if not row:
        return {"ok": False, "error": "Unknown transfer reference"}
    with get_db() as conn:
        conn.execute(
            "UPDATE escrow_ledger SET status='PAYOUT_FAILED', payout_status='failed' WHERE txn_id=?",
            (row["txn_id"],),
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            ("system", "PAYOUT_FAILED", f"TXN:{row['txn_id']} REASON:{reason}"),
        )
    from app.services.sms_service import send_sms
    send_sms(row["farmer_phone"],
              f"Sowtrust: Your payout for TXN {row['txn_id']} failed and will be retried. "
              f"If this persists, dial *709# > 6 to reach an agent.")
    return {"ok": True}


def get_active_escrow(farmer_phone: str):
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


# ── EXPIRY / CLEANUP JOB ──────────────────────────────────────────────────
# Meant to be run periodically (e.g. every 15 min) by an external
# scheduler — see scripts/run_expiry_job.py. Deliberately NOT run
# in-process on the web server, since gunicorn runs multiple worker
# processes and each would spawn its own duplicate scheduler.

def expire_stale_escrows() -> dict:
    """
    Two categories of stale transaction, handled differently:

    1. PENDING_PAYMENT past its window (buyer never completed the bank
       transfer) — nothing to refund, since no money ever arrived.
       Just cancel it.
    2. ESCROW_LOCKED past its 72-hour window (farmer never delivered /
       release code never used) — buyer's real money IS sitting in
       escrow, so it must be refunded, not just abandoned.
    """
    from app.models.database import fetchall
    results = {"cancelled": 0, "refunded": 0, "refund_failed": 0}

    # 1. Unpaid, abandoned payment attempts
    stale_pending = fetchall(
        """SELECT txn_id, buyer_phone FROM escrow_ledger
           WHERE status = 'PENDING_PAYMENT'
             AND locked_at < datetime('now', ?)""",
        (f"-{config.PAYMENT_PENDING_TIMEOUT_MINUTES} minutes",),
    )
    for row in stale_pending:
        with get_db() as conn:
            conn.execute(
                "UPDATE escrow_ledger SET status='CANCELLED' WHERE txn_id=?",
                (row["txn_id"],)
            )
        results["cancelled"] += 1

    # 2. Paid, locked, but never delivered/released in time — refund the buyer
    stale_locked = fetchall(
        """SELECT * FROM escrow_ledger
           WHERE status = 'ESCROW_LOCKED' AND expires_at < datetime('now')"""
    )
    for row in stale_locked:
        # Refund the FULL amount the buyer actually paid (buyer_total —
        # product + their platform fee + logistics), not just the legacy
        # `amount` field which only ever tracked the product portion.
        refund_amount = row["buyer_total"] if row["buyer_total"] else row["amount"]
        refund = payment_service.initiate_refund(row["payment_reference"], refund_amount)
        if refund["ok"]:
            with get_db() as conn:
                conn.execute(
                    "UPDATE escrow_ledger SET status='EXPIRED' WHERE txn_id=?",
                    (row["txn_id"],)
                )
                conn.execute(
                    "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
                    ("system", "ESCROW_EXPIRED_REFUNDED",
                     f"TXN:{row['txn_id']} AMT:{refund_amount}"),
                )
            from app.services.sms_service import send_sms
            send_sms(row["buyer_phone"],
                     f"Sowtrust: Your order (TXN {row['txn_id']}) expired after 72 hours "
                     f"without delivery confirmation. NGN {refund_amount:,.0f} has been refunded.")
            send_sms(row["farmer_phone"],
                     f"Sowtrust: Your reserved sale (TXN {row['txn_id']}) expired after 72 "
                     f"hours — the release code was never used, so the order was cancelled "
                     f"and the buyer refunded. Dial *709# to relist.")
            results["refunded"] += 1
        else:
            # Don't silently drop this — flag it, an agent/admin needs to
            # look at it manually rather than money staying in limbo unresolved.
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
                    ("system", "REFUND_FAILED",
                     f"TXN:{row['txn_id']} ERROR:{refund['error']}"),
                )
            results["refund_failed"] += 1

    return results
