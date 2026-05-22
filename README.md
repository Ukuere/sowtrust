# 🌾 AgriHub Global v6.0
**USSD-Based Agricultural Escrow & Logistics Ecosystem**

---

## Folder Structure
```
agrihub/
├── app/
│   ├── __init__.py           # Flask app factory
│   ├── routes/
│   │   └── ussd.py           # All USSD portal logic
│   ├── models/
│   │   └── database.py       # DB connection manager
│   ├── services/
│   │   ├── escrow_service.py # Escrow lock/release engine
│   │   └── sms_service.py    # Africa's Talking SMS wrapper
│   └── utils/
│       └── security.py       # PIN hashing, session mgmt
├── dashboard/
│   └── app.py                # Streamlit CEO console
├── migrations/
│   └── init_db.py            # Database schema setup
├── config/
│   └── settings.py           # Central config (env vars)
├── scripts/
│   └── seed_demo_data.py     # Demo data seeder
├── tests/
│   └── test_ussd.py          # Test suite
├── run.py                    # Flask entry point
├── Procfile                  # Deployment (Heroku/Railway)
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Quick Start (Local)

### 1. Clone & Setup
```bash
git clone <your-repo>
cd agrihub
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env — add your Africa's Talking API key
```

### 3. Initialise Database
```bash
python migrations/init_db.py
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

### 7. Run Dashboard
```bash
streamlit run dashboard/app.py
# Opens at http://localhost:8501
# Default password: changeme (set in .env)
```

---

## Production Deployment (Railway / Render / VPS)

### Railway
```bash
railway login
railway init
railway up
# Set env vars in Railway dashboard
```

### Gunicorn (VPS)
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
| Main | 4 | Wallet / PIN |
| Main | 5 | Withdraw Funds |
| Main | 6 | Agent Portal |

### Escrow Flow
```
Buyer dials *709# → 2 → 2
  → Select crop → Farmer phone → Qty
  → Confirm → ESCROW LOCKED
  → SMS to farmer (funds locked)
  → Release code sent to buyer

Farmer dials *709# → 1 → 3
  → Enter PIN → Enter release code
  → PAYMENT RELEASED to wallet
```

---

## Security
- PIN stored as SHA-256 hash (never plaintext)
- Release codes are single-use, hashed
- USSD sessions expire after 120 seconds
- Full audit trail on every action
- ACID-compliant database (WAL mode)

---

## All Farmer Portals
- Register (name, crop, location, PIN)
- Update price listing
- Release escrow with code
- View transaction history

## All Buyer Portals
- Search farmers by crop (sorted cheapest first)
- Lock escrow payment
- Post crop request

## Agent Portals
- Register as field agent
- Verify farmer KYC
- View recruit count

---

*Built by Promise Ukuere — Optimistic World Enterprise Ltd*
