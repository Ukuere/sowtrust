"""
Sowtrust — Simulate a Paystack charge.success webhook.

Paystack's test-mode dashboard doesn't appear to offer a way to manually
complete a pending bank_transfer charge (we checked). This script sends
your OWN Flask app a webhook payload shaped exactly like Paystack's real
one, signed with your REAL secret key — so your signature verification,
webhook handler, and escrow-locking logic all get genuinely exercised.

This does NOT prove Paystack's servers can reach your ngrok URL (only a
real completed transfer would prove that end of it) — but it proves
every single thing YOUR code controls, which is the part most likely to
have bugs.

Usage:
    python scripts/simulate_webhook.py <reference> <amount_naira>

Example (matching your last test order):
    python scripts/simulate_webhook.py PAY-264E0FFAC03E 50000
"""
import sys
import os
import json
import hmac
import hashlib
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import config

if len(sys.argv) != 3:
    print(__doc__)
    sys.exit(1)

reference = sys.argv[1]
amount_naira = float(sys.argv[2])

payload = json.dumps({
    "event": "charge.success",
    "data": {
        "reference": reference,
        "amount": int(amount_naira * 100),  # kobo
        "status": "success",
    }
}).encode()

signature = hmac.new(
    config.PAYSTACK_SECRET_KEY.encode("utf-8"), payload, hashlib.sha512
).hexdigest()

print(f"Sending simulated charge.success webhook for {reference} (NGN {amount_naira:,.0f})...")
print("Sending to your LOCAL server directly (http://localhost:5000) — no need to go via ngrok for this test.\n")

resp = requests.post(
    "http://localhost:5000/webhooks/paystack",
    data=payload,
    headers={
        "Content-Type": "application/json",
        "x-paystack-signature": signature,
    },
)

print(f"HTTP status: {resp.status_code}")
print(f"Response: {resp.text}")

if resp.status_code == 200:
    print("\n✅ Webhook accepted. Check your Flask server's terminal log for SMS output —")
    print("   you should see the farmer's 'Payment RECEIVED & LOCKED!' message printed there.")
else:
    print("\n⚠️  Webhook was rejected — check the response above.")
