"""
Sowtrust — Debug: inspect the RAW Paystack /charge response.

Run this once to see exactly what Paystack actually returns for a
bank_transfer charge on YOUR account — this is more reliable than any
documentation snippet, since response shape can vary by account
configuration/region. We'll use this to fix payment_service.py's
parsing to match reality exactly.

Usage: python scripts/debug_paystack_charge.py
"""
import sys
import os
import json
import uuid
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import config

resp = requests.post(
    "https://api.paystack.co/charge",
    headers={
        "Authorization": f"Bearer {config.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    },
    json={
        "email": "2348099990000@sowtrust.com",
        "amount": 5000000,  # NGN 50,000 in kobo
        "currency": "NGN",
        "reference": f"DEBUG-{uuid.uuid4().hex[:10].upper()}",
        "bank_transfer": {"account_expires_at": None},
    },
)

print(f"HTTP status: {resp.status_code}\n")
print("RAW RESPONSE:")
print(json.dumps(resp.json(), indent=2))
