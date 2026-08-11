"""
Sowtrust — USSD route tests v6.1
Run: python -m pytest tests/
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch
from app import create_app


@pytest.fixture
def client(tmp_path):
    # NOTE: config.settings.config is a module-level singleton — its
    # DATABASE_PATH is read from the environment once, at first import.
    # Setting os.environ alone AFTER that point has no effect, so every
    # test would silently share one real on-disk database (this was a
    # pre-existing bug: a stray `sowtrust.db` file was accumulating state
    # across test runs in the repo root). Patching the singleton
    # attribute directly guarantees each test gets a fresh, isolated DB.
    from config.settings import config
    test_db = str(tmp_path / "test.db")
    os.environ["DATABASE_PATH"] = test_db
    config.DATABASE_PATH = test_db

    from migrations.init_db import init_db
    init_db()
    from migrations.add_products_table import migrate as migrate_products
    migrate_products()

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def ussd(client, text, phone="+2341234567890"):
    return client.post("/ussd", data={"text": text, "phoneNumber": phone})


def test_main_menu(client):
    r = ussd(client, "")
    assert b"Sowtrust Global" in r.data
    assert b"Farmer Portal" in r.data


def test_farmer_registration_success(client):
    with patch("app.services.sms_service.send_sms", return_value=True):
        r = ussd(client, "1*1*John Farmer*Maize*Lagos*1234*1234", phone="+2340000000001")
    # Fresh DB — should register successfully
    assert b"Registration Successful" in r.data or b"already exists" in r.data


def test_farmer_registration_custom_product(client):
    # The whole point of the dynamic catalog: any product name works,
    # not just the old hardcoded 7.
    with patch("app.services.sms_service.send_sms", return_value=True):
        r = ussd(client, "1*1*Amaka*Bitter Leaf*Enugu*1234*1234", phone="+2340000000009")
    assert b"Registration Successful" in r.data
    assert b"Bitter Leaf" in r.data


def test_farmer_registration_invalid_product_rejected(client):
    with patch("app.services.sms_service.send_sms", return_value=True):
        r = ussd(client, "1*1*Tunde*123456*Oyo*1234*1234", phone="+2340000000010")
    assert b"valid product name" in r.data


def test_farmer_duplicate_blocked(client):
    with patch("app.services.sms_service.send_sms", return_value=True):
        ussd(client, "1*1*John Farmer*Maize*Lagos*1234*1234", phone="+2340000000002")
        r = ussd(client, "1*1*John Again*Maize*Lagos*1234*1234", phone="+2340000000002")
    assert b"already exists" in r.data


def test_invalid_pin_mismatch(client):
    with patch("app.services.sms_service.send_sms", return_value=True):
        r = ussd(client, "1*1*Jane*Rice*Kano*1234*5678", phone="+2340000000003")
    assert b"PINs do not match" in r.data


def test_buyer_browse_no_farmers(client):
    # Fresh DB, no farmers — system should tell buyer none available
    r = ussd(client, "2*1*Maize", phone="+2349000000001")
    assert b"No verified sellers" in r.data


def test_buyer_browse_with_farmers(client):
    # Register and verify a farmer, then buyer browses
    with patch("app.services.sms_service.send_sms", return_value=True):
        ussd(client, "1*1*Emeka*Maize*Ogun*1234*1234", phone="+2348000000001")
    # Set price
    with patch("app.services.sms_service.send_sms", return_value=True):
        ussd(client, "1*2*1234*150000", phone="+2348000000001")
    # Manually verify farmer KYC in DB
    from app.models.database import execute
    execute("UPDATE farmers SET kyc_status='VERIFIED' WHERE phone=?", ("+2348000000001",))
    # Buyer types the product name directly
    r = ussd(client, "2*1*Maize", phone="+2349000000002")
    assert b"Emeka" in r.data
    assert b"Enter number to select" in r.data


def test_buyer_browse_menu_number_pick(client):
    # Buyer can also pick from the numbered "currently listed" menu
    with patch("app.services.sms_service.send_sms", return_value=True):
        ussd(client, "1*1*Ngozi*Yam*Benue*1234*1234", phone="+2348000000005")
    with patch("app.services.sms_service.send_sms", return_value=True):
        ussd(client, "1*2*1234*90000", phone="+2348000000005")
    from app.models.database import execute
    execute("UPDATE farmers SET kyc_status='VERIFIED' WHERE phone=?", ("+2348000000005",))
    # See the dynamic list first
    r1 = ussd(client, "2*1", phone="+2349000000006")
    assert b"Yam" in r1.data
    # Then pick option "1"
    r2 = ussd(client, "2*1*1", phone="+2349000000006")
    assert b"Ngozi" in r2.data


def test_buyer_browse_partial_match(client):
    # Fuzzy/partial free-text match: "tomato" should find "Tomatoes"
    with patch("app.services.sms_service.send_sms", return_value=True):
        ussd(client, "1*1*Bisi*Tomatoes*Kaduna*1234*1234", phone="+2348000000007")
    with patch("app.services.sms_service.send_sms", return_value=True):
        ussd(client, "1*2*1234*40000", phone="+2348000000007")
    from app.models.database import execute
    execute("UPDATE farmers SET kyc_status='VERIFIED' WHERE phone=?", ("+2348000000007",))
    r = ussd(client, "2*1*tomato", phone="+2349000000008")
    assert b"Bisi" in r.data


def test_buyer_post_request(client):
    r = ussd(client, "2*2*Dried Ginger*10*120000*Ogun State", phone="+2349000000003")
    assert b"Request Posted" in r.data
    assert b"Dried Ginger" in r.data


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
