"""Manual, periodic payout batching for approved agent incentives."""
from __future__ import annotations

import json

from app.models.database import fetchall, fetchone, get_db


def _audit(conn, actor, action, details):
    conn.execute(
        "INSERT INTO audit_log(actor, action, details) VALUES (?, ?, ?)",
        (actor, action, json.dumps(details, sort_keys=True, default=str)),
    )


class AgentPayoutService:
    @staticmethod
    def calculate_agent_payout(agent_id: int, period_end: str | None = None) -> dict:
        params = [agent_id]
        date_clause = ""
        if period_end:
            date_clause = "AND date(created_at)<=date(?)"
            params.append(period_end)
        row = fetchone(
            f"""SELECT
                  COALESCE(SUM(CASE WHEN amount_kobo>0 THEN amount_kobo ELSE 0 END),0) gross_kobo,
                  ABS(COALESCE(SUM(CASE WHEN amount_kobo<0 THEN amount_kobo ELSE 0 END),0)) deductions_kobo,
                  COALESCE(SUM(amount_kobo),0) net_kobo
                FROM agent_ledger_entries
                WHERE agent_id=? AND status='APPROVED' AND payout_batch_id IS NULL
                {date_clause}""",
            tuple(params),
        )
        return dict(row)

    @staticmethod
    def create_batch(period_start: str, period_end: str, frequency: str,
                     actor: str, notes: str = "") -> dict:
        frequency = (frequency or "WEEKLY").upper()
        if frequency not in {"WEEKLY", "BIWEEKLY", "MONTHLY", "CUSTOM"}:
            return {"ok": False, "error": "Unsupported payout frequency."}
        if not period_start or not period_end or period_start > period_end:
            return {"ok": False, "error": "Enter a valid payout period."}

        with get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """SELECT agent_id,
                          SUM(CASE WHEN amount_kobo>0 THEN amount_kobo ELSE 0 END) gross_kobo,
                          ABS(SUM(CASE WHEN amount_kobo<0 THEN amount_kobo ELSE 0 END)) deductions_kobo,
                          SUM(amount_kobo) net_kobo
                   FROM agent_ledger_entries
                   WHERE status='APPROVED' AND payout_batch_id IS NULL
                     AND date(created_at)<=date(?)
                   GROUP BY agent_id HAVING SUM(amount_kobo)>0""",
                (period_end,),
            ).fetchall()
            if not rows:
                return {"ok": False, "error": "No approved earnings are ready for this period."}
            total_kobo = sum(row["net_kobo"] for row in rows)
            cursor = conn.execute(
                """INSERT INTO agent_payout_batches
                   (period_start, period_end, frequency, status, total_amount_kobo,
                    currency, created_by, notes)
                   VALUES (?, ?, ?, 'DRAFT', ?, 'NGN', ?, ?)""",
                (period_start, period_end, frequency, total_kobo, actor, notes or None),
            )
            batch_id = cursor.lastrowid
            for row in rows:
                conn.execute(
                    """INSERT INTO agent_payout_items
                       (payout_batch_id, agent_id, gross_approved_earnings_kobo,
                        deductions_kobo, net_payout_amount_kobo)
                       VALUES (?, ?, ?, ?, ?)""",
                    (batch_id, row["agent_id"], row["gross_kobo"],
                     row["deductions_kobo"], row["net_kobo"]),
                )
                conn.execute(
                    """UPDATE agent_ledger_entries
                       SET status='PAYABLE', payout_batch_id=?
                       WHERE agent_id=? AND status='APPROVED' AND payout_batch_id IS NULL
                         AND date(created_at)<=date(?)""",
                    (batch_id, row["agent_id"], period_end),
                )
            _audit(conn, actor, "PAYOUT_BATCH_CREATED", {
                "batch_id": batch_id, "period_start": period_start,
                "period_end": period_end, "frequency": frequency,
                "total_amount_kobo": total_kobo, "agent_count": len(rows),
            })
        return {"ok": True, "batch_id": batch_id, "total_amount_kobo": total_kobo}

    @staticmethod
    def approve_batch(batch_id: int, actor: str) -> dict:
        with get_db() as conn:
            batch = conn.execute("SELECT * FROM agent_payout_batches WHERE id=?", (batch_id,)).fetchone()
            if not batch:
                return {"ok": False, "error": "Payout batch not found."}
            if batch["status"] == "APPROVED":
                return {"ok": True, "already_approved": True}
            if batch["status"] not in {"DRAFT", "UNDER_REVIEW"}:
                return {"ok": False, "error": f"A {batch['status']} batch cannot be approved."}
            conn.execute(
                """UPDATE agent_payout_batches SET status='APPROVED',
                       approved_by=?, approved_at=datetime('now') WHERE id=?""",
                (actor, batch_id),
            )
            _audit(conn, actor, "PAYOUT_APPROVED", {
                "batch_id": batch_id, "previous_status": batch["status"],
                "new_status": "APPROVED", "total_amount_kobo": batch["total_amount_kobo"],
            })
        return {"ok": True}

    @staticmethod
    def mark_paid(batch_id: int, actor: str, payment_reference: str) -> dict:
        if not (payment_reference or "").strip():
            return {"ok": False, "error": "A payment reference is required."}
        with get_db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            batch = conn.execute("SELECT * FROM agent_payout_batches WHERE id=?", (batch_id,)).fetchone()
            if not batch:
                return {"ok": False, "error": "Payout batch not found."}
            if batch["status"] == "PAID":
                return {"ok": True, "already_paid": True}
            if batch["status"] not in {"APPROVED", "PROCESSING"}:
                return {"ok": False, "error": "Approve the payout batch before confirming payment."}
            reference = payment_reference.strip()
            conn.execute(
                """UPDATE agent_payout_batches SET status='PAID', paid_at=datetime('now'),
                       payment_reference=? WHERE id=?""",
                (reference, batch_id),
            )
            conn.execute(
                """UPDATE agent_payout_items SET status='PAID', paid_at=datetime('now'),
                       payment_reference=? WHERE payout_batch_id=?""",
                (reference, batch_id),
            )
            conn.execute(
                """UPDATE agent_ledger_entries SET status='PAID', paid_at=datetime('now')
                   WHERE payout_batch_id=? AND status='PAYABLE'""",
                (batch_id,),
            )
            _audit(conn, actor, "PAYOUT_PAID", {
                "batch_id": batch_id, "previous_status": batch["status"],
                "new_status": "PAID", "total_amount_kobo": batch["total_amount_kobo"],
                "payment_reference": reference,
            })
        return {"ok": True}

    @staticmethod
    def mark_failed(batch_id: int, actor: str, reason: str) -> dict:
        if not (reason or "").strip():
            return {"ok": False, "error": "A failure reason is required."}
        with get_db() as conn:
            batch = conn.execute("SELECT * FROM agent_payout_batches WHERE id=?", (batch_id,)).fetchone()
            if not batch:
                return {"ok": False, "error": "Payout batch not found."}
            if batch["status"] == "PAID":
                return {"ok": False, "error": "A paid batch cannot be failed."}
            conn.execute(
                "UPDATE agent_payout_batches SET status='FAILED', notes=? WHERE id=?",
                (reason.strip(), batch_id),
            )
            conn.execute(
                "UPDATE agent_payout_items SET status='FAILED' WHERE payout_batch_id=?",
                (batch_id,),
            )
            conn.execute(
                """UPDATE agent_ledger_entries SET status='APPROVED', payout_batch_id=NULL
                   WHERE payout_batch_id=? AND status='PAYABLE'""",
                (batch_id,),
            )
            _audit(conn, actor, "PAYOUT_FAILED", {
                "batch_id": batch_id, "previous_status": batch["status"],
                "new_status": "FAILED", "total_amount_kobo": batch["total_amount_kobo"],
                "reason": reason.strip(),
            })
        return {"ok": True}

    @staticmethod
    def list_batches(limit: int = 50) -> list[dict]:
        rows = fetchall(
            """SELECT b.*, COUNT(i.id) AS agent_count
               FROM agent_payout_batches b
               LEFT JOIN agent_payout_items i ON i.payout_batch_id=b.id
               GROUP BY b.id ORDER BY b.created_at DESC, b.id DESC LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in rows]

    @staticmethod
    def current_agent_payout(agent_id: int):
        row = fetchone(
            """SELECT b.*, i.net_payout_amount_kobo, i.status AS item_status
               FROM agent_payout_items i JOIN agent_payout_batches b ON b.id=i.payout_batch_id
               WHERE i.agent_id=? ORDER BY b.created_at DESC, b.id DESC LIMIT 1""",
            (agent_id,),
        )
        return dict(row) if row else None


agent_payout_service = AgentPayoutService()
