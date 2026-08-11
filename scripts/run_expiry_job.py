"""
Sowtrust — Escrow Expiry Job.

Run this periodically (every 15 minutes recommended) via Railway's
Cron Job service — see README_APPLY_THIS.md for setup steps.

Deliberately a separate script, NOT a background thread inside the
Flask app: your Procfile runs `gunicorn -w 4`, meaning 4 separate
worker processes. An in-process scheduler would start 4 duplicate
timers, each trying to expire/refund the same transactions.

Usage:  python scripts/run_expiry_job.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.escrow_service import expire_stale_escrows

if __name__ == "__main__":
    results = expire_stale_escrows()
    print(f"[Sowtrust Expiry Job] cancelled={results['cancelled']} "
          f"refunded={results['refunded']} refund_failed={results['refund_failed']}")
    if results["refund_failed"] > 0:
        print("⚠️  One or more refunds FAILED — check audit_log table "
              "(action='REFUND_FAILED') and follow up manually.")
        sys.exit(1)  # non-zero exit so Railway Cron flags the run as failed
