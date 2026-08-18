import os
import pytest

os.environ.setdefault("AT_USERNAME", "sandbox")
os.environ.setdefault("AT_API_KEY", "test_dummy_key")
os.environ.setdefault("PAYSTACK_SECRET_KEY", "sk_test_dummy_key_for_testing")


@pytest.fixture
def client(tmp_path):
    from config.settings import config
    test_db = str(tmp_path / "test_fees.db")
    os.environ["DATABASE_PATH"] = test_db
    config.DATABASE_PATH = test_db

    from migrations.init_db import init_db
    init_db()
    from migrations.add_products_table import migrate as migrate_products
    migrate_products()
    from migrations.add_payments_columns import migrate as migrate_payments
    migrate_payments()
    from migrations.add_three_sided_fees import migrate as migrate_fees
    migrate_fees()

    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_default_config_is_2_5_percent_all_three(client):
    from app.services.fee_service import get_platform_config
    cfg = get_platform_config()
    assert cfg["buyer_fee_percent"] == 2.5
    assert cfg["seller_fee_percent"] == 2.5
    assert cfg["logistics_fee_percent"] == 2.5


def test_product_fee_matches_doc_example(client):
    """Matches the exact worked example from the spec doc:
    product=100,000 -> buyer fee 2,500, seller fee 2,500, farmer gets 97,500"""
    from app.services.fee_service import calculate_product_fees
    result = calculate_product_fees(100000)
    assert result["buyer_platform_fee"] == 2500
    assert result["seller_platform_fee"] == 2500
    assert result["farmer_settlement_amount"] == 97500
    assert result["buyer_subtotal"] == 102500


def test_logistics_fee_matches_doc_example(client):
    """Matches the spec doc example: logistics=15,000 -> commission 375,
    provider receives 14,625 (fee deducted from provider, not added for buyer)"""
    from app.services.fee_service import calculate_logistics_fees
    result = calculate_logistics_fees(15000)
    assert result["logistics_platform_fee"] == 375
    assert result["logistics_settlement_amount"] == 14625


def test_full_order_matches_doc_worked_example(client):
    """The complete worked example from the spec doc:
    product 100,000 + logistics 15,000 -> buyer pays 117,500 total,
    Sowtrust earns 5,375 total across all three fee components."""
    from app.services.fee_service import calculate_full_order
    result = calculate_full_order(product_amount=100000, logistics_amount=15000)

    assert result["buyer_total"] == 117500
    assert result["farmer_settlement_amount"] == 97500
    assert result["logistics_settlement_amount"] == 14625
    assert result["sowtrust_total_revenue"] == 5375


def test_ledger_always_balances_product_only(client):
    """The core invariant: buyer_total must always exactly equal the sum
    of what everyone else receives. No transaction should ever be allowed
    to proceed if this doesn't hold — see the assert inside calculate_full_order."""
    from app.services.fee_service import calculate_full_order
    for amount in [100, 999.99, 50000, 123456.78, 1]:
        result = calculate_full_order(amount)
        total_out = (result["farmer_settlement_amount"]
                     + result["logistics_settlement_amount"]
                     + result["sowtrust_total_revenue"])
        assert round(total_out, 2) == result["buyer_total"], f"Mismatch at amount={amount}"


def test_ledger_always_balances_with_logistics(client):
    """Same invariant, but with a logistics leg included — the case most
    likely to introduce a rounding drift bug if the math were done wrong."""
    from app.services.fee_service import calculate_full_order
    for product, logistics in [(100000, 15000), (999.99, 333.33), (1, 1), (54321.09, 8765.43)]:
        result = calculate_full_order(product, logistics)
        total_out = (result["farmer_settlement_amount"]
                     + result["logistics_settlement_amount"]
                     + result["sowtrust_total_revenue"])
        assert round(total_out, 2) == result["buyer_total"], f"Mismatch at {product}/{logistics}"


def test_zero_logistics_defaults_cleanly(client):
    """Most current orders have no logistics leg wired in yet — must not error,
    must just show zero for that component."""
    from app.services.fee_service import calculate_full_order
    result = calculate_full_order(50000)
    assert result["logistics_amount"] == 0
    assert result["logistics_platform_fee"] == 0
    assert result["logistics_settlement_amount"] == 0
    assert result["buyer_total"] == result["product_amount"] + result["buyer_platform_fee"]


def test_fee_percent_is_configurable_not_hardcoded(client):
    """Changing platform_config must change the calculation immediately,
    with no redeploy or code change — this is the whole point of the table."""
    from app.models.database import execute
    from app.services.fee_service import calculate_product_fees

    execute("UPDATE platform_config SET buyer_fee_percent=5.0, seller_fee_percent=1.0 WHERE id=1")

    result = calculate_product_fees(100000)
    assert result["buyer_platform_fee"] == 5000   # 5% now, not 2.5%
    assert result["seller_platform_fee"] == 1000  # 1% now, not 2.5%
    assert result["farmer_settlement_amount"] == 99000


def test_buyer_now_actually_charged_the_fee_end_to_end(client):
    """USSD must request a quote first, then charge the locked final total."""
    from unittest.mock import patch
    from app.models.database import execute, fetchone
    from app.services import escrow_service, logistics_service

    execute(
        """INSERT INTO farmers
           (phone, name, crop, location, pin_hash, price, kyc_status, listing_status)
           VALUES
           ('+2348011110000','Test Farmer','Maize','Lagos','x',25000,'VERIFIED','PUBLISHED')"""
    )

    captured = {}
    def fake_charge(email, amount_naira, reference):
        captured["amount_charged"] = amount_naira
        return {"ok": True, "account_number": "123", "bank_name": "Test Bank", "reference": reference}

    with patch("app.services.escrow_service.payment_service.initiate_bank_transfer_charge",
               side_effect=fake_charge):
        def ussd(text, phone="+2348099990000"):
            return client.post("/ussd", data={
                "sessionId": "s1", "serviceCode": "*709#", "phoneNumber": phone, "text": text
            })
        ussd("2")
        ussd("2*1")
        ussd("2*1*Maize")
        ussd("2*1*Maize*1")
        ussd("2*1*Maize*1*2")   # 2 bags @ 25,000 = 50,000 product amount
        ussd("2*1*Maize*1*2*Lagos")
        response = ussd("2*1*Maize*1*2*Lagos*1")  # request quote

        assert b"Quote requested" in response.data
        assert "amount_charged" not in captured

        order = fetchone(
            "SELECT * FROM escrow_ledger WHERE buyer_phone=? ORDER BY id DESC LIMIT 1",
            ("+2348099990000",),
        )
        assert order["status"] == "QUOTE_PENDING"

        with patch("app.services.logistics_service.send_sms", return_value=True):
            provider = logistics_service.register_provider(
                "+2348055512345", "Fee Test Logistics", "Lagos", "Van", "1234"
            )
        assert provider["ok"] is True
        execute(
            "UPDATE logistics_providers SET kyc_status='VERIFIED' WHERE phone=?",
            ("+2348055512345",),
        )
        quote = logistics_service.record_quote(
            order["txn_id"], 17500, "Lagos", "Lagos",
            logistics_provider_id="+2348055512345", quoted_by="operations-test"
        )
        assert quote["ok"] is True
        accepted = logistics_service.accept_locked_quote(
            order["txn_id"], "+2348099990000"
        )
        assert accepted["ok"] is True
        payment = escrow_service.initiate_payment_for_order(
            order["txn_id"], "+2348099990000"
        )
        assert payment["ok"] is True

    # 50,000 goods + 1,250 buyer fee + 17,500 locked delivery quote.
    assert captured["amount_charged"] == 68750
