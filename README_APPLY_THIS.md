# Three-Sided Fee Model — Step 1 (Fee Split + Config)

## What this does
1. **New `platform_config` table** — the three fee percentages (buyer,
   seller, logistics) live here, editable via SQL without a redeploy.
   Defaults to 2.5% / 2.5% / 2.5% as specified.
2. **New `fee_service.py`** — the actual calculation logic, isolated
   and independently tested. Includes a built-in integrity check: every
   calculation asserts that `buyer_total` exactly equals the sum of
   everyone else's share, every single time. If that ever fails, the
   transaction is refused rather than silently proceeding with a
   ledger mismatch.
3. **`escrow_ledger` split fields** — `product_amount`,
   `buyer_platform_fee`, `seller_platform_fee`, `logistics_amount`,
   `logistics_platform_fee`, `buyer_total`, `farmer_settlement_amount`,
   `logistics_settlement_amount`, `sowtrust_total_revenue`. The old
   `amount`/`service_fee` columns are kept and kept in sync — nothing
   that reads those breaks.
4. **A real bug fix, found live in our test session**: the buyer's
   USSD screen used to *display* a fee-inclusive total, but the actual
   Paystack charge only ever requested the raw product amount — the
   fee was cosmetic, never actually collected. Buyers now are actually
   charged what they're shown.
5. **Logistics fields exist in the schema and calculation engine now**,
   but aren't wired into the USSD flow yet — that's step 2 (a real
   logistics provider model with its own KYC and payout rail), per the
   sequencing we agreed on. Right now `logistics_amount` defaults to 0
   everywhere, so nothing changes for the logistics side yet.

Tested against the exact worked examples in the spec doc — ₦100,000
product → ₦2,500/₦2,500 buyer/seller fees, ₦15,000 logistics → ₦375
commission, ₦117,500 buyer total, ₦5,375 total platform revenue — all
match to the naira. 32/32 tests pass (9 new, testing the fee math
specifically; 2 existing tests updated to reflect the buyer now
correctly paying their fee).

## Files — where each goes

| File here | Goes to |
|---|---|
| `fee_service.py` | **NEW** → `app/services/fee_service.py` |
| `add_three_sided_fees.py` | **NEW** → `migrations/add_three_sided_fees.py` |
| `test_fees.py` | **NEW** → `tests/test_fees.py` |
| `escrow_service.py` | **REPLACES** → `app/services/escrow_service.py` |
| `ussd.py` | **REPLACES** → `app/routes/ussd.py` |
| `init_db.py` | **REPLACES** → `migrations/init_db.py` |
| `test_payments.py` | **REPLACES** → `tests/test_payments.py` |

## How to apply
```
# 1. Copy files to the paths above

# 2. Run the new migration against your existing DB
python migrations/add_three_sided_fees.py

# 3. Run the full suite
python -m pytest tests/ -v
# Expect: 32 passed

# 4. Restart Flask, retest a live buy via local_test_flow.py buy —
#    you should now see the buyer charged product+fee, not just product
```

## To change fee percentages later (no redeploy needed)
```sql
UPDATE platform_config SET buyer_fee_percent=3.0, seller_fee_percent=2.0 WHERE id=1;
```
Takes effect on the very next transaction — `fee_service` reads this
table live on every call, deliberately not cached.

## What's next (step 2, not built yet)
A real `logistics_providers` table (business info, vehicle info, bank
account, KYC status — mirroring what farmers already have), a
`logistics_service.py`, and wiring `logistics_amount` into the actual
USSD buyer flow so the logistics leg of the fee model goes from
"calculable" to "actually happening." That's a comparably-sized chunk
of work to what we just did for payments — worth its own session.
