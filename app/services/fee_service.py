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
from decimal import Decimal, ROUND_HALF_UP

from app.models.database import fetchone


def to_kobo(amount) -> int:
    """Convert a naira value to integer kobo without binary-float drift."""
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))


def from_kobo(amount_kobo: int) -> float:
    return float((Decimal(int(amount_kobo)) / 100).quantize(Decimal("0.01")))


def _percentage_kobo(amount_kobo: int, percent) -> int:
    return int(
        (Decimal(amount_kobo) * Decimal(str(percent)) / Decimal("100"))
        .to_integral_value(rounding=ROUND_HALF_UP)
    )


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
    product_kobo = to_kobo(product_amount)
    buyer_fee_kobo = _percentage_kobo(product_kobo, cfg["buyer_fee_percent"])
    seller_fee_kobo = _percentage_kobo(product_kobo, cfg["seller_fee_percent"])
    farmer_kobo = product_kobo - seller_fee_kobo
    subtotal_kobo = product_kobo + buyer_fee_kobo
    return {
        "product_amount": from_kobo(product_kobo),
        "buyer_platform_fee": from_kobo(buyer_fee_kobo),
        "seller_platform_fee": from_kobo(seller_fee_kobo),
        "farmer_settlement_amount": from_kobo(farmer_kobo),
        "buyer_subtotal": from_kobo(subtotal_kobo),
        "product_amount_kobo": product_kobo,
        "buyer_platform_fee_kobo": buyer_fee_kobo,
        "seller_platform_fee_kobo": seller_fee_kobo,
        "farmer_settlement_amount_kobo": farmer_kobo,
        "buyer_subtotal_kobo": subtotal_kobo,
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
    logistics_kobo = to_kobo(logistics_amount)
    fee_kobo = _percentage_kobo(logistics_kobo, cfg["logistics_fee_percent"])
    settlement_kobo = logistics_kobo - fee_kobo
    return {
        "logistics_amount": from_kobo(logistics_kobo),
        "logistics_platform_fee": from_kobo(fee_kobo),
        "logistics_settlement_amount": from_kobo(settlement_kobo),
        "logistics_amount_kobo": logistics_kobo,
        "logistics_platform_fee_kobo": fee_kobo,
        "logistics_settlement_amount_kobo": settlement_kobo,
    }


def calculate_full_order(product_amount: float, logistics_amount: float = 0.0) -> dict:
    """
    Full order breakdown combining both sides — this is what
    escrow_service should call when initiating a new order.
    """
    cfg = get_platform_config()
    product = calculate_product_fees(product_amount, cfg)
    logistics = calculate_logistics_fees(logistics_amount, cfg) if logistics_amount else {
        "logistics_amount": 0.0, "logistics_platform_fee": 0.0,
        "logistics_settlement_amount": 0.0, "logistics_amount_kobo": 0,
        "logistics_platform_fee_kobo": 0, "logistics_settlement_amount_kobo": 0,
    }

    buyer_total_kobo = product["buyer_subtotal_kobo"] + logistics["logistics_amount_kobo"]
    revenue_kobo = (
        product["buyer_platform_fee_kobo"] + product["seller_platform_fee_kobo"]
        + logistics["logistics_platform_fee_kobo"]
    )

    result = {
        "product_amount": product["product_amount"],
        "buyer_platform_fee": product["buyer_platform_fee"],
        "seller_platform_fee": product["seller_platform_fee"],
        "farmer_settlement_amount": product["farmer_settlement_amount"],
        "logistics_amount": logistics["logistics_amount"],
        "logistics_platform_fee": logistics["logistics_platform_fee"],
        "logistics_settlement_amount": logistics["logistics_settlement_amount"],
        "buyer_total": from_kobo(buyer_total_kobo),
        "sowtrust_total_revenue": from_kobo(revenue_kobo),
        "product_amount_kobo": product["product_amount_kobo"],
        "buyer_platform_fee_kobo": product["buyer_platform_fee_kobo"],
        "seller_platform_fee_kobo": product["seller_platform_fee_kobo"],
        "farmer_settlement_amount_kobo": product["farmer_settlement_amount_kobo"],
        "logistics_amount_kobo": logistics["logistics_amount_kobo"],
        "logistics_platform_fee_kobo": logistics["logistics_platform_fee_kobo"],
        "logistics_settlement_amount_kobo": logistics["logistics_settlement_amount_kobo"],
        "buyer_total_kobo": buyer_total_kobo,
        "sowtrust_total_revenue_kobo": revenue_kobo,
    }

    # Self-check every time — if this ever fails, something in the math
    # above is wrong and we want a loud, immediate error, not a silent
    # ledger mismatch discovered weeks later during reconciliation.
    check_kobo = (
        result["farmer_settlement_amount_kobo"]
        + result["logistics_settlement_amount_kobo"]
        + result["sowtrust_total_revenue_kobo"]
    )
    assert check_kobo == buyer_total_kobo, (
        f"Ledger integrity check failed: components sum to {check_kobo} kobo, "
        f"but buyer_total is {buyer_total_kobo} kobo. This should never happen — "
        f"do not proceed with this transaction."
    )

    return result
