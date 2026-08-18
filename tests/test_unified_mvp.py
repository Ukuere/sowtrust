"""Acceptance tests for the unified production MVP architecture."""
import io
from unittest.mock import patch

import pytest
from werkzeug.datastructures import FileStorage


@pytest.fixture
def mvp(tmp_path):
    from config.settings import config

    original = {
        "DATABASE_PATH": config.DATABASE_PATH,
        "UPLOAD_FOLDER": config.UPLOAD_FOLDER,
        "ENV": config.ENV,
        "CANONICAL_HOST": config.CANONICAL_HOST,
        "DASHBOARD_API_TOKEN": config.DASHBOARD_API_TOKEN,
        "DASHBOARD_USERNAME": config.DASHBOARD_USERNAME,
        "DASHBOARD_PASSWORD": config.DASHBOARD_PASSWORD,
        "STORAGE_BACKEND": config.STORAGE_BACKEND,
    }
    config.DATABASE_PATH = str(tmp_path / "unified.db")
    config.UPLOAD_FOLDER = str(tmp_path / "uploads")
    config.ENV = "testing"
    config.CANONICAL_HOST = ""
    config.DASHBOARD_API_TOKEN = "dashboard-test-token"
    config.DASHBOARD_USERNAME = "ops-admin"
    config.DASHBOARD_PASSWORD = "correct-horse-battery-staple"
    config.STORAGE_BACKEND = "local"

    from migrations.init_db import init_db
    init_db()
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield app, client

    for name, value in original.items():
        setattr(config, name, value)


def _ussd(client, text, phone):
    return client.post(
        "/ussd",
        data={
            "sessionId": f"session-{phone[-4:]}",
            "serviceCode": "*709#",
            "phoneNumber": phone,
            "text": text,
        },
    )


def test_cross_channel_registration_and_otp_activation(mvp):
    _, client = mvp
    from app.models.database import fetchall, fetchone
    from app.services import identity_service

    with patch("app.routes.ussd.send_sms", return_value=True), patch(
        "app.services.sms_service.send_sms", return_value=True
    ):
        farmer = _ussd(client, "1*1*Ada Farmer*Maize*Kaduna*1234*1234", "+2348010000001")
        agent = _ussd(client, "6*1*Bola Agent*Lagos*4321", "+2348020000002")
        provider = _ussd(
            client,
            "3*2*Chika Transport*Lagos Ibadan*Truck*2468*2468",
            "+2348030000003",
        )
        buyer = _ussd(client, "2", "+2348040000004")

    assert b"Registration Successful" in farmer.data
    assert b"Agent Registration Successful" in agent.data
    assert b"Registered!" in provider.data
    assert b"Buyer Portal" in buyer.data
    assert fetchone("SELECT COUNT(*) AS n FROM users")["n"] == 4
    assert fetchone("SELECT COUNT(*) AS n FROM user_roles")["n"] == 4

    roles = {row["role"] for row in fetchall("SELECT role FROM user_roles")}
    assert roles == {"FARMER", "AGENT", "LOGISTICS", "BUYER"}
    assert fetchone(
        "SELECT registration_channel FROM agents WHERE normalized_phone=?",
        ("+2348020000002",),
    )["registration_channel"] == "USSD"

    with patch("app.services.notification_service.notify_sms", return_value=True), patch(
        "app.services.identity_service.secrets.randbelow", return_value=123456
    ):
        requested = identity_service.request_otp("08020000002", "AGENT")
        assert requested["ok"] is True
        assert requested["debug_otp"] == "123456"
        verified = client.post(
            "/agents/activate",
            data={"action": "verify", "phone": "2348020000002", "otp": "123456"},
            follow_redirects=False,
        )
    assert verified.status_code == 302
    with client.session_transaction() as session:
        assert session["agent_phone"] == "+2348020000002"

    duplicate = client.post(
        "/agent/register",
        data={"name": "Bola Agent", "phone": "08020000002", "location": "Lagos", "pin": "4321"},
    )
    assert duplicate.status_code == 302
    assert fetchone("SELECT COUNT(*) AS n FROM agents")["n"] == 1


def test_unverified_listing_is_public_with_placeholder_and_can_be_suspended(mvp):
    _, client = mvp
    from app.models.database import execute, fetchone
    from app.services import product_service

    execute(
        """INSERT INTO farmers
           (name, phone, normalized_phone, crop, location, pin_hash, price,
            listing_status, verification_status, kyc_status, is_active)
           VALUES ('Ada Farmer', '+2348011000001', '+2348011000001',
                   'Maize', 'Kaduna', 'x', 0, 'DRAFT', 'UNVERIFIED', 'PENDING', 1)"""
    )
    execute(
        """INSERT INTO agents
           (name, phone, normalized_phone, pin_hash, location, is_active)
           VALUES ('Bola Agent', '+2348021000002', '+2348021000002', 'x', 'Kaduna', 1)"""
    )
    result = product_service.submit_agent_product_listing(
        "+2348021000002", "08011000001", "Maize", 25000,
        "Kaduna", "Clean dry maize", 20, None,
    )
    assert result["ok"] is True
    listing = fetchone("SELECT * FROM farmers WHERE normalized_phone=?", ("+2348011000001",))
    assert listing["listing_status"] == "PUBLISHED"
    assert listing["verification_status"] == "PENDING"
    assert listing["product_image_path"] is None

    marketplace = client.get("/marketplace")
    assert marketplace.status_code == 200
    assert b"Maize" in marketplace.data
    assert b"sowtrust-product-placeholder.png" in marketplace.data

    suspended = product_service.review_product_listing(
        "+2348011000001", "SUSPENDED", "ops-admin", "Image could not be verified"
    )
    assert suspended["ok"] is True
    assert b"Maize" not in client.get("/marketplace").data


def test_public_routes_staff_authorization_and_dashboard_api(mvp):
    app, client = mvp
    from app.models.database import execute

    home = client.get("/")
    assert home.status_code == 200
    for text in (b"Buyers", b"Farmers", b"Agents", b"Logistics"):
        assert text in home.data
    assert client.get("/agents/login").status_code == 302
    assert client.get("/missing-page").status_code == 404
    assert client.get("/admin/users").status_code == 302

    with client.session_transaction() as session:
        session["buyer_phone"] = "+2348050000005"
    assert client.get("/admin/users").status_code == 403
    with client.session_transaction() as session:
        session.clear()

    login = client.post(
        "/staff/login",
        data={"username": "ops-admin", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 302
    assert client.get("/admin/users").status_code == 200

    assert client.get("/api/internal/dashboard/snapshot").status_code == 401
    execute(
        """INSERT INTO buyers
           (phone, normalized_phone, name, registration_channel, phone_verified)
           VALUES ('+2348050000005', '+2348050000005', 'Test Buyer', 'WEB', 1)"""
    )
    snapshot = client.get(
        "/api/internal/dashboard/snapshot",
        headers={"Authorization": "Bearer dashboard-test-token"},
    )
    assert snapshot.status_code == 200
    forbidden_fields = {"password_hash", "pin_hash", "code_hash", "id_document_path"}
    assert not forbidden_fields.intersection(str(snapshot.get_json()))

    app.config["TESTING"] = False
    with app.test_client() as protected_client:
        response = protected_client.post(
            "/staff/login", data={"username": "ops-admin", "password": "wrong"}
        )
        assert response.status_code == 400


def test_quote_before_payment_and_idempotent_verified_webhook(mvp):
    _, client = mvp
    from app.models.database import execute, fetchone
    from app.services import buyer_service, escrow_service, logistics_service

    execute(
        """INSERT INTO farmers
           (name, phone, normalized_phone, crop, location, pin_hash, price,
            listing_status, verification_status, kyc_status, is_active)
           VALUES ('Farmer One', '+2348060000006', '+2348060000006',
                   'Rice', 'Kano', 'x', 100000, 'PUBLISHED', 'VERIFIED', 'VERIFIED', 1)"""
    )
    assert buyer_service.ensure_ussd_buyer("08070000007")["ok"] is True
    execute(
        """INSERT INTO logistics_providers
           (name, phone, normalized_phone, pin_hash, operating_area,
            kyc_status, verification_status, account_status, is_active)
           VALUES ('Provider One', '+2348080000008', '+2348080000008', 'x',
                   'Kano-Lagos', 'VERIFIED', 'VERIFIED', 'ACTIVE', 1)"""
    )
    provider_id = fetchone(
        "SELECT id FROM logistics_providers WHERE normalized_phone=?", ("+2348080000008",)
    )["id"]

    order = escrow_service.create_order_awaiting_quote(
        "+2348070000007", "+2348060000006", "Rice", 1, 100000,
        delivery_address="Lagos",
    )
    assert order["ok"] is True
    assert logistics_service.create_quote_request(order["txn_id"], "Kano", "Lagos")["ok"] is True
    locked = logistics_service.record_quote(
        order["txn_id"], 17500, "Kano", "Lagos", provider_id, "ops-admin"
    )
    assert locked["ok"] is True
    assert fetchone("SELECT status FROM escrow_ledger WHERE txn_id=?", (order["txn_id"],))["status"] == "QUOTE_READY"

    payment_mock = {
        "ok": True, "account_number": "1234567890", "bank_name": "Test Bank"
    }
    with patch(
        "app.services.escrow_service.payment_service.initiate_bank_transfer_charge",
        return_value=payment_mock,
    ) as initiate:
        assert escrow_service.initiate_payment_for_order(
            order["txn_id"], "+2348070000007"
        )["ok"] is False
        assert initiate.call_count == 0
        assert logistics_service.accept_locked_quote(
            order["txn_id"], "+2348070000007"
        )["ok"] is True
        payment = escrow_service.initiate_payment_for_order(
            order["txn_id"], "+2348070000007"
        )
    assert payment["ok"] is True
    assert payment["buyer_total"] == 120000.0
    assert logistics_service.record_quote(
        order["txn_id"], 18000, "Kano", "Lagos", provider_id
    )["ok"] is False

    execute(
        """INSERT INTO logistics_providers
           (name, phone, normalized_phone, pin_hash, operating_area,
            kyc_status, verification_status, account_status, is_active)
           VALUES ('Provider Two', '+2348080000009', '+2348080000009', 'x',
                   'Kano-Lagos', 'VERIFIED', 'VERIFIED', 'ACTIVE', 1)"""
    )
    replacement_id = fetchone(
        "SELECT id FROM logistics_providers WHERE normalized_phone=?",
        ("+2348080000009",),
    )["id"]
    replacement = logistics_service.request_provider_replacement(
        order["txn_id"], replacement_id, 16000, "ops-admin", "Provider unavailable"
    )
    assert replacement["ok"] is True
    assert replacement["buyer_total_unchanged"] is True
    updated_quote = fetchone(
        "SELECT * FROM logistics_quotes WHERE order_id=?", (order["txn_id"],)
    )
    assert updated_quote["logistics_provider_id"] == replacement_id
    assert updated_quote["quoted_amount"] == 17500
    assert fetchone(
        "SELECT buyer_total FROM escrow_ledger WHERE txn_id=?", (order["txn_id"],)
    )["buyer_total"] == 120000
    higher = logistics_service.request_provider_replacement(
        order["txn_id"], provider_id, 18000, "ops-admin", "Second provider unavailable"
    )
    assert higher["ok"] is False
    assert "after payment initialization" in higher["error"]

    ledger = fetchone("SELECT * FROM escrow_ledger WHERE txn_id=?", (order["txn_id"],))
    event = {"event": "charge.success", "data": {"reference": ledger["payment_reference"]}}
    verified = {
        "ok": True, "paid": True, "reference": ledger["payment_reference"],
        "amount_kobo": 12000000, "currency": "NGN",
    }
    with patch("app.routes.webhooks.verify_webhook_signature", return_value=True), patch(
        "app.routes.webhooks.payment_service.verify_transaction", return_value=verified
    ), patch("app.services.escrow_service.notify_escrow_locked"), patch(
        "app.services.escrow_service.notify_release_code"
    ):
        first = client.post("/webhooks/paystack", json=event)
        second = client.post("/webhooks/paystack", json=event)
    assert first.status_code == 200
    assert second.get_json()["status"] == "duplicate"
    assert fetchone("SELECT status FROM escrow_ledger WHERE txn_id=?", (order["txn_id"],))["status"] == "ESCROW_LOCKED"
    assert fetchone("SELECT COUNT(*) AS n FROM payment_webhook_events")["n"] == 1


def test_upload_validation_uses_file_signatures(mvp):
    from app.services import document_storage

    executable = FileStorage(stream=io.BytesIO(b"MZ-not-an-image"), filename="photo.png")
    assert document_storage.save_product_image(executable)["ok"] is False

    png = FileStorage(
        stream=io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"safe-test-data"),
        filename="produce.png",
    )
    saved = document_storage.save_product_image(png)
    assert saved["ok"] is True
    assert saved["path"].endswith(".png")


def test_higher_replacement_requires_buyer_approval_before_payment(mvp):
    from app.models.database import execute, fetchone
    from app.services import buyer_service, escrow_service, logistics_service

    execute(
        """INSERT INTO farmers
           (name, phone, normalized_phone, crop, location, pin_hash, price,
            listing_status, verification_status, kyc_status, is_active)
           VALUES ('Replacement Farmer', '+2348061000001', '+2348061000001',
                   'Beans', 'Kano', 'x', 100000, 'PUBLISHED', 'VERIFIED', 'VERIFIED', 1)"""
    )
    assert buyer_service.ensure_ussd_buyer("08071000002")["ok"] is True
    for name, phone in (
        ("Original Provider", "+2348081000003"),
        ("Replacement Provider", "+2348081000004"),
    ):
        execute(
            """INSERT INTO logistics_providers
               (name, phone, normalized_phone, pin_hash, operating_area,
                kyc_status, verification_status, account_status, is_active)
               VALUES (?, ?, ?, 'x', 'Kano-Lagos', 'VERIFIED', 'VERIFIED', 'ACTIVE', 1)""",
            (name, phone, phone),
        )
    original_id = fetchone(
        "SELECT id FROM logistics_providers WHERE normalized_phone=?",
        ("+2348081000003",),
    )["id"]
    replacement_id = fetchone(
        "SELECT id FROM logistics_providers WHERE normalized_phone=?",
        ("+2348081000004",),
    )["id"]

    order = escrow_service.create_order_awaiting_quote(
        "+2348071000002", "+2348061000001", "Beans", 1, 100000,
        delivery_address="Lagos",
    )
    logistics_service.create_quote_request(order["txn_id"], "Kano", "Lagos")
    assert logistics_service.record_quote(
        order["txn_id"], 17500, "Kano", "Lagos", original_id, "ops-admin"
    )["ok"] is True
    assert logistics_service.accept_locked_quote(
        order["txn_id"], "+2348071000002"
    )["ok"] is True

    with patch("app.services.notification_service.notify_sms", return_value=True):
        requested = logistics_service.request_provider_replacement(
            order["txn_id"], replacement_id, 18000, "ops-admin", "Provider unavailable"
        )
    assert requested["buyer_approval_required"] is True
    assert fetchone(
        "SELECT buyer_total FROM escrow_ledger WHERE txn_id=?", (order["txn_id"],)
    )["buyer_total"] == 120000

    assert logistics_service.approve_quote_replacement(
        order["txn_id"], "+2348071000002"
    )["ok"] is True
    payment_mock = {"ok": True, "account_number": "1234567890", "bank_name": "Test Bank"}
    with patch(
        "app.services.escrow_service.payment_service.initiate_bank_transfer_charge",
        return_value=payment_mock,
    ):
        payment = escrow_service.initiate_payment_for_order(
            order["txn_id"], "+2348071000002"
        )
    assert payment["ok"] is True
    assert payment["buyer_total"] == 120500
    quote = fetchone("SELECT * FROM logistics_quotes WHERE order_id=?", (order["txn_id"],))
    assert quote["logistics_provider_id"] == replacement_id
    assert quote["quoted_amount"] == 18000
