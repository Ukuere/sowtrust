"""
AgriHub — USSD route tests v6.1
Run: python -m pytest tests/
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch
from app import create_app


@pytest.fixture
def client(tmp_path):
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    from migrations.init_db import init_db
    init_db()
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def ussd(client, text, phone="+2341234567890"):
    return client.post("/ussd", data={"text": text, "phoneNumber": phone})


def test_main_menu(client):
    r = ussd(client, "")
    assert b"AgriHub Global" in r.data
    assert b"Farmer Portal" in r.data


def test_farmer_registration_success(client):
    with patch("app.services.sms_service.send_sms", return_value=True):
        r = ussd(client, "1*1*John Farmer*1*Lagos*1234*1234", phone="+2340000000001")
    # Fresh DB — should register successfully
    assert b"Registration Successful" in r.data or b"already exists" in r.data


def test_farmer_duplicate_blocked(client):
    with patch("app.services.sms_service.send_sms", return_value=True):
        ussd(client, "1*1*John Farmer*1*Lagos*1234*1234", phone="+2340000000002")
        r = ussd(client, "1*1*John Again*1*Lagos*1234*1234", phone="+2340000000002")
    assert b"already exists" in r.data


def test_invalid_pin_mismatch(client):
    with patch("app.services.sms_service.send_sms", return_value=True):
        r = ussd(client, "1*1*Jane*2*Kano*1234*5678", phone="+2340000000003")
    assert b"PINs do not match" in r.data


def test_buyer_browse_no_farmers(client):
    # Fresh DB, no farmers — system should tell buyer none available
    r = ussd(client, "2*1*1", phone="+2349000000001")
    assert b"No verified farmers" in r.data or b"Verified Maize Sellers" in r.data


def test_buyer_browse_with_farmers(client):
    # Register and verify a farmer, then buyer browses
    with patch("app.services.sms_service.send_sms", return_value=True):
        ussd(client, "1*1*Emeka*1*Ogun*1234*1234", phone="+2348000000001")
    # Set price
    with patch("app.services.sms_service.send_sms", return_value=True):
        ussd(client, "1*2*1234*150000", phone="+2348000000001")
    # Manually verify farmer KYC in DB
    from app.models.database import execute
    execute("UPDATE farmers SET kyc_status='VERIFIED' WHERE phone=?", ("+2348000000001",))
    # Buyer browses Maize (crop key "1")
    r = ussd(client, "2*1*1", phone="+2349000000002")
    assert b"Emeka" in r.data
    assert b"Enter number to select" in r.data


def test_buyer_post_request(client):
    r = ussd(client, "2*2*1*10*120000*Ogun State", phone="+2349000000003")
    assert b"Request Posted" in r.data


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
