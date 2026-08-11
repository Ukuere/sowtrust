import os
import pytest
from unittest.mock import patch

os.environ.setdefault("AT_USERNAME", "sandbox")
os.environ.setdefault("AT_API_KEY", "test_dummy_key")
os.environ.setdefault("PAYSTACK_SECRET_KEY", "sk_test_dummy_key_for_testing")


@pytest.fixture
def client(tmp_path):
    from config.settings import config
    test_db = str(tmp_path / "test_logistics.db")
    os.environ["DATABASE_PATH"] = test_db
    config.DATABASE_PATH = test_db

    from migrations.init_db import init_db
    init_db()
    from migrations.add_products_table import migrate as m1
    m1()
    from migrations.add_payments_columns import migrate as m2
    m2()
    from migrations.add_three_sided_fees import migrate as m3
    m3()
    from migrations.add_logistics_providers import migrate as m4
    m4()

    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


PROVIDER_PHONE = "+2348055550000"
BUYER_PHONE = "+2348099990000"
FARMER_PHONE = "+2348011110000"


def _setup_provider(client, verified=True, with_bank=True):
    from app.services.logistics_service import register_provider
    from app.models.database import execute
    with patch("app.services.sms_service.send_sms", return_value=True):
        register_provider(PROVIDER_PHONE, "Musa Ibrahim", "Lagos-Ibadan", "Truck", "1234")
    if verified:
        execute("UPDATE logistics_providers SET kyc_status='VERIFIED' WHERE phone=?", (PROVIDER_PHONE,))
    if with_bank:
        execute(
            """UPDATE logistics_providers SET bank_code='999992',
               bank_account_number='1234567890', bank_account_name='MUSA IBRAHIM',
               bank_verified_at=datetime('now') WHERE phone=?""",
            (PROVIDER_PHONE,)
        )


def _setup_order(client, status="ESCROW_LOCKED"):
    """Create a farmer + an order in the given state."""
    from app.models.database import execute, fetchone
    execute(
        """INSERT INTO farmers (phone, name, crop, location, pin_hash, price, kyc_status)
           VALUES (?, 'Test Farmer', 'Maize', 'Lagos', 'x', 25000, 'VERIFIED')""",
        (FARMER_PHONE,)
    )
    execute(
        """INSERT INTO escrow_ledger
           (farmer_phone, buyer_phone, crop, quantity_bags, amount, service_fee,
            product_amount, buyer_platform_fee, seller_platform_fee,
            farmer_settlement_amount, buyer_total, sowtrust_total_revenue,
            release_code_hash, status, payment_reference)
           VALUES (?, ?, 'Maize', 2, 50000, 1250,
                   50000, 1250, 1250, 48750, 51250, 2500,
                   'x', ?, 'PAY-LOGTEST')""",
        (FARMER_PHONE, BUYER_PHONE, status)
    )
    return fetchone("SELECT * FROM escrow_ledger WHERE payment_reference='PAY-LOGTEST'")


# ── Registration & verification ───────────────────────────────────────────

def test_provider_registers_as_pending_not_verified(client):
    """New providers must NOT be able to take jobs until an agent verifies them."""
    from app.models.database import fetchone
    _setup_provider(client, verified=False, with_bank=False)
    p = fetchone("SELECT * FROM logistics_providers WHERE phone=?", (PROVIDER_PHONE,))
    assert p is not None
    assert p["kyc_status"] == "PENDING"


def test_duplicate_provider_blocked(client):
    from app.services.logistics_service import register_provider
    _setup_provider(client)
    with patch("app.services.sms_service.send_sms", return_value=True):
        result = register_provider(PROVIDER_PHONE, "Someone Else", "Abuja", "Van", "5678")
    assert result["ok"] is False


# ── Quoting ───────────────────────────────────────────────────────────────

def test_quote_updates_buyer_total_and_keeps_ledger_balanced(client):
    """Adding a logistics quote must increase what the buyer pays by exactly
    the quote amount — the platform commission comes out of the provider's
    settlement, NOT added on top for the buyer."""
    from app.services.logistics_service import record_quote
    from app.models.database import fetchone
    order = _setup_order(client, status="PENDING_PAYMENT")

    result = record_quote(order["txn_id"], 15000, "Lagos", "Ibadan")
    assert result["ok"] is True
    assert result["logistics_platform_fee"] == 375
    assert result["logistics_settlement_amount"] == 14625

    row = fetchone("SELECT * FROM escrow_ledger WHERE txn_id=?", (order["txn_id"],))
    # buyer_total was 51,250 (product 50,000 + buyer fee 1,250); +15,000 logistics
    assert row["buyer_total"] == 66250
    # Revenue: buyer fee 1,250 + seller fee 1,250 + logistics fee 375
    assert row["sowtrust_total_revenue"] == 2875

    # The core invariant must still hold
    total_out = (row["farmer_settlement_amount"]
                 + row["logistics_settlement_amount"]
                 + row["sowtrust_total_revenue"])
    assert round(total_out, 2) == row["buyer_total"]


def test_quote_rejected_after_buyer_already_charged(client):
    """Can't change the price after the buyer has already paid."""
    from app.services.logistics_service import record_quote
    order = _setup_order(client, status="ESCROW_LOCKED")
    result = record_quote(order["txn_id"], 15000, "Lagos", "Ibadan")
    assert result["ok"] is False
    assert "already been charged" in result["error"]


# ── Assignment ────────────────────────────────────────────────────────────

def test_unverified_provider_cannot_take_job(client):
    from app.services.logistics_service import record_quote, assign_provider
    from app.models.database import execute
    _setup_provider(client, verified=False)
    order = _setup_order(client, status="PENDING_PAYMENT")
    record_quote(order["txn_id"], 15000, "Lagos", "Ibadan")
    execute("UPDATE escrow_ledger SET status='ESCROW_LOCKED' WHERE txn_id=?", (order["txn_id"],))

    result = assign_provider(order["txn_id"], PROVIDER_PHONE)
    assert result["ok"] is False
    assert "not yet verified" in result["error"]


def test_provider_without_bank_account_cannot_take_job(client):
    """No verified payout account = can't accept work, same rule as farmers."""
    from app.services.logistics_service import record_quote, assign_provider
    from app.models.database import execute
    _setup_provider(client, verified=True, with_bank=False)
    order = _setup_order(client, status="PENDING_PAYMENT")
    record_quote(order["txn_id"], 15000, "Lagos", "Ibadan")
    execute("UPDATE escrow_ledger SET status='ESCROW_LOCKED' WHERE txn_id=?", (order["txn_id"],))

    result = assign_provider(order["txn_id"], PROVIDER_PHONE)
    assert result["ok"] is False
    assert "payout account" in result["error"]


def test_job_cannot_be_double_assigned(client):
    from app.services.logistics_service import record_quote, assign_provider
    from app.models.database import execute
    _setup_provider(client)
    order = _setup_order(client, status="PENDING_PAYMENT")
    record_quote(order["txn_id"], 15000, "Lagos", "Ibadan")
    execute("UPDATE escrow_ledger SET status='ESCROW_LOCKED' WHERE txn_id=?", (order["txn_id"],))

    with patch("app.services.sms_service.send_sms", return_value=True):
        first = assign_provider(order["txn_id"], PROVIDER_PHONE)
    assert first["ok"] is True

    # A second provider tries to grab the same job
    from app.services.logistics_service import register_provider
    with patch("app.services.sms_service.send_sms", return_value=True):
        register_provider("+2348055551111", "Other Driver", "Lagos", "Van", "1234")
    execute("""UPDATE logistics_providers SET kyc_status='VERIFIED', bank_code='999992',
               bank_account_number='9999999999', bank_account_name='OTHER DRIVER',
               bank_verified_at=datetime('now') WHERE phone=?""", ("+2348055551111",))
    with patch("app.services.sms_service.send_sms", return_value=True):
        second = assign_provider(order["txn_id"], "+2348055551111")
    assert second["ok"] is False
    assert "already assigned" in second["error"]


# ── Delivery confirmation & settlement ────────────────────────────────────

def _assign_and_capture_code(client, order):
    """Assign a job and capture the delivery code sent to the buyer via SMS."""
    from app.services.logistics_service import assign_provider
    captured = {}
    def fake_sms(to, msg):
        if "Delivery Code" in msg:
            captured["code"] = msg.split("Delivery Code:")[1].split("\n")[0].strip()
        return True
    with patch("app.services.logistics_service.send_sms", side_effect=fake_sms):
        assign_provider(order["txn_id"], PROVIDER_PHONE)
    return captured.get("code")


def test_full_logistics_settlement_flow(client):
    """End-to-end: quote -> assign -> confirm delivery -> real transfer fires."""
    from app.services.logistics_service import record_quote, confirm_delivery
    from app.models.database import execute, fetchone
    _setup_provider(client)
    order = _setup_order(client, status="PENDING_PAYMENT")
    record_quote(order["txn_id"], 15000, "Lagos", "Ibadan")
    execute("UPDATE escrow_ledger SET status='ESCROW_LOCKED' WHERE txn_id=?", (order["txn_id"],))

    code = _assign_and_capture_code(client, order)
    assert code, "Delivery code should have been SMS'd to the buyer"

    fake_recipient = {"ok": True, "recipient_code": "RCP_log123"}
    fake_transfer = {"ok": True, "transfer_code": "TRF-log456", "status": "pending"}
    with patch("app.services.logistics_service.payment_service.create_transfer_recipient",
               return_value=fake_recipient), \
         patch("app.services.logistics_service.payment_service.initiate_transfer",
               return_value=fake_transfer), \
         patch("app.services.logistics_service.send_sms", return_value=True):
        result = confirm_delivery(PROVIDER_PHONE, order["txn_id"], code)

    assert result["ok"] is True
    assert result["settlement_amount"] == 14625   # 15,000 minus 2.5% commission

    log = fetchone("SELECT * FROM logistics_log WHERE txn_id=?", (order["txn_id"],))
    assert log["status"] == "DELIVERED"
    assert log["payout_status"] == "pending"
    assert log["delivery_code_used_at"] is not None


def test_wrong_delivery_code_rejected_and_logged(client):
    from app.services.logistics_service import record_quote, confirm_delivery
    from app.models.database import execute, fetchone
    _setup_provider(client)
    order = _setup_order(client, status="PENDING_PAYMENT")
    record_quote(order["txn_id"], 15000, "Lagos", "Ibadan")
    execute("UPDATE escrow_ledger SET status='ESCROW_LOCKED' WHERE txn_id=?", (order["txn_id"],))
    _assign_and_capture_code(client, order)

    result = confirm_delivery(PROVIDER_PHONE, order["txn_id"], "WRONGCODE")
    assert result["ok"] is False

    # Every attempt must be logged, per spec section 10
    attempt = fetchone(
        "SELECT * FROM delivery_code_attempts WHERE txn_id=? AND success=0", (order["txn_id"],)
    )
    assert attempt is not None
    assert attempt["reason"] == "Invalid code"


def test_delivery_code_cannot_be_reused(client):
    from app.services.logistics_service import record_quote, confirm_delivery
    from app.models.database import execute
    _setup_provider(client)
    order = _setup_order(client, status="PENDING_PAYMENT")
    record_quote(order["txn_id"], 15000, "Lagos", "Ibadan")
    execute("UPDATE escrow_ledger SET status='ESCROW_LOCKED' WHERE txn_id=?", (order["txn_id"],))
    code = _assign_and_capture_code(client, order)

    fake_recipient = {"ok": True, "recipient_code": "RCP_log123"}
    fake_transfer = {"ok": True, "transfer_code": "TRF-log456", "status": "pending"}
    with patch("app.services.logistics_service.payment_service.create_transfer_recipient",
               return_value=fake_recipient), \
         patch("app.services.logistics_service.payment_service.initiate_transfer",
               return_value=fake_transfer), \
         patch("app.services.logistics_service.send_sms", return_value=True):
        first = confirm_delivery(PROVIDER_PHONE, order["txn_id"], code)
        assert first["ok"] is True
        # Try to claim payment a second time with the same code
        second = confirm_delivery(PROVIDER_PHONE, order["txn_id"], code)

    assert second["ok"] is False


def test_provider_cannot_confirm_someone_elses_job(client):
    from app.services.logistics_service import record_quote, confirm_delivery, register_provider
    from app.models.database import execute
    _setup_provider(client)
    order = _setup_order(client, status="PENDING_PAYMENT")
    record_quote(order["txn_id"], 15000, "Lagos", "Ibadan")
    execute("UPDATE escrow_ledger SET status='ESCROW_LOCKED' WHERE txn_id=?", (order["txn_id"],))
    code = _assign_and_capture_code(client, order)

    with patch("app.services.sms_service.send_sms", return_value=True):
        register_provider("+2348055559999", "Impostor", "Lagos", "Bike", "1234")
    execute("UPDATE logistics_providers SET kyc_status='VERIFIED' WHERE phone=?", ("+2348055559999",))

    result = confirm_delivery("+2348055559999", order["txn_id"], code)
    assert result["ok"] is False
    assert "not assigned to you" in result["error"]


def test_logistics_payout_webhook_confirms_and_increments_jobs(client):
    from app.services.logistics_service import record_quote, confirm_delivery
    from app.models.database import execute, fetchone
    import json, hmac, hashlib
    from config.settings import config

    _setup_provider(client)
    order = _setup_order(client, status="PENDING_PAYMENT")
    record_quote(order["txn_id"], 15000, "Lagos", "Ibadan")
    execute("UPDATE escrow_ledger SET status='ESCROW_LOCKED' WHERE txn_id=?", (order["txn_id"],))
    code = _assign_and_capture_code(client, order)

    fake_recipient = {"ok": True, "recipient_code": "RCP_log123"}
    fake_transfer = {"ok": True, "transfer_code": "TRF-logwebhook", "status": "pending"}
    with patch("app.services.logistics_service.payment_service.create_transfer_recipient",
               return_value=fake_recipient), \
         patch("app.services.logistics_service.payment_service.initiate_transfer",
               return_value=fake_transfer), \
         patch("app.services.logistics_service.send_sms", return_value=True):
        confirm_delivery(PROVIDER_PHONE, order["txn_id"], code)

    payload = json.dumps({
        "event": "transfer.success",
        "data": {"transfer_code": "TRF-logwebhook"}
    }).encode()
    sig = hmac.new(config.PAYSTACK_SECRET_KEY.encode(), payload, hashlib.sha512).hexdigest()

    with patch("app.services.logistics_service.send_sms", return_value=True):
        resp = client.post("/webhooks/paystack", data=payload,
                            headers={"x-paystack-signature": sig,
                                     "Content-Type": "application/json"})
    assert resp.status_code == 200

    log = fetchone("SELECT * FROM logistics_log WHERE txn_id=?", (order["txn_id"],))
    assert log["payout_status"] == "success"

    provider = fetchone("SELECT * FROM logistics_providers WHERE phone=?", (PROVIDER_PHONE,))
    assert provider["completed_jobs"] == 1
