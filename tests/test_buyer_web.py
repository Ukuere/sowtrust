"""
Sowtrust — Buyer Web App Tests.

Follows the same pattern as test_payments.py: a fresh temp SQLite DB per
test run, Paystack calls mocked (no real network/API key needed), one
real end-to-end test still required against Paystack test keys before
this goes live.
"""
import os
import re
import pytest
from unittest.mock import patch

os.environ.setdefault("AT_USERNAME", "sandbox")
os.environ.setdefault("AT_API_KEY", "test_dummy_key")
os.environ.setdefault("PAYSTACK_SECRET_KEY", "sk_test_dummy_key_for_testing")
os.environ.setdefault("FLASK_SECRET_KEY", "test_secret_key_not_for_production")
os.environ.setdefault("DASHBOARD_PASSWORD", "test_admin_password")


@pytest.fixture
def client(tmp_path):
    from config.settings import config
    test_db = str(tmp_path / "test_buyer_web.db")
    os.environ["DATABASE_PATH"] = test_db
    config.DATABASE_PATH = test_db
    os.environ["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    config.UPLOAD_FOLDER = os.environ["UPLOAD_FOLDER"]
    # config is a singleton read once at import time — same reason
    # DATABASE_PATH needs a direct override above rather than just an env
    # var. Your real .env already sets DASHBOARD_PASSWORD, so it's already
    # loaded into `config` by the time this test file's os.environ.setdefault
    # runs — too late to matter. Override the attribute directly instead.
    config.DASHBOARD_USERNAME = "reviewer"
    config.DASHBOARD_PASSWORD = "test_admin_password"

    from migrations.init_db import init_db
    init_db()
    from migrations.add_products_table import migrate as migrate_products
    from migrations.add_payments_columns import migrate as migrate_payments
    from migrations.add_three_sided_fees import migrate as migrate_fees
    from migrations.add_buyer_accounts import migrate as migrate_buyers
    from migrations.add_buyer_kyc import migrate as migrate_kyc
    from migrations.add_kyc_verification_system import migrate as migrate_kyc_system
    from migrations.add_logistics_quotes import migrate as migrate_logistics_quotes
    migrate_products()
    migrate_payments()
    migrate_fees()
    migrate_buyers()
    migrate_kyc()
    migrate_kyc_system()
    migrate_logistics_quotes()

    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    app.config["PHONE_OTP_TEST_BYPASS"] = True
    with app.test_client() as c:
        yield c


def _seed_verified_farmer(phone="+2348033334444", name="Chidi Okafor",
                           crop="Bitter Leaf", price=1500):
    """Directly seed a verified farmer — buyer-side tests don't need to
    drive the USSD registration flow to set one up."""
    from app.models.database import execute
    execute(
        """INSERT INTO farmers
           (phone, name, crop, location, pin_hash, price, kyc_status,
            verification_status, listing_status, is_active)
           VALUES (?, ?, ?, 'Ikorodu', 'x', ?, 'VERIFIED', 'VERIFIED', 'PUBLISHED', 1)""",
        (phone, name, crop, price),
    )


def _seed_verified_provider(phone="+2348055511111"):
    from app.models.database import execute
    from app.services import logistics_service

    with patch("app.services.logistics_service.send_sms", return_value=True):
        result = logistics_service.register_provider(
            phone, "SowTrust Test Logistics", "Lagos", "Van", "1234"
        )
    assert result["ok"] is True
    execute(
        "UPDATE logistics_providers SET kyc_status='VERIFIED' WHERE phone=?",
        (phone,),
    )


def _mark_buyer_verified(phone="+2348011112222"):
    """Test shortcut for the admin-approval outcome, without driving the
    full submit -> admin-approve flow every time a test just needs a
    checkout-eligible buyer."""
    from app.models.database import execute
    execute("UPDATE buyers SET kyc_status = 'VERIFIED' WHERE phone = ?", (phone,))


def _valid_buyer(**overrides):
    """Full spec-section-7 registration payload with sane defaults —
    individual tests override only the field they care about."""
    payload = {
        "name": "Amaka Buyer",
        "phone": "08011112222",
        "password": "secure123",
        "business_name": "Amaka Foods",
        "email": "amaka@example.com",
        "delivery_address": "12 Marina Street",
        "city": "Lagos",
        "state": "Lagos",
        "buyer_type": "Retailer",
    }
    payload.update(overrides)
    return payload


# ── Registration & login ────────────────────────────────────────────────

def test_register_creates_account_and_logs_in(client):
    resp = client.post("/buyer/register", data=_valid_buyer(), follow_redirects=True)
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert sess.get("buyer_phone") == "+2348011112222"


def test_register_rejects_short_password(client):
    resp = client.post("/buyer/register", data=_valid_buyer(password="123"))
    assert resp.status_code == 400
    assert b"8 characters" in resp.data


def test_register_rejects_missing_email(client):
    resp = client.post("/buyer/register", data=_valid_buyer(email=""))
    assert resp.status_code == 400
    assert b"valid email" in resp.data


def test_register_rejects_missing_delivery_address(client):
    resp = client.post("/buyer/register", data=_valid_buyer(delivery_address=""))
    assert resp.status_code == 400
    assert b"delivery address" in resp.data


def test_register_rejects_invalid_buyer_type(client):
    resp = client.post("/buyer/register", data=_valid_buyer(buyer_type="Not A Real Type"))
    assert resp.status_code == 400
    assert b"buyer type" in resp.data


def test_register_rejects_duplicate_phone(client):
    client.post("/buyer/register", data=_valid_buyer())
    client.get("/buyer/logout")
    resp = client.post("/buyer/register", data=_valid_buyer(
        name="Someone Else", email="someone@example.com"))
    assert resp.status_code == 400
    assert b"already exists" in resp.data


def test_register_rejects_duplicate_email(client):
    client.post("/buyer/register", data=_valid_buyer())
    client.get("/buyer/logout")
    resp = client.post("/buyer/register", data=_valid_buyer(
        phone="08099998888", name="Someone Else"))
    assert resp.status_code == 400
    assert b"already registered" in resp.data


def test_login_wrong_password_rejected(client):
    client.post("/buyer/register", data=_valid_buyer())
    client.get("/buyer/logout")
    resp = client.post("/buyer/login", data={"phone": "08011112222", "password": "wrongpass"})
    assert resp.status_code == 400
    assert b"Incorrect password" in resp.data


def test_login_succeeds_with_correct_password(client):
    client.post("/buyer/register", data=_valid_buyer())
    client.get("/buyer/logout")
    resp = client.post("/buyer/login", data={
        "phone": "08011112222", "password": "secure123",
    }, follow_redirects=True)
    assert resp.status_code == 200
    with client.session_transaction() as sess:
        assert sess.get("buyer_phone") == "+2348011112222"


# ── Email verification ───────────────────────────────────────────────────

def test_registration_sets_unverified_email(client):
    client.post("/buyer/register", data=_valid_buyer())
    from app.services.buyer_service import get_buyer
    buyer = get_buyer("+2348011112222")
    assert buyer["email_verified"] == 0
    assert buyer["email_verification_token"]


def test_verify_email_with_valid_token_marks_verified(client, capsys):
    client.post("/buyer/register", data=_valid_buyer())
    printed = capsys.readouterr().out
    match = re.search(r"/buyer/verify-email/(\S+)", printed)
    assert match, "verification link should have been logged by the email stub"
    token = match.group(1)

    resp = client.get(f"/buyer/verify-email/{token}", follow_redirects=True)
    assert resp.status_code == 200

    from app.services.buyer_service import get_buyer
    buyer = get_buyer("+2348011112222")
    assert buyer["email_verified"] == 1
    assert buyer["email_verification_token"] is None


def test_verify_email_rejects_invalid_token(client):
    resp = client.get("/buyer/verify-email/not-a-real-token", follow_redirects=True)
    assert b"Invalid or already-used" in resp.data


def test_verify_email_token_is_single_use(client):
    client.post("/buyer/register", data=_valid_buyer())
    from app.services.buyer_service import get_buyer
    token = get_buyer("+2348011112222")["email_verification_token"]

    client.get(f"/buyer/verify-email/{token}")
    resp = client.get(f"/buyer/verify-email/{token}", follow_redirects=True)
    assert b"Invalid or already-used" in resp.data


# ── Access control ───────────────────────────────────────────────────────

def test_browse_requires_login(client):
    resp = client.get("/buyer/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/buyer/login" in resp.headers["Location"]


def test_orders_requires_login(client):
    resp = client.get("/buyer/orders", follow_redirects=False)
    assert resp.status_code == 302


# ── Browsing ─────────────────────────────────────────────────────────────

def test_browse_lists_verified_products_only(client):
    _seed_verified_farmer()
    client.post("/buyer/register", data=_valid_buyer())
    resp = client.get("/buyer/")
    assert resp.status_code == 200
    assert b"Bitter Leaf" in resp.data


def test_product_detail_shows_verified_farmer(client):
    _seed_verified_farmer()
    client.post("/buyer/register", data=_valid_buyer())
    resp = client.get("/buyer/product/Bitter Leaf")
    assert resp.status_code == 200
    assert b"Chidi Okafor" in resp.data
    assert b"1,500" in resp.data


# ── Checkout & fee breakdown ─────────────────────────────────────────────

def test_checkout_preview_matches_fee_service(client):
    """The web checkout preview must use the same fee math as escrow_service
    — this is the assertion that would catch a buyer being shown one total
    and charged another."""
    _seed_verified_farmer(price=1000)
    client.post("/buyer/register", data=_valid_buyer())
    _mark_buyer_verified()

    from app.services import fee_service
    expected = fee_service.calculate_full_order(3000, 0.0)  # 3 bags @ 1000

    resp = client.get("/buyer/checkout/+2348033334444/Bitter Leaf?quantity=3")
    assert resp.status_code == 200
    assert f"{expected['buyer_total']:,.2f}".encode() in resp.data


def test_checkout_post_creates_order_and_redirects(client):
    _seed_verified_farmer(price=1000)
    client.post("/buyer/register", data=_valid_buyer())
    _mark_buyer_verified()

    with patch("app.services.payment_service.initiate_bank_transfer_charge") as paystack:
        resp = client.post(
            "/buyer/checkout/+2348033334444/Bitter Leaf",
            data={"quantity": 2},
            follow_redirects=True,
        )
    assert resp.status_code == 200
    assert b"SowTrust Operations" in resp.data
    paystack.assert_not_called()

    from app.models.database import fetchone
    row = fetchone(
        "SELECT * FROM escrow_ledger WHERE buyer_phone = ?", ("+2348011112222",)
    )
    assert row is not None
    assert row["status"] == "QUOTE_PENDING"
    assert row["quantity_bags"] == 2
    assert row["payment_reference"] is None

    quote = fetchone("SELECT * FROM logistics_quotes WHERE order_id = ?", (row["txn_id"],))
    assert quote is not None
    assert quote["status"] == "PENDING"


def test_checkout_snapshots_delivery_info_onto_order(client):
    """Spec section 7 — delivery info must be captured on the order itself,
    not just looked up live from the buyer's (possibly later-edited)
    profile."""
    _seed_verified_farmer(price=1000)
    client.post("/buyer/register", data=_valid_buyer(
        delivery_address="12 Marina Street", city="Lagos", state="Lagos"))
    _mark_buyer_verified()

    fake_charge = {"ok": True, "account_number": "9990001111", "bank_name": "Test Bank"}
    with patch("app.services.payment_service.initiate_bank_transfer_charge",
               return_value=fake_charge):
        client.post("/buyer/checkout/+2348033334444/Bitter Leaf", data={"quantity": 1})

    from app.models.database import fetchone
    row = fetchone("SELECT * FROM escrow_ledger WHERE buyer_phone = ?", ("+2348011112222",))
    assert row["buyer_name"] == "Amaka Buyer"
    assert row["delivery_address"] == "12 Marina Street"
    assert row["delivery_city"] == "Lagos"
    assert row["delivery_state"] == "Lagos"


def test_buyer_accepts_locked_quote_before_payment_initialization(client):
    _seed_verified_farmer(price=100000)
    _seed_verified_provider()
    client.post("/buyer/register", data=_valid_buyer())
    _mark_buyer_verified()

    with patch("app.services.payment_service.initiate_bank_transfer_charge") as paystack:
        client.post("/buyer/checkout/+2348033334444/Bitter Leaf", data={"quantity": 1})
    paystack.assert_not_called()

    from app.models.database import fetchone
    from app.services import logistics_service
    row = fetchone("SELECT * FROM escrow_ledger WHERE buyer_phone = ?", ("+2348011112222",))
    quote = logistics_service.record_quote(
        row["txn_id"], 17500, "Ikorodu", "12 Marina Street, Lagos",
        logistics_provider_id="+2348055511111",
    )
    assert quote["ok"], quote.get("error")

    fake_charge = {"ok": True, "account_number": "9990001111", "bank_name": "Paystack-Titan"}
    with patch("app.services.payment_service.initiate_bank_transfer_charge",
               return_value=fake_charge) as paystack:
        resp = client.post(f"/buyer/orders/{row['txn_id']}/accept-quote", follow_redirects=True)

    assert resp.status_code == 200
    assert b"9990001111" in resp.data
    paystack.assert_called_once()

    updated = fetchone("SELECT * FROM escrow_ledger WHERE txn_id = ?", (row["txn_id"],))
    assert updated["status"] == "PAYMENT_INITIALIZED"
    assert updated["buyer_total"] == 120000
    assert updated["payment_reference"] is not None

    locked_quote = fetchone("SELECT * FROM logistics_quotes WHERE order_id = ?", (row["txn_id"],))
    assert locked_quote["buyer_accepted_at"] is not None
    assert locked_quote["commission_amount"] == 437.5
    assert locked_quote["provider_net_amount"] == 17062.5


def test_orders_page_only_shows_own_orders(client):
    _seed_verified_farmer(price=1000)
    fake_charge = {"ok": True, "account_number": "9990001111", "bank_name": "Test Bank"}

    client.post("/buyer/register", data=_valid_buyer())
    _mark_buyer_verified()
    with patch("app.services.payment_service.initiate_bank_transfer_charge",
               return_value=fake_charge):
        client.post("/buyer/checkout/+2348033334444/Bitter Leaf", data={"quantity": 1})
    client.get("/buyer/logout")

    client.post("/buyer/register", data=_valid_buyer(
        phone="08022223333", name="Tunde", email="tunde@example.com"))
    resp = client.get("/buyer/orders")
    assert resp.status_code == 200
    assert b"Bitter Leaf" not in resp.data  # Tunde has no orders of his own


def test_cannot_view_another_buyers_order(client):
    _seed_verified_farmer(price=1000)
    fake_charge = {"ok": True, "account_number": "9990001111", "bank_name": "Test Bank"}

    client.post("/buyer/register", data=_valid_buyer())
    _mark_buyer_verified()
    with patch("app.services.payment_service.initiate_bank_transfer_charge",
               return_value=fake_charge):
        client.post("/buyer/checkout/+2348033334444/Bitter Leaf", data={"quantity": 1})

    from app.models.database import fetchone
    txn_id = fetchone(
        "SELECT txn_id FROM escrow_ledger WHERE buyer_phone = ?", ("+2348011112222",)
    )["txn_id"]

    client.get("/buyer/logout")
    client.post("/buyer/register", data=_valid_buyer(
        phone="08022223333", name="Tunde", email="tunde@example.com"))
    resp = client.get(f"/buyer/orders/{txn_id}", follow_redirects=True)
    assert b"Order not found" in resp.data


# ── KYC checkout gate (spec sections 1, 6, 7) ─────────────────────────────

def test_new_buyer_starts_profile_completed_not_verified(client):
    client.post("/buyer/register", data=_valid_buyer())
    from app.services.buyer_service import get_buyer
    buyer = get_buyer("+2348011112222")
    assert buyer["kyc_status"] == "PROFILE_COMPLETED"


def test_unverified_buyer_can_browse(client):
    _seed_verified_farmer()
    client.post("/buyer/register", data=_valid_buyer())
    resp = client.get("/buyer/")
    assert resp.status_code == 200
    assert b"Bitter Leaf" in resp.data


def test_unverified_buyer_blocked_from_checkout_get(client):
    _seed_verified_farmer(price=1000)
    client.post("/buyer/register", data=_valid_buyer())
    resp = client.get("/buyer/checkout/+2348033334444/Bitter Leaf", follow_redirects=True)
    assert b"Complete identity verification" in resp.data
    assert b"Confirm your order" not in resp.data  # never reached the checkout page itself


def test_unverified_buyer_blocked_from_checkout_post(client):
    """The hard requirement — even if a POST is crafted directly against
    the checkout endpoint, no order should be created for an unverified
    buyer. This is the check that actually matters; the GET redirect is
    just UX."""
    _seed_verified_farmer(price=1000)
    client.post("/buyer/register", data=_valid_buyer())

    resp = client.post(
        "/buyer/checkout/+2348033334444/Bitter Leaf",
        data={"quantity": 1}, follow_redirects=True,
    )
    assert b"verification" in resp.data.lower()

    from app.models.database import fetchone
    row = fetchone("SELECT * FROM escrow_ledger WHERE buyer_phone = ?", ("+2348011112222",))
    assert row is None  # no order was created


def test_verified_buyer_can_checkout(client):
    _seed_verified_farmer(price=1000)
    client.post("/buyer/register", data=_valid_buyer())
    _mark_buyer_verified()
    resp = client.get("/buyer/checkout/+2348033334444/Bitter Leaf")
    assert resp.status_code == 200
    assert b"Confirm your order" in resp.data


# ── KYC submission (spec section 2) ───────────────────────────────────────

def _fake_pdf(name="id.pdf"):
    import io
    return (io.BytesIO(b"%PDF-1.4 fake content for testing"), name)


def test_individual_buyer_kyc_submission(client):
    client.post("/buyer/register", data=_valid_buyer(buyer_type="Individual"))
    resp = client.post(
        "/buyer/kyc",
        data={
            "id_type": "National ID (NIN)",
            "id_number": "12345678901",
            "id_document": _fake_pdf(),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    from app.services.buyer_service import get_buyer
    buyer = get_buyer("+2348011112222")
    assert buyer["kyc_status"] == "KYC_PENDING"
    assert buyer["id_number"] == "12345678901"
    assert buyer["id_document_path"]

    from app.models.database import fetchone
    record = fetchone(
        "SELECT * FROM kyc_verifications WHERE user_id = ? AND user_type = 'buyer'",
        ("+2348011112222",),
    )
    assert record["status"] == "PENDING"
    assert record["verification_type"] == "identity"


def test_business_buyer_kyc_requires_cac_fields(client):
    client.post("/buyer/register", data=_valid_buyer(buyer_type="Retailer"))
    resp = client.post(
        "/buyer/kyc",
        data={"id_type": "National ID (NIN)", "id_number": "12345678901", "id_document": _fake_pdf()},
        content_type="multipart/form-data",
    )
    # missing CAC fields entirely -> submit_kyc rejects, template re-renders with error
    assert b"CAC registration number" in resp.data


def test_business_buyer_kyc_submission_with_cac(client):
    client.post("/buyer/register", data=_valid_buyer(buyer_type="Retailer"))
    resp = client.post(
        "/buyer/kyc",
        data={
            "id_type": "National ID (NIN)",
            "id_number": "12345678901",
            "id_document": _fake_pdf("id.pdf"),
            "business_reg_number": "RC1234567",
            "business_reg_document": _fake_pdf("cac.pdf"),
            "authorized_rep_name": "Amaka Buyer",
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200

    from app.services.buyer_service import get_buyer
    buyer = get_buyer("+2348011112222")
    assert buyer["kyc_status"] == "KYC_PENDING"
    assert buyer["business_reg_number"] == "RC1234567"

    record = None
    from app.models.database import fetchone
    record = fetchone(
        "SELECT * FROM kyc_verifications WHERE user_id = ? AND user_type = 'buyer'",
        ("+2348011112222",),
    )
    assert record["verification_type"] == "business"


def test_kyc_submission_rejects_missing_document(client):
    client.post("/buyer/register", data=_valid_buyer(buyer_type="Individual"))
    resp = client.post(
        "/buyer/kyc",
        data={"id_type": "National ID (NIN)", "id_number": "12345678901"},
        content_type="multipart/form-data",
    )
    assert b"Upload a verification document" in resp.data


def test_cannot_resubmit_kyc_while_pending(client):
    client.post("/buyer/register", data=_valid_buyer(buyer_type="Individual"))
    client.post("/buyer/kyc", data={
        "id_type": "National ID (NIN)", "id_number": "11111111", "id_document": _fake_pdf(),
    }, content_type="multipart/form-data")

    resp = client.post("/buyer/kyc", data={
        "id_type": "National ID (NIN)", "id_number": "22222222", "id_document": _fake_pdf(),
    }, content_type="multipart/form-data")
    assert b"already under review" in resp.data


# ── Admin KYC review (spec sections 2, 3, 7) ──────────────────────────────

def _admin_login(client, password="test_admin_password"):
    return client.post(
        "/staff/login",
        data={"username": "reviewer", "password": password},
        follow_redirects=False,
    )


def test_admin_queue_requires_auth(client):
    resp = client.get("/admin/kyc/")
    assert resp.status_code == 302
    assert "/staff/login" in resp.headers["Location"]


def test_admin_queue_rejects_wrong_password(client):
    resp = _admin_login(client, "wrong_password")
    assert resp.status_code == 401


def test_admin_queue_shows_pending_submission(client):
    client.post("/buyer/register", data=_valid_buyer(buyer_type="Individual"))
    client.post("/buyer/kyc", data={
        "id_type": "National ID (NIN)", "id_number": "12345678901", "id_document": _fake_pdf(),
    }, content_type="multipart/form-data")

    assert _admin_login(client).status_code == 302
    resp = client.get("/admin/kyc/")
    assert resp.status_code == 200
    assert b"Amaka Buyer" in resp.data
    assert b"12345678901" in resp.data


def test_admin_approve_unlocks_checkout(client):
    _seed_verified_farmer(price=1000)
    client.post("/buyer/register", data=_valid_buyer(buyer_type="Individual"))
    client.post("/buyer/kyc", data={
        "id_type": "National ID (NIN)", "id_number": "12345678901", "id_document": _fake_pdf(),
    }, content_type="multipart/form-data")

    from app.models.database import fetchone
    record = fetchone(
        "SELECT * FROM kyc_verifications WHERE user_id = ? AND user_type = 'buyer'",
        ("+2348011112222",),
    )
    assert _admin_login(client).status_code == 302
    resp = client.post(
        f"/admin/kyc/{record['id']}/decide",
        data={"decision": "VERIFIED"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    from app.services.buyer_service import get_buyer
    assert get_buyer("+2348011112222")["kyc_status"] == "VERIFIED"

    client.post(
        "/buyer/login",
        data={"phone": "08011112222", "password": "secure123"},
    )
    checkout_resp = client.get("/buyer/checkout/+2348033334444/Bitter Leaf")
    assert checkout_resp.status_code == 200
    assert b"Confirm your order" in checkout_resp.data


def test_admin_reject_requires_reason_and_keeps_buyer_blocked(client):
    client.post("/buyer/register", data=_valid_buyer(buyer_type="Individual"))
    client.post("/buyer/kyc", data={
        "id_type": "National ID (NIN)", "id_number": "12345678901", "id_document": _fake_pdf(),
    }, content_type="multipart/form-data")

    from app.models.database import fetchone
    record = fetchone(
        "SELECT * FROM kyc_verifications WHERE user_id = ? AND user_type = 'buyer'",
        ("+2348011112222",),
    )
    assert _admin_login(client).status_code == 302
    resp = client.post(
        f"/admin/kyc/{record['id']}/decide",
        data={"decision": "REJECTED", "rejection_reason": "Document unreadable"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    from app.services.buyer_service import get_buyer
    buyer = get_buyer("+2348011112222")
    assert buyer["kyc_status"] == "REJECTED"
    assert buyer["kyc_rejection_reason"] == "Document unreadable"

    client.post(
        "/buyer/login",
        data={"phone": "08011112222", "password": "secure123"},
    )
    checkout_resp = client.get(
        "/buyer/checkout/+2348033334444/Bitter Leaf", follow_redirects=True
    )
    assert b"verification" in checkout_resp.data.lower()


def test_cannot_review_same_record_twice(client):
    client.post("/buyer/register", data=_valid_buyer(buyer_type="Individual"))
    client.post("/buyer/kyc", data={
        "id_type": "National ID (NIN)", "id_number": "12345678901", "id_document": _fake_pdf(),
    }, content_type="multipart/form-data")

    from app.models.database import fetchone
    record = fetchone(
        "SELECT * FROM kyc_verifications WHERE user_id = ? AND user_type = 'buyer'",
        ("+2348011112222",),
    )
    assert _admin_login(client).status_code == 302
    client.post(f"/admin/kyc/{record['id']}/decide", data={"decision": "VERIFIED"},
                )
    resp = client.post(f"/admin/kyc/{record['id']}/decide", data={"decision": "REJECTED",
                        "rejection_reason": "too late"},
                        follow_redirects=True)
    assert b"already been reviewed" in resp.data
