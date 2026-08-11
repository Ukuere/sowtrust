"""
Sowtrust — Fee Service.

Calculates the three-sided platform fee split. Deliberately isolated
from escrow_service so the math can be tested on its own, independent
of database/payment concerns.

Fee percentages are read from the `platform_config` table (NOT hardcoded
or env-var-only) — an admin can change them without a redeploy.

Rounding note: every component below is rounded independently to 2
decimal places (kobo), then buyer_total and sowtrust_total_revenue are
computed as SUMS of those already-rounded numbers — never as a rounded
total minus rounded parts. This means the ledger always balances
exactly (buyer_total == farmer_settlement + logistics_settlement +
sowtrust_revenue) with no possibility of a rounding-drift mismatch.
"""
from app.models.database import fetchone


def get_platform_config() -> dict:
    """
    Reads current fee percentages live from the DB every call — deliberately
    not cached, since the whole point is that these are editable without
    a redeploy and should take effect immediately.
    """
    row = fetchone("SELECT * FROM platform_config WHERE id = 1")
    if not row:
        # Should never happen if the migration ran, but fail safe rather
        # than crash a live transaction — fall back to the documented default.
        return {"buyer_fee_percent": 2.5, "seller_fee_percent": 2.5, "logistics_fee_percent": 2.5}
    return dict(row)


def calculate_product_fees(product_amount: float, cfg: dict | None = None) -> dict:
    """
    The product-side split (buyer + seller fees).

    Example: product_amount = 100,000, both fees at 2.5%
      -> buyer_platform_fee    = 2,500
      -> seller_platform_fee   = 2,500
      -> farmer_settlement_amount = 97,500
      -> buyer_subtotal (product + buyer fee, excludes logistics) = 102,500
    """
    cfg = cfg or get_platform_config()
    buyer_fee = round(product_amount * cfg["buyer_fee_percent"] / 100, 2)
    seller_fee = round(product_amount * cfg["seller_fee_percent"] / 100, 2)
    return {
        "product_amount": round(product_amount, 2),
        "buyer_platform_fee": buyer_fee,
        "seller_platform_fee": seller_fee,
        "farmer_settlement_amount": round(product_amount - seller_fee, 2),
        "buyer_subtotal": round(product_amount + buyer_fee, 2),
    }


def calculate_logistics_fees(logistics_amount: float, cfg: dict | None = None) -> dict:
    """
    The logistics-side split. The buyer pays the FULL quoted logistics
    amount — the platform commission is deducted from the provider's
    settlement, not added on top of what the buyer pays.

    Example: logistics_amount = 15,000, fee at 2.5%
      -> logistics_platform_fee = 375
      -> logistics_settlement_amount = 14,625
    """
    cfg = cfg or get_platform_config()
    fee = round(logistics_amount * cfg["logistics_fee_percent"] / 100, 2)
    return {
        "logistics_amount": round(logistics_amount, 2),
        "logistics_platform_fee": fee,
        "logistics_settlement_amount": round(logistics_amount - fee, 2),
    }


def calculate_full_order(product_amount: float, logistics_amount: float = 0.0) -> dict:
    """
    Full order breakdown combining both sides — this is what
    escrow_service should call when initiating a new order.
    """
    cfg = get_platform_config()
    product = calculate_product_fees(product_amount, cfg)
    logistics = calculate_logistics_fees(logistics_amount, cfg) if logistics_amount else {
        "logistics_amount": 0.0, "logistics_platform_fee": 0.0, "logistics_settlement_amount": 0.0
    }

    buyer_total = round(product["buyer_subtotal"] + logistics["logistics_amount"], 2)
    sowtrust_total_revenue = round(
        product["buyer_platform_fee"] + product["seller_platform_fee"] + logistics["logistics_platform_fee"], 2
    )

    result = {
        "product_amount": product["product_amount"],
        "buyer_platform_fee": product["buyer_platform_fee"],
        "seller_platform_fee": product["seller_platform_fee"],
        "farmer_settlement_amount": product["farmer_settlement_amount"],
        "logistics_amount": logistics["logistics_amount"],
        "logistics_platform_fee": logistics["logistics_platform_fee"],
        "logistics_settlement_amount": logistics["logistics_settlement_amount"],
        "buyer_total": buyer_total,
        "sowtrust_total_revenue": sowtrust_total_revenue,
    }

    # Self-check every time — if this ever fails, something in the math
    # above is wrong and we want a loud, immediate error, not a silent
    # ledger mismatch discovered weeks later during reconciliation.
    check = round(
        result["farmer_settlement_amount"] + result["logistics_settlement_amount"] + result["sowtrust_total_revenue"],
        2
    )
    assert check == buyer_total, (
        f"Ledger integrity check failed: components sum to {check}, "
        f"but buyer_total is {buyer_total}. This should never happen — "
        f"do not proceed with this transaction."
    )

    return result
