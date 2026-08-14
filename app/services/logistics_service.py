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


def _get_provider_by_phone_or_id(provider_ref):
    if not provider_ref:
        return None
    provider = fetchone(
        "SELECT * FROM logistics_providers WHERE phone = ? AND is_active = 1",
        (str(provider_ref),),
    )
    if provider:
        return provider
    if str(provider_ref).isdigit():
        return fetchone(
            "SELECT * FROM logistics_providers WHERE id = ? AND is_active = 1",
            (int(provider_ref),),
        )
    return None


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


def get_verified_providers() -> list:
    rows = fetchall(
        """SELECT * FROM logistics_providers
           WHERE is_active = 1 AND kyc_status = 'VERIFIED'
           ORDER BY name ASC"""
    )
    return [dict(r) for r in rows]


# ── KYC DOCUMENT SUBMISSION (spec sections 5, 8) ─────────────────────────
# assign_provider() below already enforces kyc_status == 'VERIFIED' — has
# since the step 2 build. This section is what actually lets a provider
# reach that status: submit documents, get reviewed, done. Mirrors
# buyer_service.submit_kyc()/admin_review_kyc() and reuses the same
# kyc_verifications audit table (user_type='logistics_provider').

ID_TYPES = ["National ID (NIN)", "International Passport", "Driver's Licence", "Voter's Card"]


def submit_provider_kyc(phone: str, id_type: str, id_number: str, id_document_path: str,
                         drivers_license_number: str = "", drivers_license_path: str = "",
                         vehicle_registration_document_path: str = "") -> dict:
    provider = fetchone("SELECT * FROM logistics_providers WHERE phone = ?", (phone,))
    if not provider:
        return {"ok": False, "error": "Account not found."}
    if provider["kyc_status"] == "UNDER_REVIEW":
        return {"ok": False, "error": "Your verification is already under review."}
    if provider["kyc_status"] == "VERIFIED":
        return {"ok": False, "error": "Your account is already verified."}

    if id_type not in ID_TYPES:
        return {"ok": False, "error": "Select a valid ID type."}
    if not id_number or len(id_number.strip()) < 4:
        return {"ok": False, "error": "Enter a valid ID number."}
    if not id_document_path:
        return {"ok": False, "error": "Upload a copy of your ID document."}
    if not vehicle_registration_document_path:
        return {"ok": False, "error": "Upload your vehicle registration document."}

    with get_db() as conn:
        conn.execute(
            """UPDATE logistics_providers SET
                 kyc_status = 'UNDER_REVIEW',
                 id_type = ?, id_number = ?, id_document_path = ?,
                 drivers_license_number = ?, drivers_license_path = ?,
                 vehicle_registration_document_path = ?,
                 kyc_submitted_at = datetime('now')
               WHERE phone = ?""",
            (id_type, id_number.strip(), id_document_path,
             drivers_license_number.strip() or None, drivers_license_path or None,
             vehicle_registration_document_path, phone),
        )
        conn.execute(
            """INSERT INTO kyc_verifications
               (user_type, user_id, verification_type, status, submitted_at)
               VALUES ('logistics_provider', ?, 'identity_vehicle', 'PENDING', datetime('now'))""",
            (phone,),
        )
    return {"ok": True}


def get_pending_provider_kyc_verifications() -> list:
    rows = fetchall(
        """SELECT v.*, p.name, p.business_name, p.email, p.operating_area,
                  p.vehicle_type, p.vehicle_registration,
                  p.id_type, p.id_number, p.id_document_path,
                  p.drivers_license_number, p.drivers_license_path,
                  p.vehicle_registration_document_path
           FROM kyc_verifications v
           JOIN logistics_providers p ON p.phone = v.user_id
           WHERE v.user_type = 'logistics_provider' AND v.status IN ('PENDING', 'UNDER_REVIEW')
           ORDER BY v.submitted_at ASC"""
    )
    return [dict(r) for r in rows]


def admin_review_provider_kyc(verification_id: int, decision: str, reviewed_by: str,
                               rejection_reason: str = "") -> dict:
    if decision not in ("VERIFIED", "REJECTED"):
        return {"ok": False, "error": "Invalid decision."}
    if decision == "REJECTED" and not rejection_reason.strip():
        return {"ok": False, "error": "A rejection reason is required."}

    record = fetchone("SELECT * FROM kyc_verifications WHERE id = ?", (verification_id,))
    if not record:
        return {"ok": False, "error": "Verification record not found."}
    if record["status"] not in ("PENDING", "UNDER_REVIEW"):
        return {"ok": False, "error": "This record has already been reviewed."}

    with get_db() as conn:
        conn.execute(
            """UPDATE kyc_verifications
               SET status = ?, verified_at = datetime('now'),
                   reviewed_by = ?, rejection_reason = ?
               WHERE id = ?""",
            (decision, reviewed_by, rejection_reason.strip() or None, verification_id),
        )
        conn.execute(
            """UPDATE logistics_providers
               SET kyc_status = ?, kyc_reviewed_at = datetime('now'),
                   kyc_rejection_reason = ?
               WHERE phone = ?""",
            (decision, rejection_reason.strip() or None, record["user_id"]),
        )
    return {"ok": True}


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


def create_quote_request(txn_id: str, pickup_location: str,
                         delivery_location: str, requested_by: str = "system") -> dict:
    escrow = fetchone("SELECT * FROM escrow_ledger WHERE txn_id = ?", (txn_id,))
    if not escrow:
        return {"ok": False, "error": "Order not found."}
    if escrow["status"] not in ("QUOTE_REQUIRED", "QUOTE_PENDING"):
        return {"ok": False, "error": f"Cannot request a quote while order is {escrow['status']}."}
    if not pickup_location or not delivery_location:
        return {"ok": False, "error": "Pickup and delivery locations are required."}

    existing = fetchone("SELECT * FROM logistics_quotes WHERE order_id = ?", (txn_id,))
    if existing:
        return {"ok": True, "quote_id": existing["id"], "already_exists": True}

    with get_db() as conn:
        conn.execute(
            """INSERT INTO logistics_quotes
               (order_id, pickup_location, delivery_location,
                product_name, quantity, status, quoted_by)
               VALUES (?, ?, ?, ?, ?, 'PENDING', ?)""",
            (txn_id, pickup_location, delivery_location,
             escrow["crop"], escrow["quantity_bags"], requested_by),
        )
        conn.execute(
            "UPDATE escrow_ledger SET status='QUOTE_PENDING' WHERE txn_id=?",
            (txn_id,),
        )
    row = fetchone("SELECT * FROM logistics_quotes WHERE order_id = ?", (txn_id,))
    return {"ok": True, "quote_id": row["id"]}


def get_quote_for_order(txn_id: str):
    row = fetchone("SELECT * FROM logistics_quotes WHERE order_id = ?", (txn_id,))
    return dict(row) if row else None


def get_pending_quote_requests(limit: int = 50) -> list:
    rows = fetchall(
        """SELECT q.*, e.buyer_phone, e.farmer_phone, e.buyer_name,
                  e.delivery_address, e.delivery_city, e.delivery_state,
                  e.product_amount, e.buyer_platform_fee, e.buyer_total,
                  f.name AS farmer_name, f.location AS farmer_location
           FROM logistics_quotes q
           JOIN escrow_ledger e ON e.txn_id = q.order_id
           JOIN farmers f ON f.phone = e.farmer_phone
           WHERE q.status IN ('PENDING', 'QUOTED', 'SELECTED')
             AND e.status IN ('QUOTE_PENDING', 'QUOTE_READY')
           ORDER BY q.created_at ASC
           LIMIT ?""",
        (limit,),
    )
    return [dict(r) for r in rows]


def record_quote(txn_id: str, quote_amount: float, origin: str, destination: str,
                 logistics_provider_id=None, quoted_by: str = "operations",
                 expires_at: str = None) -> dict:
    """
    Operations records/selects a verified-provider logistics quote before
    buyer payment. This locks the quoted buyer price but does not call
    Paystack; the buyer must explicitly accept first.
    """
    escrow = fetchone("SELECT * FROM escrow_ledger WHERE txn_id = ?", (txn_id,))
    if not escrow:
        return {"ok": False, "error": "Transaction not found."}
    if escrow["status"] in ("PENDING_PAYMENT", "PAYMENT_INITIALIZED",
                            "ESCROW_LOCKED", "FUNDS_HELD",
                            "LOGISTICS_ASSIGNED", "PICKED_UP",
                            "IN_TRANSIT", "DELIVERED_PENDING_CONFIRMATION",
                            "CONFIRMED", "SETTLEMENT", "COMPLETED"):
        return {"ok": False, "error":
                f"Cannot add a quote once the order is {escrow['status']} - "
                f"payment has already been initialized."}
    if escrow["status"] not in ("QUOTE_REQUIRED", "QUOTE_PENDING", "QUOTE_READY"):
        return {"ok": False, "error": f"Order is not awaiting a logistics quote ({escrow['status']})."}
    if quote_amount <= 0:
        return {"ok": False, "error": "Quote amount must be greater than zero."}

    provider = _get_provider_by_phone_or_id(logistics_provider_id)
    if logistics_provider_id and not provider:
        return {"ok": False, "error": "Selected logistics provider was not found."}
    if provider and provider["kyc_status"] != "VERIFIED":
        return {"ok": False, "error": "Only verified logistics providers can be selected for a quote."}

    existing_quote = fetchone("SELECT * FROM logistics_quotes WHERE order_id = ?", (txn_id,))
    if existing_quote and existing_quote["buyer_accepted_at"]:
        return {"ok": False, "error": "Buyer has accepted this quote; reopen the order before changing it."}

    fees = fee_service.calculate_logistics_fees(quote_amount)
    logistics_fee_percent = fee_service.get_platform_config()["logistics_fee_percent"]

    with get_db() as conn:
        if existing_quote:
            conn.execute(
                """UPDATE logistics_quotes
                   SET pickup_location=?, delivery_location=?,
                       logistics_provider_id=?, quoted_amount=?,
                       commission_rate=?, commission_amount=?,
                       provider_net_amount=?, status='LOCKED',
                       quoted_by=?, accepted_at=datetime('now'),
                       expires_at=COALESCE(?, expires_at),
                       locked_at=datetime('now')
                   WHERE order_id=?""",
                (origin, destination, provider["id"] if provider else None,
                 fees["logistics_amount"], logistics_fee_percent,
                 fees["logistics_platform_fee"], fees["logistics_settlement_amount"],
                 quoted_by, expires_at, txn_id),
            )
        else:
            conn.execute(
                """INSERT INTO logistics_quotes
                   (order_id, pickup_location, delivery_location,
                    product_name, quantity, logistics_provider_id,
                    quoted_amount, commission_rate, commission_amount,
                    provider_net_amount, status, quoted_by,
                    accepted_at, expires_at, locked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'LOCKED', ?,
                           datetime('now'), ?, datetime('now'))""",
                (txn_id, origin, destination, escrow["crop"], escrow["quantity_bags"],
                 provider["id"] if provider else None, fees["logistics_amount"],
                 logistics_fee_percent, fees["logistics_platform_fee"],
                 fees["logistics_settlement_amount"], quoted_by, expires_at),
            )

        log = conn.execute(
            "SELECT * FROM logistics_log WHERE txn_id = ?", (txn_id,)
        ).fetchone()
        if log:
            conn.execute(
                """UPDATE logistics_log
                   SET provider_id=?, origin=?, destination=?, status='QUOTED',
                       quote_amount=?, platform_fee=?, settlement_amount=?
                   WHERE txn_id=?""",
                (provider["id"] if provider else None, origin, destination,
                 fees["logistics_amount"], fees["logistics_platform_fee"],
                 fees["logistics_settlement_amount"], txn_id),
            )
        else:
            conn.execute(
                """INSERT INTO logistics_log
                   (txn_id, provider_id, origin, destination, status,
                    quote_amount, platform_fee, settlement_amount)
                   VALUES (?, ?, ?, ?, 'QUOTED', ?, ?, ?)""",
                (txn_id, provider["id"] if provider else None, origin, destination,
                 fees["logistics_amount"], fees["logistics_platform_fee"],
                 fees["logistics_settlement_amount"]),
            )
        conn.execute(
            """UPDATE escrow_ledger
               SET logistics_amount=?, logistics_platform_fee=?,
                   logistics_settlement_amount=?,
                   buyer_total = product_amount + buyer_platform_fee + ?,
                   sowtrust_total_revenue = buyer_platform_fee + seller_platform_fee + ?,
                   status='QUOTE_READY'
               WHERE txn_id=?""",
            (fees["logistics_amount"], fees["logistics_platform_fee"],
             fees["logistics_settlement_amount"],
             fees["logistics_amount"], fees["logistics_platform_fee"], txn_id),
        )
    return {"ok": True, **fees}


def accept_locked_quote(txn_id: str, buyer_phone: str) -> dict:
    escrow = fetchone(
        "SELECT * FROM escrow_ledger WHERE txn_id = ? AND buyer_phone = ?",
        (txn_id, buyer_phone),
    )
    if not escrow:
        return {"ok": False, "error": "Order not found."}
    if escrow["status"] == "BUYER_ACCEPTED_QUOTE":
        return {"ok": True, "already_accepted": True}
    if escrow["status"] != "QUOTE_READY":
        return {"ok": False, "error": f"Order is not ready for quote acceptance ({escrow['status']})."}

    quote = fetchone(
        """SELECT * FROM logistics_quotes
           WHERE order_id = ? AND status = 'LOCKED'
             AND (expires_at IS NULL OR expires_at > datetime('now'))""",
        (txn_id,),
    )
    if not quote:
        return {"ok": False, "error": "No active locked quote is available for this order."}

    with get_db() as conn:
        conn.execute(
            """UPDATE logistics_quotes
               SET buyer_accepted_at=datetime('now')
               WHERE id=? AND buyer_accepted_at IS NULL""",
            (quote["id"],),
        )
        conn.execute(
            "UPDATE escrow_ledger SET status='BUYER_ACCEPTED_QUOTE' WHERE txn_id=?",
            (txn_id,),
        )
        conn.execute(
            "INSERT INTO audit_log (actor, action, details) VALUES (?, ?, ?)",
            (buyer_phone, "LOGISTICS_QUOTE_ACCEPTED",
             f"TXN:{txn_id} QUOTE:{quote['quoted_amount']}"),
        )
    return {"ok": True}


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
    if log["provider_id"] and log["provider_id"] != provider["id"]:
        return {"ok": False, "error": "This job is already assigned to another provider."}
    if log["status"] == "ASSIGNED":
        return {"ok": False, "error": "This job is already assigned."}

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


def get_available_jobs(provider_phone: str = None, limit: int = 5):
    """Quoted jobs with confirmed buyer payment, not yet assigned."""
    provider = get_provider(provider_phone) if provider_phone else None
    provider_id = provider["id"] if provider else None
    return fetchall(
        """SELECT l.*, e.crop, e.quantity_bags
           FROM   logistics_log l
           JOIN   escrow_ledger e ON e.txn_id = l.txn_id
           WHERE  (l.provider_id IS NULL OR l.provider_id = ?)
             AND  l.status = 'QUOTED'
             AND  e.status = 'ESCROW_LOCKED'
           ORDER  BY l.created_at DESC LIMIT ?""",
        (provider_id, limit),
    )
