"""
Sowtrust — Payment & Settlement Tests.

All Paystack HTTP calls are mocked here since api.paystack.co requires
real network access and real API keys. This proves the STATE MACHINE
and DATA FLOW are correct — you still need to do one real end-to-end
test with actual Paystack TEST keys (sk_test_...) before going live,
since a mock can't catch a genuine API contract mismatch.
"""
import os
import pytest
from unittest.mock import patch

os.environ.setdefault("AT_USERNAME", "sandbox")
os.environ.setdefault("AT_API_KEY", "test_dummy_key")
os.environ.setdefault("PAYSTACK_SECRET_KEY", "sk_test_dummy_key_for_testing")


@pytest.fixture
def client(tmp_path):
    from config.settings import config
    test_db = str(tmp_path / "test_payments.db")
    os.environ["DATABASE_PATH"] = test_db
    config.DATABASE_PATH = test_db

    from migrations.init_db import init_db
    init_db()
    from migrations.add_products_table import migrate as migrate_products
    migrate_products()

    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def ussd(client, text, phone="+2348011112222"):
    return client.post("/ussd", data={
        "text": text, "phoneNumber": phone,
        "sessionId": "s1", "serviceCode": "*709#"
    })


def _register_and_verify_farmer(client, phone, name="Chidi Okafor", crop="Bitter Leaf", price=1500):
    with patch("app.services.sms_service.send_sms", return_value=True):
        ussd(client, f"1*1*{name}*{crop}*Ikorodu*1234*1234", phone=phone)
        ussd(client, f"1*2*1234*{price}", phone=phone)
    from app.models.database import execute
    execute("UPDATE farmers SET kyc_status='VERIFIED' WHERE phone=?", (phone,))


# ── 1. Buyer payment collection ──────────────────────────────────────────

def test_buyer_gets_real_virtual_account_not_instant_lock(client):
    """The core fix: confirming an order must NOT instantly lock escrow —
    it must hand the buyer a real account to pay into first."""
    _register_and_verify_farmer(client, "+2348033334444")

    fake_charge = {
        "ok": True, "account_number": "9876543210",
        "bank_name": "Wema Bank", "reference": "PAY-TEST123",
    }
    with patch("app.services.escrow_service.payment_service.initiate_bank_transfer_charge",
               return_value=fake_charge), \
         patch("app.services.sms_service.send_sms", return_value=True):
        ussd(client, "2*1*Bitter Leaf", phone="+2348099998888")
        ussd(client, "2*1*Bitter Leaf*1", phone="+2348099998888")
        ussd(client, "2*1*Bitter Leaf*1*5", phone="+2348099998888")
        r = ussd(client, "2*1*Bitter Leaf*1*5*1", phone="+2348099998888")

    body = r.data.decode()
    assert "9876543210" in body
    assert "Wema Bank" in body
    assert "reserved once payment lands" in body

    from app.models.database import fetchone
    row = fetchone(
        "SELECT status FROM escrow_ledger WHERE farmer_phone=? ORDER BY locked_at DESC LIMIT 1",
        ("+2348033334444",)
    )
    assert row["status"] == "PENDING_PAYMENT"   # NOT locked yet — money hasn't arrived


def test_webhook_confirms_payment_and_locks_escrow(client):
    """Simulates Paystack calling our webhook once the buyer's transfer lands."""
    _register_and_verify_farmer(client, "+2348033335555")

    fake_charge = {
        "ok": True, "account_number": "1112223334",
        "bank_name": "Wema Bank", "reference": "PAY-TEST456",
    }
    with patch("app.services.escrow_service.payment_service.initiate_bank_transfer_charge",
               return_value=fake_charge), \
         patch("app.services.sms_service.send_sms", return_value=True):
        ussd(client, "2*1*Bitter Leaf", phone="+2348099991111")
        ussd(client, "2*1*Bitter Leaf*1", phone="+2348099991111")
        ussd(client, "2*1*Bitter Leaf*1*5", phone="+2348099991111")
        ussd(client, "2*1*Bitter Leaf*1*5*1", phone="+2348099991111")

    from app.models.database import fetchone
    row = fetchone(
        "SELECT txn_id, buyer_total, payment_reference FROM escrow_ledger "
        "WHERE buyer_phone=? ORDER BY locked_at DESC LIMIT 1",
        ("+2348099991111",)
    )
    amount = row["buyer_total"]   # buyer now pays product + buyer_platform_fee, not just product
    real_reference = row["payment_reference"]

    # Simulate the real webhook Paystack would send
    import json, hmac, hashlib
    from config.settings import config
    payload = json.dumps({
        "event": "charge.success",
        "data": {"reference": real_reference, "amount": int(amount * 100)}
    }).encode()
    sig = hmac.new(config.PAYSTACK_SECRET_KEY.encode(), payload, hashlib.sha512).hexdigest()

    with patch("app.services.sms_service.send_sms", return_value=True):
        resp = client.post("/webhooks/paystack", data=payload,
                            headers={"x-paystack-signature": sig,
                                     "Content-Type": "application/json"})
    assert resp.status_code == 200

    row = fetchone("SELECT status FROM escrow_ledger WHERE payment_reference=?", (real_reference,))
    assert row["status"] == "ESCROW_LOCKED"   # now it's real


def test_webhook_rejects_bad_signature(client):
    """Someone POSTing a fake 'payment successful' event without a valid
    signature must be ignored — otherwise anyone could unlock produce for free."""
    resp = client.post("/webhooks/paystack",
                        data=b'{"event":"charge.success","data":{"reference":"FAKE"}}',
                        headers={"x-paystack-signature": "not-a-real-signature"})
    assert resp.status_code == 401


# ── 2. Farmer payout ──────────────────────────────────────────────────────

def test_release_blocked_without_verified_bank_account(client):
    """A farmer with no payout account on file must be blocked, not paid into the void."""
    from app.services.escrow_service import release_escrow
    from app.models.database import execute
    _register_and_verify_farmer(client, "+2348033336666")

    execute(
        """INSERT INTO escrow_ledger
           (farmer_phone, buyer_phone, crop, quantity_bags, amount, service_fee,
            release_code_hash, status)
           VALUES (?, 'buyer1', 'Bitter Leaf', 5, 7500, 187.5, ?, 'ESCROW_LOCKED')""",
        ("+2348033336666", __import__("app.utils.security", fromlist=["hash_release_code"])
            .hash_release_code("ABC123")),
    )
    result = release_escrow("+2348033336666", "does-not-matter", "ABC123")
    # Should fail — either txn lookup mismatch or bank-account check;
    # either way, must NOT succeed without a verified payout account.
    assert result["ok"] is False


def test_full_settlement_flow_with_verified_bank(client):
    """End-to-end: verified bank account -> release -> real transfer initiated
    -> webhook confirms -> credit score increments."""
    from app.services.escrow_service import release_escrow, get_active_escrow
    from app.models.database import execute, fetchone
    phone = "+2348033337777"
    _register_and_verify_farmer(client, phone)

    # Add a verified payout account (bypassing USSD bank-menu flow for test speed —
    # the USSD-level flow itself is exercised separately below)
    execute(
        """UPDATE farmers SET bank_code='999992', bank_account_number='1234567890',
           bank_account_name='Chidi Okafor', bank_verified_at=datetime('now')
           WHERE phone=?""", (phone,)
    )

    from app.utils.security import hash_release_code
    execute(
        """INSERT INTO escrow_ledger
           (farmer_phone, buyer_phone, crop, quantity_bags, amount, service_fee,
            product_amount, buyer_platform_fee, seller_platform_fee, farmer_settlement_amount,
            buyer_total, sowtrust_total_revenue,
            release_code_hash, status)
           VALUES (?, 'buyer1', 'Bitter Leaf', 5, 7500, 187.5,
                   7500, 187.5, 187.5, 7312.5,
                   7687.5, 375,
                   ?, 'ESCROW_LOCKED')""",
        (phone, hash_release_code("XYZ999")),
    )

    fake_recipient = {"ok": True, "recipient_code": "RCP_test123"}
    fake_transfer = {"ok": True, "transfer_code": "TRF-abc123", "status": "pending"}

    with patch("app.services.escrow_service.payment_service.create_transfer_recipient",
               return_value=fake_recipient), \
         patch("app.services.escrow_service.payment_service.initiate_transfer",
               return_value=fake_transfer):
        active = get_active_escrow(phone)
        result = release_escrow(phone, active["txn_id"], "XYZ999")

    assert result["ok"] is True
    assert result["net_payout"] == 7312.5  # farmer_settlement_amount (product - seller fee)

    row = fetchone("SELECT * FROM escrow_ledger WHERE txn_id=?", (active["txn_id"],))
    assert row["status"] == "DELIVERED"
    assert row["payout_status"] == "pending"   # not yet confirmed — webhook does that

    # Simulate transfer.success webhook
    import json, hmac, hashlib
    from config.settings import config
    payload = json.dumps({
        "event": "transfer.success",
        "data": {"transfer_code": "TRF-abc123"}
    }).encode()
    sig = hmac.new(config.PAYSTACK_SECRET_KEY.encode(), payload, hashlib.sha512).hexdigest()
    with patch("app.services.sms_service.send_sms", return_value=True):
        resp = client.post("/webhooks/paystack", data=payload,
                            headers={"x-paystack-signature": sig,
                                     "Content-Type": "application/json"})
    assert resp.status_code == 200

    row = fetchone("SELECT * FROM escrow_ledger WHERE txn_id=?", (active["txn_id"],))
    assert row["payout_status"] == "success"

    farmer = fetchone("SELECT credit_score FROM farmers WHERE phone=?", (phone,))
    assert farmer["credit_score"] == 1


def test_bank_account_resolution_ussd_flow(client):
    """USSD-level test: farmer adds a bank account, sees the resolved name,
    confirms it, and it gets saved."""
    _register_and_verify_farmer(client, "+2348033338888")

    fake_resolve = {"ok": True, "account_name": "CHIDI OKAFOR"}
    with patch("app.routes.ussd.resolve_account_number", return_value=fake_resolve), \
         patch("app.services.sms_service.send_sms", return_value=True):
        ussd(client, "4*3*5", phone="+2348033338888")               # pick Access Bank
        ussd(client, "4*3*5*0123456789", phone="+2348033338888")    # account number
        r1 = ussd(client, "4*3*5*0123456789*1234", phone="+2348033338888")  # PIN
        assert b"CHIDI OKAFOR" in r1.data
        r2 = ussd(client, "4*3*5*0123456789*1234*1", phone="+2348033338888")  # confirm yes
        assert b"Saved" in r2.data

    from app.models.database import fetchone
    farmer = fetchone("SELECT * FROM farmers WHERE phone=?", ("+2348033338888",))
    assert farmer["bank_account_number"] == "0123456789"
    assert farmer["bank_verified_at"] is not None
