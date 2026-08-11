"""
Sowtrust — Payment Service (Paystack integration).

Handles real money movement:
  1. COLLECTION — buyer pays real cash into escrow via a one-time bank
     transfer (works from ANY Nigerian bank app or USSD banking code —
     the buyer does not need an account with us or with Paystack).
  2. PAYOUT — farmer gets paid out via Paystack Transfer into a NUBAN
     account, which can be a traditional bank OR a free digital wallet
     (OPay, Kuda, PalmPay etc.) — this is how we solve "no bank account"
     without building our own custody/wallet infrastructure.

Requires PAYSTACK_SECRET_KEY in your environment. Get a free test key
from https://dashboard.paystack.com (test keys start with sk_test_,
live keys with sk_live_ — never use a live key until you've fully
tested the flow end to end in test mode).

IMPORTANT: We do NOT hold customer funds ourselves. Paystack is a
licensed payment processor — money sits in their regulated settlement
flow, we only orchestrate + keep our own ledger of who owns what.
"""
import hashlib
import hmac
import requests
from config.settings import config

BASE_URL = "https://api.paystack.co"


def _headers():
    return {
        "Authorization": f"Bearer {config.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


class PaystackError(Exception):
    pass


# ── 1. BUYER PAYMENT COLLECTION ──────────────────────────────────────────

def initiate_bank_transfer_charge(email: str, amount_naira: float, reference: str) -> dict:
    """
    Generate a one-time virtual account number for the buyer to transfer
    into. Works with any Nigerian bank app, USSD banking (*901#, *737#
    etc.), or mobile money — the buyer needs NO prior relationship with
    us or Paystack.

    Returns: {
      "ok": True,
      "account_number": "1234567890",
      "bank_name": "Wema Bank",
      "reference": "...",
      "expires_at": "..."
    }
    """
    try:
        resp = requests.post(
            f"{BASE_URL}/charge",
            headers=_headers(),
            json={
                "email": email,
                "amount": int(round(amount_naira * 100)),  # Paystack uses kobo
                "currency": "NGN",
                "reference": reference,
                "bank_transfer": {"account_expires_at": None},
            },
            timeout=15,
        )
        data = resp.json()
        if not data.get("status"):
            return {"ok": False, "error": data.get("message", "Charge initiation failed")}

        # Verified against Paystack's ACTUAL response shape (confirmed via
        # live test call — their docs snippets didn't match this exactly):
        # account_number and account_expires_at sit at the top level of
        # `data`, and the bank name is nested under `data.bank.name`.
        tx = data["data"]
        bank_info = tx.get("bank", {}) or {}
        return {
            "ok": True,
            "account_number": tx.get("account_number"),
            "bank_name": bank_info.get("name"),
            "reference": tx.get("reference", reference),
            "expires_at": tx.get("account_expires_at"),
        }
    except requests.RequestException as e:
        return {"ok": False, "error": f"Network error contacting Paystack: {e}"}


def verify_transaction(reference: str) -> dict:
    """
    Explicitly check a transaction's status (used as a fallback if the
    webhook is delayed or missed — never rely on webhooks alone for
    money movement, always allow polling/verification too).
    """
    try:
        resp = requests.get(
            f"{BASE_URL}/transaction/verify/{reference}",
            headers=_headers(), timeout=15,
        )
        data = resp.json()
        if not data.get("status"):
            return {"ok": False, "error": data.get("message")}
        tx = data["data"]
        return {
            "ok": True,
            "paid": tx.get("status") == "success",
            "amount_naira": tx.get("amount", 0) / 100,
            "reference": tx.get("reference"),
        }
    except requests.RequestException as e:
        return {"ok": False, "error": f"Network error contacting Paystack: {e}"}


# ── 2. FARMER PAYOUT VERIFICATION & TRANSFER ─────────────────────────────

def resolve_account_number(account_number: str, bank_code: str) -> dict:
    """
    CRITICAL SAFETY CHECK — before saving a farmer's payout destination,
    confirm the account actually exists and see whose name is on it.
    The USSD flow should show this name back to the farmer to confirm
    it's really theirs before saving — prevents fraud and typos sending
    money to a stranger's account.
    """
    try:
        resp = requests.get(
            f"{BASE_URL}/bank/resolve",
            headers=_headers(),
            params={"account_number": account_number, "bank_code": bank_code},
            timeout=15,
        )
        data = resp.json()
        if not data.get("status"):
            return {"ok": False, "error": data.get("message", "Could not verify account")}
        return {"ok": True, "account_name": data["data"]["account_name"]}
    except requests.RequestException as e:
        return {"ok": False, "error": f"Network error contacting Paystack: {e}"}


def create_transfer_recipient(name: str, account_number: str, bank_code: str) -> dict:
    """Register the farmer as a transfer recipient (required once, before any payout)."""
    try:
        resp = requests.post(
            f"{BASE_URL}/transferrecipient",
            headers=_headers(),
            json={
                "type": "nuban",
                "name": name,
                "account_number": account_number,
                "bank_code": bank_code,
                "currency": "NGN",
            },
            timeout=15,
        )
        data = resp.json()
        if not data.get("status"):
            return {"ok": False, "error": data.get("message", "Could not create recipient")}
        return {"ok": True, "recipient_code": data["data"]["recipient_code"]}
    except requests.RequestException as e:
        return {"ok": False, "error": f"Network error contacting Paystack: {e}"}


def initiate_transfer(recipient_code: str, amount_naira: float, reference: str, reason: str) -> dict:
    """
    Actually send money to the farmer. This is real settlement —
    the missing piece the old MVP's "Withdraw Funds" only simulated.
    """
    try:
        resp = requests.post(
            f"{BASE_URL}/transfer",
            headers=_headers(),
            json={
                "source": "balance",
                "amount": int(round(amount_naira * 100)),
                "recipient": recipient_code,
                "reference": reference,
                "reason": reason,
            },
            timeout=15,
        )
        data = resp.json()
        if not data.get("status"):
            return {"ok": False, "error": data.get("message", "Transfer failed")}
        tx = data["data"]
        return {
            "ok": True,
            "transfer_code": tx.get("transfer_code"),
            "status": tx.get("status"),  # "success" (instant) or "pending" (queued for review)
        }
    except requests.RequestException as e:
        return {"ok": False, "error": f"Network error contacting Paystack: {e}"}


def initiate_refund(transaction_reference: str, amount_naira: float | None = None) -> dict:
    """
    Refund a buyer's payment — used when an escrow expires (farmer never
    delivered / release code never entered within the window). Without
    this, an expired escrow would just strand the buyer's real money
    with no way back to them.
    Omit amount_naira for a full refund.
    """
    try:
        payload = {"transaction": transaction_reference}
        if amount_naira is not None:
            payload["amount"] = int(round(amount_naira * 100))
        resp = requests.post(
            f"{BASE_URL}/refund", headers=_headers(), json=payload, timeout=15,
        )
        data = resp.json()
        if not data.get("status"):
            return {"ok": False, "error": data.get("message", "Refund failed")}
        return {"ok": True, "refund_reference": data["data"].get("id")}
    except requests.RequestException as e:
        return {"ok": False, "error": f"Network error contacting Paystack: {e}"}


# ── 3. WEBHOOK SECURITY ──────────────────────────────────────────────────

def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    """
    Paystack signs every webhook with your secret key. ALWAYS verify this
    before trusting a webhook payload — otherwise anyone could POST a fake
    "payment successful" event to your server and steal produce for free.
    """
    if not signature_header or not config.PAYSTACK_SECRET_KEY:
        return False
    computed = hmac.new(
        config.PAYSTACK_SECRET_KEY.encode("utf-8"), raw_body, hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(computed, signature_header)
