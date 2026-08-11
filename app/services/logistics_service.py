"""
Sowtrust — Logistics Service.

Makes logistics a real financial participant rather than a tracking log:
  register_provider        -> provider signs up (KYC pending until verified)
  record_quote             -> a quote is attached to an order, buyer sees the cost
  assign_provider          -> verified provider takes the job, delivery code issued
  confirm_delivery         -> provider enters buyer's delivery code -> settlement fires
  confirm_payout_success   -> [webhook] provider's money actually landed

Two separate codes exist by design, and must not be confused:
  - RELEASE code  : buyer -> farmer.   Releases the FARMER's money.
  - DELIVERY code : buyer -> provider. Releases the PROVIDER's money.
"""
import uuid
from app.models.database import get_db, fetchone, fetchall
from app.utils.security import (
    hash_pin, generate_release_code, hash_release_code, verify_release_code
)
from app.services import payment_service, fee_service
from app.services.sms_service import send_sms


def _ref(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


# ── PROVIDER ONBOARDING ───────────────────────────────────────────────────

def register_provider(phone: str, name: str, operating_area: str,
                      vehicle_type: str, pin: str, business_name: str = None) -> dict:
    """
    Provider self-registers. They start PENDING — an agent must verify
    them before they can be assigned jobs (same trust model as farmers).
    """
    if fetchone("SELECT id FROM logistics_providers WHERE phone = ?", (phone,)):
        return {"ok": False, "error": "A provider is already registered on this number."}
    if len(pin) != 4 or not pin.isdigit():
        return {"ok": False, "error": "PIN must be exactly 4 digits."}

    with get_db() as conn:
        conn.execute(
            """INSERT INTO logistics_providers
               (name, business_name, phone, operating_area, vehicle_type, pin_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, business_name, phone, operating_area, vehicle_type, hash_pin(pin)),
        )
    send_sms(phone,
             f"Sowtrust: Welcome {name}! Your logistics account is registered and "
             f"pending verification. An agent will verify you shortly.")
    return {"ok": True}


def get_provider(phone: str):
    return fetchone(
        "SELECT * FROM logistics_providers WHERE phone = ? AND is_active = 1", (phone,)
    )


def save_provider_bank_account(phone: str, bank_code: str, account_number: str) -> dict:
    """
    Same Paystack account-resolution safety check farmers get: confirm the
    account exists and return whose name is on it, so the provider can
    confirm it's really theirs before we save it.
    """
    result = payment_service.resolve_account_number(account_number, bank_code)
    if not result["ok"]:
        return {"ok": False, "error": result["error"]}
    return {"ok": True, "account_name": result["account_name"]}


def commit_provider_bank_account(phone: str, bank_code: str,
                                  account_number: str, account_name: str) -> dict:
    with get_db() as conn:
        conn.execute(
            """UPDATE logistics_providers
               SET bank_code=?, bank_account_number=?, bank_account_name=?,
                   bank_verified_at=datetime('now')
               WHERE phone=?""",
            (bank_code, account_number, account_name, phone),
        )
    return {"ok": True}


# ── QUOTING ───────────────────────────────────────────────────────────────

def record_quote(txn_id: str, quote_amount: float, origin: str, destination: str) -> dict:
    """
    A logistics quote is attached to an order BEFORE the buyer pays, so
    the buyer sees the full cost up front (product + buyer fee +
    logistics) and pays it all in one transfer.

    Per the revenue model: the buyer pays the FULL quoted amount; the
    platform commission is deducted from the provider's settlement, not
    added on top of the buyer's cost.
    """
    escrow = fetchone("SELECT * FROM escrow_ledger WHERE txn_id = ?", (txn_id,))
    if not escrow:
        return {"ok": False, "error": "Transaction not found."}
    if escrow["status"] != "PENDING_PAYMENT":
        return {"ok": False, "error":
                f"Cannot add a quote once the order is {escrow['status']} — "
                f"the buyer has already been charged."}

    fees = fee_service.calculate_logistics_fees(quote_amount)

    with get_db() as conn:
        conn.execute(
            """INSERT INTO logistics_log
               (txn_id, origin, destination, status,
                quote_amount, platform_fee, settlement_amount)
               VALUES (?, ?, ?, 'QUOTED', ?, ?, ?)""",
            (txn_id, origin, destination,
             fees["logistics_amount"], fees["logistics_platform_fee"],
             fees["logistics_settlement_amount"]),
        )
        # Keep the order's own totals in sync — the buyer's payable amount
        # now includes logistics.
        conn.execute(
            """UPDATE escrow_ledger
               SET logistics_amount=?, logistics_platform_fee=?,
                   logistics_settlement_amount=?,
                   buyer_total = product_amount + buyer_platform_fee + ?,
                   sowtrust_total_revenue = buyer_platform_fee + seller_platform_fee + ?
               WHERE txn_id=?""",
            (fees["logistics_amount"], fees["logistics_platform_fee"],
             fees["logistics_settlement_amount"],
             fees["logistics_amount"], fees["logistics_platform_fee"], txn_id),
        )
    return {"ok": True, **fees}


def assign_provider(txn_id: str, provider_phone: str) -> dict:
    """
    A verified provider takes the job. Generates the DELIVERY code and
    sends it to the BUYER (not the provider) — the buyer hands it over
    only once goods are physically received.
    """
    provider = get_provider(provider_phone)
    if not provider:
        return {"ok": False, "error": "Provider not found."}
    if provider["kyc_status"] != "VERIFIED":
        return {"ok": False, "error": "Your account is not yet verified. An agent must verify you first."}
    if not provider["bank_account_number"] or not provider["bank_verified_at"]:
        return {"ok": False, "error": "Add your bank/wallet payout account before accepting jobs."}

    log = fetchone("SELECT * FROM logistics_log WHERE txn_id = ?", (txn_id,))
    if not log:
        return {"ok": False, "error": "No logistics job found for that transaction."}
    if log["provider_id"]:
        return {"ok": False, "error": "This job is already assigned to another provider."}

    escrow = fetchone("SELECT * FROM escrow_ledger WHERE txn_id = ?", (txn_id,))
    if not escrow or escrow["status"] != "ESCROW_LOCKED":
        return {"ok": False, "error": "Buyer payment is not confirmed yet — job not available."}

    delivery_code = generate_release_code()

    with get_db() as conn:
        conn.execute(
            """UPDATE logistics_log
               SET provider_id=?, status='ASSIGNED', delivery_code_hash=?,
                   dispatched_at=datetime('now')
               WHERE txn_id=?""",
            (provider["id"], hash_release_code(delivery_code), txn_id),
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (provider_phone, "LOGISTICS_ASSIGNED", f"TXN:{txn_id} PROVIDER:{provider['id']}"),
        )

    send_sms(escrow["buyer_phone"],
             f"Sowtrust Delivery Code: {delivery_code}\nTXN: {txn_id}\n"
             f"Give this to the driver ONLY after your goods arrive.")
    send_sms(provider_phone,
             f"Sowtrust: Job assigned! TXN {txn_id}\n"
             f"{log['origin']} to {log['destination']}\n"
             f"You'll earn NGN {log['settlement_amount']:,.0f} on delivery.")
    send_sms(escrow["farmer_phone"],
             f"Sowtrust: A driver has been assigned for your sale (TXN {txn_id}). "
             f"Prepare your goods for pickup.")
    return {"ok": True, "settlement_amount": log["settlement_amount"]}


# ── DELIVERY CONFIRMATION & SETTLEMENT ────────────────────────────────────

def confirm_delivery(provider_phone: str, txn_id: str, delivery_code: str) -> dict:
    """
    Provider enters the delivery code the buyer gave them on arrival.
    On success this fires a REAL Paystack transfer to the provider.
    Every attempt (success or failure) is logged, per spec section 10.
    """
    provider = get_provider(provider_phone)
    log = fetchone("SELECT * FROM logistics_log WHERE txn_id = ?", (txn_id,))

    def _log_attempt(success: bool, reason: str):
        with get_db() as conn:
            conn.execute(
                """INSERT INTO delivery_code_attempts
                   (logistics_id, txn_id, attempted_by, success, reason)
                   VALUES (?, ?, ?, ?, ?)""",
                (log["logistics_id"] if log else None, txn_id, provider_phone,
                 1 if success else 0, reason),
            )

    if not provider:
        return {"ok": False, "error": "Provider not found."}
    if not log:
        _log_attempt(False, "No logistics record")
        return {"ok": False, "error": "No delivery job found for that transaction."}
    if log["provider_id"] != provider["id"]:
        _log_attempt(False, "Not assigned to this provider")
        return {"ok": False, "error": "This job is not assigned to you."}
    if log["status"] == "DELIVERED":
        _log_attempt(False, "Already delivered")
        return {"ok": False, "error": "This delivery is already confirmed."}
    if log["delivery_code_used_at"]:
        _log_attempt(False, "Code already used")
        return {"ok": False, "error": "That code has already been used."}
    if not verify_release_code(delivery_code, log["delivery_code_hash"] or ""):
        _log_attempt(False, "Invalid code")
        return {"ok": False, "error": "Invalid delivery code."}

    _log_attempt(True, "Verified")

    settlement = log["settlement_amount"]
    payout_ref = _ref("LPAY")

    recipient = payment_service.create_transfer_recipient(
        provider["bank_account_name"], provider["bank_account_number"], provider["bank_code"]
    )
    if not recipient["ok"]:
        return {"ok": False, "error": f"Could not set up payout: {recipient['error']}"}

    transfer = payment_service.initiate_transfer(
        recipient["recipient_code"], settlement, payout_ref,
        f"Sowtrust logistics payout — TXN:{txn_id}"
    )
    if not transfer["ok"]:
        with get_db() as conn:
            conn.execute(
                "UPDATE logistics_log SET payout_status='failed' WHERE txn_id=?", (txn_id,)
            )
        return {"ok": False, "error": f"Transfer failed: {transfer['error']}"}

    with get_db() as conn:
        conn.execute(
            """UPDATE logistics_log
               SET status='DELIVERED', delivery_code_used_at=datetime('now'),
                   confirmed_at=datetime('now'),
                   payout_reference=?, payout_status='pending'
               WHERE txn_id=?""",
            (transfer["transfer_code"], txn_id),
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (provider_phone, "DELIVERY_CONFIRMED",
             f"TXN:{txn_id} SETTLEMENT:{settlement} REF:{transfer['transfer_code']}"),
        )

    escrow = fetchone("SELECT * FROM escrow_ledger WHERE txn_id = ?", (txn_id,))
    if escrow:
        send_sms(escrow["buyer_phone"],
                 f"Sowtrust: Delivery confirmed for TXN {txn_id}. "
                 f"Give the farmer your release code to complete the sale.")
    return {"ok": True, "settlement_amount": settlement}


def confirm_payout_success(transfer_code: str) -> dict:
    """[Called from Paystack webhook] Provider's money actually landed."""
    log = fetchone("SELECT * FROM logistics_log WHERE payout_reference = ?", (transfer_code,))
    if not log:
        return {"ok": False, "error": "Unknown logistics transfer reference"}

    with get_db() as conn:
        conn.execute(
            "UPDATE logistics_log SET payout_status='success' WHERE id=?", (log["id"],)
        )
        conn.execute(
            "UPDATE logistics_providers SET completed_jobs = completed_jobs + 1 WHERE id=?",
            (log["provider_id"],),
        )
    provider = fetchone("SELECT * FROM logistics_providers WHERE id=?", (log["provider_id"],))
    if provider:
        send_sms(provider["phone"],
                 f"Sowtrust: NGN {log['settlement_amount']:,.0f} has been paid to your "
                 f"account for TXN {log['txn_id']}.")
    return {"ok": True}


def mark_payout_failed(transfer_code: str, reason: str) -> dict:
    """[Called from Paystack webhook] Provider payout bounced."""
    log = fetchone("SELECT * FROM logistics_log WHERE payout_reference = ?", (transfer_code,))
    if not log:
        return {"ok": False, "error": "Unknown logistics transfer reference"}
    with get_db() as conn:
        conn.execute(
            "UPDATE logistics_log SET payout_status='failed' WHERE id=?", (log["id"],)
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            ("system", "LOGISTICS_PAYOUT_FAILED", f"TXN:{log['txn_id']} REASON:{reason}"),
        )
    return {"ok": True}


def get_available_jobs(limit: int = 5):
    """Quoted jobs with confirmed buyer payment, not yet assigned."""
    return fetchall(
        """SELECT l.*, e.crop, e.quantity_bags
           FROM   logistics_log l
           JOIN   escrow_ledger e ON e.txn_id = l.txn_id
           WHERE  l.provider_id IS NULL
             AND  l.status = 'QUOTED'
             AND  e.status = 'ESCROW_LOCKED'
           ORDER  BY l.created_at DESC LIMIT ?""",
        (limit,),
    )
