"""
Sowtrust — Inspect a transaction's full financial breakdown.

Shows every component of the three-sided fee model for one order, and
verifies the ledger invariant (buyer_total must exactly equal the sum
of what everyone receives).

Usage:
    python scripts/inspect_txn.py <TXN_ID>
    python scripts/inspect_txn.py            (shows the most recent order)
"""
import sys
import os
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import config

conn = sqlite3.connect(config.DATABASE_PATH)
conn.row_factory = sqlite3.Row

if len(sys.argv) > 1:
    row = conn.execute(
        "SELECT * FROM escrow_ledger WHERE txn_id = ?", (sys.argv[1].upper(),)
    ).fetchone()
else:
    row = conn.execute(
        "SELECT * FROM escrow_ledger ORDER BY locked_at DESC LIMIT 1"
    ).fetchone()

if not row:
    print("No matching transaction found.")
    sys.exit(1)


def naira(v):
    return f"NGN {(v or 0):>12,.2f}"


print(f"\nTXN: {row['txn_id']}    Status: {row['status']}")
print(f"Crop: {row['crop']}  |  Qty: {row['quantity_bags']} bags")
print("=" * 46)
print("BUYER PAYS")
print(f"  Product                {naira(row['product_amount'])}")
print(f"  Buyer platform fee     {naira(row['buyer_platform_fee'])}")
print(f"  Logistics              {naira(row['logistics_amount'])}")
print(f"  {'TOTAL':<22} {naira(row['buyer_total'])}")
print("-" * 46)
print("PAYOUTS")
print(f"  Farmer receives        {naira(row['farmer_settlement_amount'])}")
print(f"    (product minus seller fee of {naira(row['seller_platform_fee']).strip()})")
print(f"  Logistics receives     {naira(row['logistics_settlement_amount'])}")
print("-" * 46)
print("SOWTRUST REVENUE")
print(f"  Buyer fee              {naira(row['buyer_platform_fee'])}")
print(f"  Seller fee             {naira(row['seller_platform_fee'])}")
print(f"  Logistics commission   {naira(row['logistics_platform_fee'])}")
print(f"  {'TOTAL':<22} {naira(row['sowtrust_total_revenue'])}")
print("=" * 46)

total_out = round(
    (row["farmer_settlement_amount"] or 0)
    + (row["logistics_settlement_amount"] or 0)
    + (row["sowtrust_total_revenue"] or 0), 2
)
buyer_total = round(row["buyer_total"] or 0, 2)

if total_out == buyer_total:
    print(f"✅ Ledger balances: {naira(total_out).strip()} out == {naira(buyer_total).strip()} in")
else:
    print(f"❌ LEDGER MISMATCH: components total {naira(total_out).strip()} "
          f"but buyer paid {naira(buyer_total).strip()}")
    sys.exit(1)
