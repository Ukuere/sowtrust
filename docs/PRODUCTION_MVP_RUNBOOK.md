# SowTrust Production MVP Runbook

This document is the operating guide for the unified SowTrust MVP. The canonical public domain is `sowtrust.com`; the Operations console is `ops.sowtrust.com`.

## 1. Architecture

```text
Users / Africa's Talking / Paystack
                  |
                  v
        sowtrust.com (Flask)
        - Public marketplace
        - Buyer, agent, logistics portals
        - USSD callback and Paystack webhook
        - Staff administration
        - Authoritative business rules and database
                  |
          SQLite persistent volume
          /app/data/sowtrust.db
                  |
        Private S3/R2 object storage
        KYC documents and product media

ops.sowtrust.com (Streamlit)
        |
        | Bearer DASHBOARD_API_TOKEN
        v
sowtrust.com/api/internal/dashboard/*
```

Only the Flask service reads or writes the production database. Streamlit must not mount, copy, or open the SQLite file.

## 2. Root Causes Fixed

- Flask and Streamlit previously used separate Railway filesystems, so matching `DATABASE_PATH` values still referred to different files.
- Phone numbers were stored in role-specific tables without one canonical identity or normalization policy.
- Web and USSD onboarding created disconnected records and could lose multi-step USSD state across Gunicorn workers.
- Product visibility was coupled to KYC approval, hiding valid unverified listings.
- Checkout could initialize Paystack before a delivery quote existed.
- Admin routes shared one Basic Auth password and did not attribute actions to individual staff users.
- Payment amounts used floating-point values and webhook payloads were trusted without server-side transaction verification.
- Uploaded documents were local-only and validated mainly by filename.

## 3. Implemented Invariants

- One normalized Nigerian phone identity can hold multiple roles through `users` and `user_roles`.
- Existing role rows are linked, not duplicated. Ambiguous records are written to `identity_migration_issues` for manual review.
- OTP codes are hashed, expire, have resend/attempt limits, and are required to activate an existing USSD identity for web access.
- USSD sessions are database-backed and work across Gunicorn workers.
- Published listings remain visible while verification is displayed separately.
- Listings without media use the SowTrust produce placeholder. Agents can later upload authorized JPG/PNG media.
- Operations must select a verified provider and lock a quote before payment.
- The buyer must explicitly accept the locked quote before Paystack is called.
- Money calculations use integer kobo internally. Webhooks require a valid HMAC signature, a successful Paystack verification call, the expected NGN currency, reference, and exact kobo amount.
- A lower/equal provider replacement preserves the locked buyer and Paystack amount. A higher replacement is blocked after payment initialization; before payment it requires explicit buyer approval.
- KYC documents are private and staff-protected. S3/R2 downloads use short-lived signed URLs.
- Staff use individual database-backed accounts with `ADMIN`, `OPERATIONS`, or `REVIEWER` roles.
- Streamlit receives a limited API snapshot without password hashes, PIN hashes, OTP hashes, KYC document paths, or payment secrets.

## 4. Main Routes

Public:

- `/` homepage
- `/marketplace`
- `/farmers`
- `/support`, `/faq`, `/privacy`, `/terms`
- `/buyers`, `/buyers/register`, `/buyers/login`, `/buyers/dashboard`
- `/agents`, `/agents/register`, `/agents/login`, `/agents/dashboard`
- `/logistics`, `/logistics/register`, `/logistics/login`, `/logistics/dashboard`
- `/track-orders`

Operations:

- `/staff/login`
- `/admin`
- `/admin/users`
- `/admin/kyc/`
- `/admin/logistics/`
- `/admin/products/`
- `/admin/disputes/`
- `/admin/payments`
- `/admin/escrow`
- `/ceo-console`

Integrations and health:

- `POST /ussd`
- `POST /webhooks/paystack`
- `GET /health`
- `GET /health/ready`

## 5. Local Windows Setup

Use the existing virtual environment if it already works:

```powershell
cd C:\dev\agrihub
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` with test credentials. Then migrate and run:

```powershell
python scripts\run_all_migrations.py
python scripts\report_identity_duplicates.py
python run.py
```

Open:

- `http://127.0.0.1:5000/`
- `http://127.0.0.1:5000/health/ready`
- `http://127.0.0.1:5000/staff/login`

Run Streamlit in a second terminal with the same `DASHBOARD_API_TOKEN`:

```powershell
cd C:\dev\agrihub
.\venv\Scripts\Activate.ps1
$env:BACKEND_API_URL="http://127.0.0.1:5000"
streamlit run dashboard\app.py --server.address 0.0.0.0 --server.port 8501
```

## 6. Existing Database Upgrade

1. Stop writes or enable a short maintenance window.
2. Create a consistent backup:

```powershell
python scripts\backup_db.py
```

3. Run every migration through the orchestrator only:

```powershell
python scripts\run_all_migrations.py
```

4. Run the identity report:

```powershell
python scripts\report_identity_duplicates.py
```

5. Resolve every row in `identity_migration_issues` before launch. Never automatically merge two ambiguous records.
6. Run the migration command a second time. It must finish successfully because every migration is idempotent.

## 7. Railway Web Service

Mount one persistent volume at `/app/data` on the Flask service.

Start command:

```text
python scripts/run_all_migrations.py && gunicorn -w 4 -b 0.0.0.0:$PORT "run:app"
```

Required variables:

```text
FLASK_ENV=production
FLASK_SECRET_KEY=<at least 32 random bytes>
PUBLIC_BASE_URL=https://sowtrust.com
CANONICAL_HOST=sowtrust.com
DATABASE_PATH=/app/data/sowtrust.db
PAYSTACK_SECRET_KEY=<live or test secret for the environment>
PAYSTACK_PUBLIC_KEY=<matching public key>
AT_USERNAME=<Africa's Talking username>
AT_API_KEY=<Africa's Talking key>
USSD_MODE=SANDBOX
USSD_PUBLIC_CODE=
DASHBOARD_ADMIN_USERNAME=<first admin username>
DASHBOARD_ADMIN_PASSWORD=<unique password, at least 12 characters>
DASHBOARD_API_TOKEN=<at least 32 random bytes>
BACKEND_API_URL=https://sowtrust.com
CEO_CONSOLE_URL=https://ops.sowtrust.com
ENFORCE_PRODUCTION_CONFIG=1
RATE_LIMIT_PER_MINUTE=120
SENTRY_DSN=<production Sentry DSN>
```

Recommended private object storage variables:

```text
STORAGE_BACKEND=r2
UPLOAD_FOLDER=/app/data/uploads
OBJECT_STORAGE_BUCKET=<private bucket>
OBJECT_STORAGE_REGION=auto
OBJECT_STORAGE_ENDPOINT=<R2 or S3 endpoint>
OBJECT_STORAGE_ACCESS_KEY=<access key>
OBJECT_STORAGE_SECRET_KEY=<secret key>
OBJECT_STORAGE_PREFIX=sowtrust
BACKUP_DIR=/app/data/backups
BACKUP_RETENTION_COUNT=14
BACKUP_TO_OBJECT_STORAGE=1
```

Do not expose the object-storage bucket publicly.

## 8. Railway Streamlit Service

The dashboard service needs no database volume and no `DATABASE_PATH`.

Start command:

```text
streamlit run dashboard/app.py --server.address 0.0.0.0 --server.port $PORT --server.headless true
```

Variables:

```text
BACKEND_API_URL=https://sowtrust.com
DASHBOARD_API_TOKEN=<exact same token as Flask>
DASHBOARD_ADMIN_USERNAME=<console login username>
DASHBOARD_ADMIN_PASSWORD=<console login password>
```

## 9. Domains and Callbacks

Railway custom domains:

- Flask service: `sowtrust.com` and `www.sowtrust.com`
- Streamlit service: `ops.sowtrust.com`

The Railway target port is the service `$PORT`; do not hardcode `5000` or `8080` in DNS.

Provider callbacks:

```text
Africa's Talking USSD callback: https://sowtrust.com/ussd
Paystack webhook:             https://sowtrust.com/webhooks/paystack
```

Keep `USSD_MODE=SANDBOX` and hide the public dial code until Africa's Talking confirms the live code.

## 10. Staff Accounts

The first admin can bootstrap from Railway variables. Create separate accounts afterward:

```powershell
$env:STAFF_CREATE_PASSWORD="a-unique-password-of-12-or-more-characters"
python scripts\create_staff_user.py --username operations1 --role OPERATIONS
python scripts\create_staff_user.py --username reviewer1 --role REVIEWER
Remove-Item Env:STAFF_CREATE_PASSWORD
```

Never share one staff account. Disable a departed user by setting `staff_users.is_active=0` through a controlled administrative database action.

## 11. Backups and Monitoring

- Run `python scripts/backup_db.py` from a Railway cron service at least daily.
- Keep one copy off the Railway volume in private object storage.
- Test restore monthly into a staging service.
- Configure Sentry and alert on 5xx errors and failed Paystack webhook verification.
- Monitor `/health/ready`; launch only when `ready` is `true` and all warnings have an accepted owner.
- Review `payment_webhook_events`, `audit_log`, `notifications`, `identity_migration_issues`, and failed payouts daily during launch week.

## 12. Release Procedure

1. Deploy to staging with Paystack test keys and Africa's Talking sandbox.
2. Back up the production database.
3. Push the reviewed commit to the Railway-connected branch.
4. Confirm the web deployment runs all migrations and starts Gunicorn.
5. Confirm `https://sowtrust.com/health/ready` returns `ready: true`.
6. Confirm public routes, buyer login, staff login, direct URL refresh, and branded 404 behavior.
7. Complete one real Paystack test transaction through quote, buyer acceptance, webhook confirmation, delivery code, and settlement initiation.
8. Confirm Streamlit reads the same users and transactions shown by Flask staff pages.
9. Switch integration credentials to live only after the test evidence is recorded.

## 13. Rollback

Application rollback:

1. Stop new transactions.
2. Redeploy the previous Railway commit.
3. Do not restore an old database merely to roll back code; migrations are additive and older code should ignore new tables/columns.

Database recovery:

1. Stop the Flask service before replacing SQLite.
2. Preserve the failed database for investigation.
3. Restore the latest integrity-checked backup to `/app/data/sowtrust.db`.
4. Run `python scripts/run_all_migrations.py`.
5. Start Flask and verify `/health/ready` before reopening traffic.

## 14. Current Scale Boundary

SQLite on one persistent Railway volume is acceptable for a controlled, low-volume MVP launch. It is not a high-availability architecture. Move to managed PostgreSQL before horizontal Flask scaling, sustained concurrent write traffic, or a requirement for automatic failover. The Streamlit API separation and centralized service layer make that later migration possible without redesigning the user portals.
