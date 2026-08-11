import os
import pytest
from unittest.mock import patch

os.environ.setdefault("AT_USERNAME", "sandbox")
os.environ.setdefault("AT_API_KEY", "test_dummy_key")
os.environ.setdefault("PAYSTACK_SECRET_KEY", "sk_test_dummy_key_for_testing")


@pytest.fixture
def client(tmp_path):
    from config.settings import config
    test_db = str(tmp_path / "test_expiry.db")
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


def _seed_farmer(phone):
    from app.models.database import execute
    execute(
        """INSERT OR IGNORE INTO farmers (phone, name, crop, location, pin_hash, kyc_status)
           VALUES (?, 'Test Farmer', 'Maize', 'Testville', 'x', 'VERIFIED')""",
        (phone,)
    )


def test_pending_payment_cancelled_after_timeout(client):
    """Buyer never completed the transfer — no money to refund, just cancel."""
    from app.models.database import execute, fetchone
    from app.services.escrow_service import expire_stale_escrows

    _seed_farmer('f1')
    execute(
        """INSERT INTO escrow_ledger
           (farmer_phone, buyer_phone, crop, quantity_bags, amount, service_fee,
            release_code_hash, status, payment_reference, locked_at)
           VALUES ('f1','b1','Maize',5,7500,187.5,'','PENDING_PAYMENT','PAY-OLD',
                   datetime('now','-2 hours'))"""
    )
    results = expire_stale_escrows()
    assert results["cancelled"] == 1

    row = fetchone("SELECT status FROM escrow_ledger WHERE payment_reference='PAY-OLD'")
    assert row["status"] == "CANCELLED"


def test_recent_pending_payment_not_touched(client):
    """A buyer who initiated payment 5 minutes ago must NOT be cancelled yet."""
    from app.models.database import execute, fetchone
    from app.services.escrow_service import expire_stale_escrows

    _seed_farmer('f1')
    execute(
        """INSERT INTO escrow_ledger
           (farmer_phone, buyer_phone, crop, quantity_bags, amount, service_fee,
            release_code_hash, status, payment_reference, locked_at)
           VALUES ('f1','b1','Maize',5,7500,187.5,'','PENDING_PAYMENT','PAY-RECENT',
                   datetime('now','-5 minutes'))"""
    )
    results = expire_stale_escrows()
    assert results["cancelled"] == 0

    row = fetchone("SELECT status FROM escrow_ledger WHERE payment_reference='PAY-RECENT'")
    assert row["status"] == "PENDING_PAYMENT"


def test_expired_locked_escrow_triggers_refund(client):
    """Farmer never delivered within 72hrs — buyer's real money must be refunded."""
    from app.models.database import execute, fetchone
    from app.services.escrow_service import expire_stale_escrows

    _seed_farmer('+2348011110000')
    execute(
        """INSERT INTO escrow_ledger
           (farmer_phone, buyer_phone, crop, quantity_bags, amount, service_fee,
            release_code_hash, status, payment_reference, locked_at, expires_at)
           VALUES ('+2348011110000','+2348022220000','Maize',5,7500,187.5,'x',
                   'ESCROW_LOCKED','PAY-EXPIRED', datetime('now','-4 days'),
                   datetime('now','-1 hours'))"""
    )
    fake_refund = {"ok": True, "refund_reference": "RFD-123"}
    with patch("app.services.escrow_service.payment_service.initiate_refund",
               return_value=fake_refund), \
         patch("app.services.sms_service.send_sms", return_value=True) as mock_sms:
        results = expire_stale_escrows()

    assert results["refunded"] == 1
    row = fetchone("SELECT status FROM escrow_ledger WHERE payment_reference='PAY-EXPIRED'")
    assert row["status"] == "EXPIRED"
    # Both buyer and farmer should be notified
    assert mock_sms.call_count == 2


def test_refund_failure_is_flagged_not_silently_dropped(client):
    """If Paystack's refund call fails, we must NOT mark it expired silently —
    it needs to stay visible for manual follow-up."""
    from app.models.database import execute, fetchone
    from app.services.escrow_service import expire_stale_escrows

    _seed_farmer('f2')
    execute(
        """INSERT INTO escrow_ledger
           (farmer_phone, buyer_phone, crop, quantity_bags, amount, service_fee,
            release_code_hash, status, payment_reference, locked_at, expires_at)
           VALUES ('f2','b2','Yam',3,9000,225,'x','ESCROW_LOCKED','PAY-FAILREFUND',
                   datetime('now','-4 days'), datetime('now','-1 hours'))"""
    )
    fake_refund = {"ok": False, "error": "Insufficient balance on Paystack account"}
    with patch("app.services.escrow_service.payment_service.initiate_refund",
               return_value=fake_refund):
        results = expire_stale_escrows()

    assert results["refund_failed"] == 1
    row = fetchone("SELECT status FROM escrow_ledger WHERE payment_reference='PAY-FAILREFUND'")
    assert row["status"] == "ESCROW_LOCKED"  # unchanged — stays visible, not silently closed

    audit = fetchone(
        "SELECT * FROM audit_log WHERE action='REFUND_FAILED' AND details LIKE '%PAY-FAILREFUND%' "
        "OR (action='REFUND_FAILED')"
    )
    assert audit is not None


def test_active_escrow_within_window_untouched(client):
    """An escrow that's still well within its 72-hour window must be left alone."""
    from app.models.database import execute, fetchone
    from app.services.escrow_service import expire_stale_escrows

    _seed_farmer('f3')
    execute(
        """INSERT INTO escrow_ledger
           (farmer_phone, buyer_phone, crop, quantity_bags, amount, service_fee,
            release_code_hash, status, payment_reference, locked_at, expires_at)
           VALUES ('f3','b3','Rice',2,6000,150,'x','ESCROW_LOCKED','PAY-ACTIVE',
                   datetime('now'), datetime('now','+70 hours'))"""
    )
    results = expire_stale_escrows()
    assert results["refunded"] == 0
    row = fetchone("SELECT status FROM escrow_ledger WHERE payment_reference='PAY-ACTIVE'")
    assert row["status"] == "ESCROW_LOCKED"
