"""
Sowtrust — Paystack Webhooks.

Paystack calls this URL when something happens on their side: a buyer's
transfer landed (charge.success) or a farmer payout completed/failed
(transfer.success / transfer.failed). This is the ONLY trustworthy
signal that real money has moved — never mark escrow as paid just
because the USSD flow reached a certain screen.

Set this URL in your Paystack dashboard under Settings > API Keys & Webhooks:
    https://<your-domain>/webhooks/paystack
"""
import hashlib

from flask import Blueprint, request, jsonify
from app.models.database import fetchone, get_db
from app.services.payment_service import verify_webhook_signature
from app.services import escrow_service, logistics_service, payment_service

webhooks_bp = Blueprint("webhooks", __name__)


@webhooks_bp.route("/webhooks/paystack", methods=["POST"])
def paystack_webhook():
    signature = request.headers.get("x-paystack-signature", "")
    raw_body = request.get_data()

    if not verify_webhook_signature(raw_body, signature):
        # Don't process anything we can't verify came from Paystack.
        return jsonify({"status": "ignored"}), 401

    event = request.get_json(silent=True) or {}
    event_type = event.get("event")
    data = event.get("data", {})
    reference = data.get("reference") or data.get("transfer_code") or data.get("id")
    payload_hash = hashlib.sha256(raw_body).hexdigest()
    event_key = f"{event_type}:{reference or payload_hash}"

    with get_db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO payment_webhook_events
               (event_key, event_type, reference, payload_hash)
               VALUES (?, ?, ?, ?)""",
            (event_key, event_type or "unknown", str(reference or ""), payload_hash),
        )
    with get_db() as conn:
        claimed = conn.execute(
            """UPDATE payment_webhook_events SET status='PROCESSING'
               WHERE event_key=? AND status IN ('RECEIVED', 'RETRY')""",
            (event_key,),
        ).rowcount
    if not claimed:
        existing = fetchone(
            "SELECT status FROM payment_webhook_events WHERE event_key=?", (event_key,)
        )
        return jsonify({
            "status": "duplicate" if existing and existing["status"] == "PROCESSED" else "processing"
        }), 200

    def finish(status, error=""):
        with get_db() as conn:
            conn.execute(
                """UPDATE payment_webhook_events
                   SET status=?, error=?,
                       processed_at=CASE WHEN ?='PROCESSED' THEN datetime('now') ELSE processed_at END
                   WHERE event_key=?""",
                (status, error or None, status, event_key),
            )

    if event_type == "charge.success":
        verified = payment_service.verify_transaction(reference)
        if not verified.get("ok"):
            finish("RETRY", "Paystack verification unavailable")
            return jsonify({"status": "retry"}), 503
        if not verified.get("paid") or str(verified.get("reference")) != str(reference):
            finish("REJECTED", "Transaction was not verified as paid")
            return jsonify({"status": "rejected"}), 400
        if verified.get("currency") not in (None, "NGN"):
            finish("REJECTED", "Unexpected transaction currency")
            return jsonify({"status": "rejected"}), 400
        result = escrow_service.confirm_payment_received(
            reference, amount_paid_kobo=verified["amount_kobo"]
        )
        finish("PROCESSED" if result.get("ok") else "REJECTED", result.get("error", ""))

    elif event_type == "transfer.success":
        transfer_code = data.get("transfer_code")
        # A transfer could be either a farmer settlement OR a logistics
        # provider settlement — try farmer first, fall through to logistics.
        result = escrow_service.confirm_payout_success(transfer_code)
        if not result.get("ok"):
            result = logistics_service.confirm_payout_success(transfer_code)
        finish("PROCESSED" if result.get("ok") else "REJECTED", result.get("error", ""))

    elif event_type == "transfer.failed" or event_type == "transfer.reversed":
        transfer_code = data.get("transfer_code")
        result = escrow_service.mark_payout_failed(transfer_code, event_type)
        if not result.get("ok"):
            result = logistics_service.mark_payout_failed(transfer_code, event_type)
        finish("PROCESSED" if result.get("ok") else "REJECTED", result.get("error", ""))

    elif event_type == "refund.processed":
        # Confirms a buyer refund (triggered by expire_stale_escrows) actually
        # completed on Paystack's side. We already marked the escrow EXPIRED
        # when the refund was requested — this is a confirmation log point,
        # not a new state transition, so no escrow_service call needed here.
        finish("PROCESSED")

    else:
        finish("PROCESSED")

    # Paystack just needs a 200 — it retries on anything else.
    return jsonify({"status": "received"}), 200
