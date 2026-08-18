"""
Sowtrust — Logistics Provider Web Dashboard Tests.

Same pattern as test_buyer_web.py: fresh temp SQLite DB per test,
Paystack calls mocked, config singleton overridden directly (not via
os.environ, since config is read once at import time).
"""
import os
import pytest
from unittest.mock import patch

os.environ.setdefault("AT_USERNAME", "sandbox")
os.environ.setdefault("AT_API_KEY", "test_dummy_key")
os.environ.setdefault("PAYSTACK_SECRET_KEY", "sk_test_dummy_key_for_testing")
os.environ.setdefault("FLASK_SECRET_KEY", "test_secret_key_not_for_production")


@pytest.fixture
def client(tmp_path):
    from config.settings import config
    test_db = str(tmp_path / "test_logistics_web.db")
    os.environ["DATABASE_PATH"] = test_db
    config.DATABASE_PATH = test_db
    os.environ["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    config.UPLOAD_FOLDER = os.environ["UPLOAD_FOLDER"]
    config.DASHBOARD_PASSWORD = "test_admin_password"

    from migrations.init_db import init_db
    init_db()
    from migrations.add_products_table import migrate as migrate_products
    from migrations.add_payments_columns import migrate as migrate_payments
    from migrations.add_three_sided_fees import migrate as migrate_fees
    from migrations.add_logistics_providers import migrate as migrate_logistics
    from migrations.add_buyer_accounts import migrate as migrate_buyers
    from migrations.add_buyer_kyc import migrate as migrate_buyer_kyc
    from migrations.add_kyc_verification_system import migrate as migrate_kyc_system
    from migrations.add_logistics_kyc import migrate as migrate_logistics_kyc
    from migrations.add_logistics_quotes import migrate as migrate_logistics_quotes
    migrate_products()
    migrate_payments()
    migrate_fees()
    migrate_logistics()
    migrate_buyers()
    migrate_buyer_kyc()
    migrate_kyc_system()
    migrate_logistics_kyc()
    migrate_logistics_quotes()

    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _register_provider(client, phone="08033335555", name="Tunde Driver",
                        operating_area="Lagos", vehicle_type="Van", pin="1234"):
    return client.post("/logistics/register", data={
        "name": name, "phone": phone, "operating_area": operating_area,
        "vehicle_type": vehicle_type, "pin": pin,
    })


def _fake_doc(name="doc.pdf"):
    import io
    return (io.BytesIO(b"%PDF-1.4 fake content"), name)


def _submit_kyc(client, id_number="12345678901"):
    return client.post("/logistics/kyc", data={
        "id_type": "National ID (NIN)",
        "id_number": id_number,
        "id_document": _fake_doc("id.pdf"),
        "vehicle_registration_document": _fake_doc("vehicle.pdf"),
    }, content_type="multipart/form-data")


def _admin_session(client):
    with client.session_transaction() as session:
        session["staff_user_id"] = 1
        session["staff_username"] = "reviewer"
        session["staff_role"] = "REVIEWER"


def _verify_provider_full(client, phone="+2348033335555"):
    """Shortcut: submit KYC, admin-approve it, then verify+set a bank
    account directly (bypassing Paystack resolution) — for tests that
    just need a fully-onboarded provider to test job flows."""
    _submit_kyc(client)
    from app.models.database import fetchone, execute
    record = fetchone(
        "SELECT * FROM kyc_verifications WHERE user_id = ? AND user_type = 'logistics_provider'",
        (phone,),
    )
    _admin_session(client)
    client.post(f"/admin/logistics/{record['id']}/decide", data={"decision": "VERIFIED"})
    execute(
        """UPDATE logistics_providers SET bank_code='058', bank_account_number='0123456789',
           bank_account_name='Tunde Driver', bank_verified_at=datetime('now') WHERE phone=?""",
        (phone,),
    )


def _seed_paid_order_with_logistics(txn_id_holder, price=1000, quantity=2,
                                    quote_provider_phone="+2348033335555"):
    """Create a quote-first order, then simulate confirmed escrow funds."""
    from app.models.database import execute, fetchone
    execute(
        """INSERT INTO farmers (phone, name, crop, location, pin_hash, price, kyc_status, is_active)
           VALUES ('+2348044445555', 'Chidi Okafor', 'Yam', 'Ikorodu', 'x', ?, 'VERIFIED', 1)""",
        (price,),
    )
    from app.services import escrow_service, logistics_service
    result = escrow_service.create_order_awaiting_quote(
        buyer_phone="+2348011119999", farmer_phone="+2348044445555",
        crop="Yam", quantity_bags=quantity, product_amount=price * quantity,
        delivery_address="1 Test Street", delivery_city="Lekki", delivery_state="Lagos",
    )
    txn_id = result["txn_id"]
    txn_id_holder["txn_id"] = txn_id
    logistics_service.create_quote_request(txn_id, "Ikorodu", "Lekki", requested_by="test")

    quote_result = logistics_service.record_quote(
        txn_id, 5000, "Ikorodu", "Lekki",
        logistics_provider_id=quote_provider_phone,
    )
    assert quote_result["ok"], quote_result.get("error")

    execute("UPDATE escrow_ledger SET status = 'ESCROW_LOCKED' WHERE txn_id = ?", (txn_id,))
    return txn_id


# ── Registration & login ────────────────────────────────────────────────

def test_register_creates_account_and_logs_in(client):
    resp = _register_provider(client)
    assert resp.status_code in (200, 302)
    with client.session_transaction() as sess:
        assert sess.get("provider_phone") == "+2348033335555"


def test_register_rejects_invalid_phone(client):
    resp = _register_provider(client, phone="123")
    assert resp.status_code == 400
    assert b"valid phone number" in resp.data


def test_register_rejects_duplicate_phone(client):
    _register_provider(client)
    client.get("/logistics/logout")
    resp = _register_provider(client, name="Someone Else")
    assert resp.status_code == 400


def test_login_wrong_pin_rejected(client):
    _register_provider(client)
    client.get("/logistics/logout")
    resp = client.post("/logistics/login", data={"phone": "08033335555", "pin": "9999"})
    assert resp.status_code == 400
    assert b"Incorrect" in resp.data


def test_login_correct_pin_succeeds(client):
    _register_provider(client)
    client.get("/logistics/logout")
    resp = client.post("/logistics/login", data={"phone": "08033335555", "pin": "1234"},
                        follow_redirects=True)
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert sess.get("provider_phone") == "+2348033335555"


# ── Access control ───────────────────────────────────────────────────────

def test_dashboard_requires_login(client):
    resp = client.get("/logistics/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/logistics/login" in resp.headers["Location"]


def test_jobs_requires_login(client):
    resp = client.get("/logistics/jobs", follow_redirects=False)
    assert resp.status_code == 302


# ── KYC gate on job assignment (already enforced in logistics_service) ───

def test_unverified_provider_sees_no_jobs_on_dashboard(client):
    _register_provider(client)
    resp = client.get("/logistics/")
    assert resp.status_code == 200
    assert b"PENDING" in resp.data


def test_unverified_provider_cannot_accept_job(client):
    """assign_provider() itself blocks this — this test confirms the web
    route surfaces that error rather than silently succeeding."""
    _register_provider(client)
    from app.models.database import execute
    from app.services import logistics_service
    with patch("app.services.logistics_service.send_sms", return_value=True):
        result = logistics_service.register_provider(
            "+2348033377777", "Verified Quote Provider", "Lagos", "Van", "1234"
        )
    assert result["ok"] is True
    execute(
        "UPDATE logistics_providers SET kyc_status='VERIFIED' WHERE phone=?",
        ("+2348033377777",),
    )
    holder = {}
    _seed_paid_order_with_logistics(
        holder, quote_provider_phone="+2348033377777"
    )
    resp = client.post(f"/logistics/jobs/{holder['txn_id']}/accept", follow_redirects=True)
    assert b"not yet verified" in resp.data


def test_verified_provider_without_bank_account_cannot_accept_job(client):
    _register_provider(client)
    _submit_kyc(client)
    from app.models.database import fetchone, execute
    record = fetchone(
        "SELECT * FROM kyc_verifications WHERE user_id = ? AND user_type = 'logistics_provider'",
        ("+2348033335555",),
    )
    _admin_session(client)
    client.post(f"/admin/logistics/{record['id']}/decide", data={"decision": "VERIFIED"})

    holder = {}
    _seed_paid_order_with_logistics(holder)
    resp = client.post(f"/logistics/jobs/{holder['txn_id']}/accept", follow_redirects=True)
    assert b"payout account" in resp.data


# ── KYC submission ─────────────────────────────────────────────────────

def test_kyc_submission_moves_to_under_review(client):
    _register_provider(client)
    _submit_kyc(client)
    from app.services.logistics_service import get_provider
    provider = get_provider("+2348033335555")
    assert provider["kyc_status"] == "UNDER_REVIEW"


def test_kyc_submission_requires_vehicle_document(client):
    _register_provider(client)
    resp = client.post("/logistics/kyc", data={
        "id_type": "National ID (NIN)", "id_number": "12345678901",
        "id_document": _fake_doc("id.pdf"),
    }, content_type="multipart/form-data")
    assert b"vehicle registration" in resp.data.lower()


def test_cannot_resubmit_while_under_review(client):
    _register_provider(client)
    _submit_kyc(client, id_number="11111111")
    resp = _submit_kyc(client, id_number="22222222")
    assert b"already under review" in resp.data


# ── Admin review ──────────────────────────────────────────────────────────

def test_admin_logistics_queue_requires_auth(client):
    resp = client.get("/admin/logistics/")
    assert resp.status_code == 302
    assert "/staff/login" in resp.headers["Location"]


def test_admin_logistics_queue_shows_submission(client):
    _register_provider(client)
    _submit_kyc(client)
    _admin_session(client)
    resp = client.get("/admin/logistics/")
    assert resp.status_code == 200
    assert b"Tunde Driver" in resp.data


def test_admin_approve_verifies_provider(client):
    _register_provider(client)
    _submit_kyc(client)
    from app.models.database import fetchone
    record = fetchone(
        "SELECT * FROM kyc_verifications WHERE user_id = ? AND user_type = 'logistics_provider'",
        ("+2348033335555",),
    )
    _admin_session(client)
    client.post(f"/admin/logistics/{record['id']}/decide", data={"decision": "VERIFIED"})
    from app.services.logistics_service import get_provider
    assert get_provider("+2348033335555")["kyc_status"] == "VERIFIED"


def test_admin_reject_requires_reason(client):
    _register_provider(client)
    _submit_kyc(client)
    from app.models.database import fetchone
    record = fetchone(
        "SELECT * FROM kyc_verifications WHERE user_id = ? AND user_type = 'logistics_provider'",
        ("+2348033335555",),
    )
    _admin_session(client)
    resp = client.post(f"/admin/logistics/{record['id']}/decide", data={"decision": "REJECTED"},
                        follow_redirects=True)
    assert b"reason is required" in resp.data


# ── Full job lifecycle ──────────────────────────────────────────────────

def test_verified_provider_can_accept_job(client):
    _register_provider(client)
    _verify_provider_full(client)
    holder = {}
    _seed_paid_order_with_logistics(holder)

    resp = client.post(f"/logistics/jobs/{holder['txn_id']}/accept", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Job accepted" in resp.data

    from app.models.database import fetchone
    log = fetchone("SELECT * FROM logistics_log WHERE txn_id = ?", (holder["txn_id"],))
    assert log["status"] == "ASSIGNED"


def test_job_disappears_from_available_list_once_accepted(client):
    _register_provider(client)
    _verify_provider_full(client)
    holder = {}
    _seed_paid_order_with_logistics(holder)

    client.post(f"/logistics/jobs/{holder['txn_id']}/accept")
    resp = client.get("/logistics/")
    assert holder["txn_id"].encode() not in resp.data


def test_confirm_delivery_with_correct_code_triggers_payout(client):
    _register_provider(client)
    _verify_provider_full(client)
    holder = {}
    _seed_paid_order_with_logistics(holder)
    txn_id = holder["txn_id"]

    # generate_release_code() is used for both the delivery code (here)
    # and the escrow release code — it's normally random and hashed
    # before storage, so patch it to a known value to test the success
    # path end-to-end rather than only the "wrong code" rejection.
    with patch("app.services.logistics_service.generate_release_code", return_value="ABC123"), \
         patch("app.services.sms_service.send_sms"):
        accept_resp = client.post(f"/logistics/jobs/{txn_id}/accept")
        assert accept_resp.status_code in (200, 302)

    from app.models.database import fetchone
    log = fetchone("SELECT * FROM logistics_log WHERE txn_id = ?", (txn_id,))
    assert log["status"] == "ASSIGNED"

    # Wrong code first — must fail cleanly and be logged.
    wrong_resp = client.post(f"/logistics/jobs/{txn_id}/confirm-delivery",
                              data={"delivery_code": "WRONGCODE"}, follow_redirects=True)
    assert b"Invalid delivery code" in wrong_resp.data

    from app.models.database import fetchall
    attempts = fetchall("SELECT * FROM delivery_code_attempts WHERE txn_id = ?", (txn_id,))
    assert len(attempts) == 1
    assert attempts[0]["success"] == 0

    # Correct code — should trigger a real (mocked) Paystack transfer and
    # move the job to DELIVERED.
    fake_recipient = {"ok": True, "recipient_code": "RCP_test123"}
    fake_transfer = {"ok": True, "transfer_code": "TRF_test456"}
    with patch("app.services.payment_service.create_transfer_recipient",
               return_value=fake_recipient), \
         patch("app.services.payment_service.initiate_transfer",
               return_value=fake_transfer), \
         patch("app.services.sms_service.send_sms"):
        success_resp = client.post(f"/logistics/jobs/{txn_id}/confirm-delivery",
                                    data={"delivery_code": "ABC123"}, follow_redirects=True)

    assert b"payout initiated" in success_resp.data

    log = fetchone("SELECT * FROM logistics_log WHERE txn_id = ?", (txn_id,))
    assert log["status"] == "DELIVERED"
    assert log["payout_reference"] == "TRF_test456"

    attempts = fetchall("SELECT * FROM delivery_code_attempts WHERE txn_id = ?", (txn_id,))
    assert len(attempts) == 2
    assert attempts[1]["success"] == 1


def test_cannot_reuse_delivery_code_after_confirmation(client):
    _register_provider(client)
    _verify_provider_full(client)
    holder = {}
    _seed_paid_order_with_logistics(holder)
    txn_id = holder["txn_id"]

    with patch("app.services.logistics_service.generate_release_code", return_value="ABC123"), \
         patch("app.services.sms_service.send_sms"):
        client.post(f"/logistics/jobs/{txn_id}/accept")

    fake_recipient = {"ok": True, "recipient_code": "RCP_test123"}
    fake_transfer = {"ok": True, "transfer_code": "TRF_test456"}
    with patch("app.services.payment_service.create_transfer_recipient", return_value=fake_recipient), \
         patch("app.services.payment_service.initiate_transfer", return_value=fake_transfer), \
         patch("app.services.sms_service.send_sms"):
        client.post(f"/logistics/jobs/{txn_id}/confirm-delivery", data={"delivery_code": "ABC123"})

    resp = client.post(f"/logistics/jobs/{txn_id}/confirm-delivery",
                        data={"delivery_code": "ABC123"}, follow_redirects=True)
    assert b"already confirmed" in resp.data


def test_cannot_accept_already_assigned_job(client):
    _register_provider(client, phone="08033335555")
    _verify_provider_full(client, phone="+2348033335555")
    holder = {}
    _seed_paid_order_with_logistics(holder)
    client.post(f"/logistics/jobs/{holder['txn_id']}/accept")

    client.get("/logistics/logout")
    _register_provider(client, phone="08033336666", name="Second Driver")
    _verify_provider_full(client, phone="+2348033336666")

    resp = client.post(f"/logistics/jobs/{holder['txn_id']}/accept", follow_redirects=True)
    assert b"already assigned" in resp.data
