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
from flask import Blueprint, request, jsonify
from app.services.payment_service import verify_webhook_signature
from app.services import escrow_service
from app.services import logistics_service

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

    if event_type == "charge.success":
        reference = data.get("reference")
        amount_naira = data.get("amount", 0) / 100
        escrow_service.confirm_payment_received(reference, amount_naira)

    elif event_type == "transfer.success":
        transfer_code = data.get("transfer_code")
        # A transfer could be either a farmer settlement OR a logistics
        # provider settlement — try farmer first, fall through to logistics.
        result = escrow_service.confirm_payout_success(transfer_code)
        if not result.get("ok"):
            logistics_service.confirm_payout_success(transfer_code)

    elif event_type == "transfer.failed" or event_type == "transfer.reversed":
        transfer_code = data.get("transfer_code")
        result = escrow_service.mark_payout_failed(transfer_code, event_type)
        if not result.get("ok"):
            logistics_service.mark_payout_failed(transfer_code, event_type)

    elif event_type == "refund.processed":
        # Confirms a buyer refund (triggered by expire_stale_escrows) actually
        # completed on Paystack's side. We already marked the escrow EXPIRED
        # when the refund was requested — this is a confirmation log point,
        # not a new state transition, so no escrow_service call needed here.
        pass

    # Paystack just needs a 200 — it retries on anything else.
    return jsonify({"status": "received"}), 200
