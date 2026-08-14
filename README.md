# 🌱 Sowtrust
**USSD-Based Agricultural Escrow & Logistics Ecosystem**
*Growing Trust. Connecting Markets.*

---

## Folder Structure
```
sowtrust/
├── app/
│   ├── __init__.py               # Flask app factory
│   ├── routes/
│   │   ├── ussd.py               # All USSD portal logic
│   │   └── webhooks.py           # Paystack payment/transfer webhooks
│   ├── models/
│   │   └── database.py           # DB connection manager
│   ├── services/
│   │   ├── escrow_service.py     # Escrow lock/release engine (real settlement)
│   │   ├── payment_service.py    # Paystack collection, payout, refund wrapper
│   │   ├── product_service.py    # Dynamic, farmer-entered product catalog
│   │   └── sms_service.py        # Africa's Talking SMS wrapper
│   └── utils/
│       └── security.py           # PIN hashing, session mgmt
├── dashboard/
│   └── app.py                    # Streamlit CEO console
├── migrations/
│   ├── init_db.py                # Full schema (fresh installs)
│   ├── add_products_table.py     # Adds dynamic product catalog
│   └── add_payments_columns.py   # Adds Paystack payment/payout columns
├── config/
│   └── settings.py               # Central config (env vars)
├── scripts/
│   ├── seed_demo_data.py         # Demo data seeder
│   └── run_expiry_job.py         # Escrow expiry + auto-refund (run via cron)
├── tests/
│   ├── test_ussd.py              # Core USSD flow tests
│   ├── test_payments.py          # Payment collection/settlement tests
│   └── test_expiry.py            # Escrow expiry job tests
├── run.py                        # Flask entry point
├── Procfile                      # Deployment (Railway)
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Quick Start (Local)

### 1. Clone & Setup
```bash
git clone https://github.com/Ukuere/sowtrust.git
cd sowtrust
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env — add your Africa's Talking API key and Paystack test keys
```

### 3. Initialise Database
```bash
python migrations/init_db.py
python migrations/add_products_table.py
python migrations/add_payments_columns.py
python migrations/add_logistics_quotes.py
python migrations/add_production_mvp_workflows.py
```

### 4. (Optional) Seed Demo Data
```bash
python scripts/seed_demo_data.py
```

### 5. Run USSD Backend
```bash
python run.py
# Runs on http://localhost:5000
```

### 6. Expose to Africa's Talking (for USSD testing)
```bash
# Install ngrok: https://ngrok.com
ngrok http 5000
# Set your AT callback URL to: https://<ngrok-url>/ussd
```

### 7. Set Paystack Webhook (for payment testing)
```
# In Paystack dashboard > Settings > API Keys & Webhooks:
https://<ngrok-url>/webhooks/paystack
```

### 8. Run Dashboard
```bash
streamlit run dashboard/app.py
# Opens at http://localhost:8501
# Default password: changeme (set in .env)
```

### 9. Run Tests
```bash
python -m pytest tests/ -v
```

---

## Production Deployment (Railway)

### Web service
```bash
railway login
railway init
railway up
# Set env vars in Railway dashboard, including PAYSTACK_SECRET_KEY
```

### Escrow expiry job — separate Railway Cron service
Deliberately NOT run inside the web process (Procfile runs 4 gunicorn
workers — an in-process scheduler would duplicate itself 4x).
- New Railway service → Cron Job
- Command: `python scripts/run_expiry_job.py`
- Schedule: `*/15 * * * *` (every 15 minutes)
- Same env vars as the web service

### Gunicorn (VPS alternative)
```bash
gunicorn -w 4 -b 0.0.0.0:5000 "run:app"
```

---

## USSD Flow (*709#)

| Menu | Option | Description |
|------|--------|-------------|
| Main | 1 | Farmer Portal |
| Main | 2 | Buyer Portal |
| Main | 3 | Logistics |
| Main | 4 | Wallet / PIN / Bank Account |
| Main | 5 | Withdraw Funds (legacy/manual balance only) |
| Main | 6 | Agent Portal |

### Real Escrow Flow (Paystack-backed)
```
Farmer dials *709# → 1 → 1
  → Types ANY product name (not a fixed list)
  → Sets price → 4 → 3: adds verified bank/wallet account

Buyer dials *709# → 2 → 1
  → Types or picks a product → Selects farmer → Qty → Confirm
  → Gets a ONE-TIME bank account number to transfer into
  → [Paystack webhook confirms payment landed]
  → ESCROW LOCKED — SMS to farmer, release code sent to buyer

Farmer dials *709# → 1 → 3
  → Enter PIN → Enter release code (given by buyer on delivery)
  → Real Paystack transfer fires to farmer's verified account
  → [Paystack webhook confirms transfer landed]
  → SMS: payment received
```

### Escrow Expiry (automatic, via cron)
- Unpaid orders older than 60 minutes → auto-cancelled (no money moved yet)
- Locked escrows past 72 hours with no delivery confirmation →
  buyer automatically refunded, both parties notified by SMS

---

## Security
- PIN stored as SHA-256 hash (never plaintext)
- Release codes are single-use, hashed
- USSD sessions expire after 120 seconds
- Full audit trail on every action
- ACID-compliant database (WAL mode)
- Paystack webhooks verified via HMAC-SHA512 signature — unsigned/forged
  webhook calls are rejected, never trusted
- Farmer payout accounts verified by name (Paystack account resolution)
  before being saved, to prevent misdirected payouts

---

## All Farmer Portals
- Register (name, ANY crop/product by name, location, PIN)
- Update price listing
- Release escrow with code (triggers real payout)
- View transaction history
- Add/update verified bank or digital wallet payout account

## All Buyer Portals
- Search farmers by product — pick from what's currently listed, or type
  any product name to search
- Pay into escrow via real one-time bank transfer
- Post crop request for products with no current sellers

## Agent Portals
- Register as field agent
- Verify farmer KYC
- View recruit count
- Web product media workflow: `/agent/`
  - Agents log in with their existing phone/PIN.
  - Agents upload product photo, quantity, price, description, and farmer phone.
  - Listings enter `PENDING_REVIEW` until admin publishes them.

## Admin Operations URLs
- Buyer KYC: `/admin/kyc/`
- Logistics KYC and quote locking: `/admin/logistics/`
- Product publication review and notification audit: `/admin/products/`
- Dispute review: `/admin/disputes/`
- Audit log and readiness dashboard: `/admin/audit/`
- Readiness check: `/health/ready`

Set `DASHBOARD_ADMIN_USERNAME` and `DASHBOARD_ADMIN_PASSWORD` in production.
If `DASHBOARD_ADMIN_USERNAME` is blank, only the password is checked for
backward compatibility.

## Product Publishing Workflow
```
Farmer registers over USSD
  -> Agent verifies farmer
  -> Agent uploads product media/details at /agent/
  -> Listing status PENDING_REVIEW
  -> Operations reviews at /admin/products/
  -> Listing becomes PUBLISHED
  -> Buyers see product image/details
  -> Matching open buyer requests are notified by NotificationService
```

## Dispute Workflow
```
Buyer opens dispute from order page
  -> Order moves to DISPUTED
  -> Release is blocked while status is DISPUTED
  -> Operations reviews at /admin/disputes/
  -> Admin records resolution and next action
```

## Production Hardening
- `/health/ready` reports missing critical launch configuration.
- Set `ENFORCE_PRODUCTION_CONFIG=1` to fail startup when critical secrets
  such as `FLASK_SECRET_KEY`, `DASHBOARD_ADMIN_PASSWORD`, or
  `PAYSTACK_SECRET_KEY` are unsafe/missing.
- Sensitive routes have lightweight per-process rate limiting via
  `RATE_LIMIT_PER_MINUTE`.
- Security headers are applied to every response.
- KYC documents stay behind admin-authenticated document routes.
- Product media is served only from the configured product media folder.
- Run database backups from a scheduler:
  `python scripts/backup_db.py`

---

*Built by Promise Ukuere — Optimistic World Enterprise Ltd*
