"""Configurable milestone incentives for SowTrust field agents.

This module maintains an internal accounting ledger. It never holds customer
funds and never initiates a payment transfer.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from app.models.database import fetchall, fetchone, get_db
from app.utils.phone import normalize_phone


ACTIVE_AGENT_STATUSES = {"ACTIVE"}
BLOCKING_DISPUTE_STATUSES = {
    "OPEN", "UNDER_REVIEW", "REFUND_REQUIRED", "RESOLVED_BUYER",
}


def _audit(conn, actor: str, action: str, details: dict) -> None:
    conn.execute(
        "INSERT INTO audit_log(actor, action, details) VALUES (?, ?, ?)",
        (actor, action, json.dumps(details, sort_keys=True, default=str)),
    )


def _agent_by_reference(agent_reference):
    if agent_reference is None:
        return None
    if isinstance(agent_reference, int) or str(agent_reference).isdigit():
        return fetchone("SELECT * FROM agents WHERE id=?", (int(agent_reference),))
    phone = normalize_phone(str(agent_reference))
    if not phone:
        return None
    return fetchone(
        "SELECT * FROM agents WHERE normalized_phone=? OR phone=?",
        (phone, phone),
    )


def _farmer_by_reference(farmer_reference):
    if farmer_reference is None:
        return None
    if isinstance(farmer_reference, int) or str(farmer_reference).isdigit():
        return fetchone("SELECT * FROM farmers WHERE id=?", (int(farmer_reference),))
    phone = normalize_phone(str(farmer_reference))
    if not phone:
        return None
    return fetchone(
        "SELECT * FROM farmers WHERE normalized_phone=? OR phone=?",
        (phone, phone),
    )


class AgentIncentiveService:
    """Evaluate milestones and manage controlled ledger state changes."""

    @staticmethod
    def assign_relationship(agent_reference, farmer_reference, relationship_type: str,
                            created_by: str, verified: bool = False) -> dict:
        agent = _agent_by_reference(agent_reference)
        farmer = _farmer_by_reference(farmer_reference)
        relationship_type = (relationship_type or "").strip().upper()
        if not agent or not farmer:
            return {"ok": False, "error": "Agent or farmer was not found."}
        if relationship_type not in {"ONBOARDING", "VERIFICATION", "LISTING_SUPPORT"}:
            return {"ok": False, "error": "Unsupported agent-farmer relationship."}

        with get_db() as conn:
            conn.execute(
                """INSERT INTO agent_farmer_relationships
                   (agent_id, farmer_id, relationship_type, verified_at, created_by)
                   VALUES (?, ?, ?, CASE WHEN ? THEN datetime('now') END, ?)
                   ON CONFLICT(agent_id, farmer_id, relationship_type) DO UPDATE SET
                     active=1,
                     verified_at=CASE WHEN excluded.verified_at IS NOT NULL
                                      THEN excluded.verified_at
                                      ELSE agent_farmer_relationships.verified_at END,
                     ended_at=NULL""",
                (agent["id"], farmer["id"], relationship_type, 1 if verified else 0,
                 created_by),
            )
            relationship = conn.execute(
                """SELECT * FROM agent_farmer_relationships
                   WHERE agent_id=? AND farmer_id=? AND relationship_type=?""",
                (agent["id"], farmer["id"], relationship_type),
            ).fetchone()
            _audit(conn, created_by, "AGENT_FARMER_ATTRIBUTED", {
                "agent_id": agent["id"], "farmer_id": farmer["id"],
                "relationship_type": relationship_type,
            })
        return {"ok": True, "relationship": dict(relationship)}

    @staticmethod
    def record_farmer_verification(agent_reference, farmer_reference,
                                   actor: str | None = None) -> dict:
        """Atomically verify and attribute a farmer, then evaluate one reward."""
        agent = _agent_by_reference(agent_reference)
        farmer = _farmer_by_reference(farmer_reference)
        if not agent or not farmer:
            return {"ok": False, "error": "Agent or farmer was not found."}
        if not agent["is_active"] or agent["account_status"] not in ACTIVE_AGENT_STATUSES:
            return {"ok": False, "error": "This agent account is not active."}
        actor = actor or agent["phone"]
        was_verified = (
            str(farmer["verification_status"] or "").upper() == "VERIFIED"
            or str(farmer["kyc_status"] or "").upper() == "VERIFIED"
        )

        with get_db() as conn:
            conn.execute(
                """UPDATE farmers SET kyc_status='VERIFIED',
                       verification_status='VERIFIED', updated_at=datetime('now')
                   WHERE id=?""",
                (farmer["id"],),
            )
            conn.execute(
                """INSERT INTO agent_farmer_relationships
                   (agent_id, farmer_id, relationship_type, verified_at, created_by)
                   VALUES (?, ?, 'VERIFICATION', datetime('now'), ?)
                   ON CONFLICT(agent_id, farmer_id, relationship_type) DO UPDATE SET
                     active=1, verified_at=COALESCE(verified_at, datetime('now')), ended_at=NULL""",
                (agent["id"], farmer["id"], actor),
            )
            if not was_verified:
                conn.execute(
                    "UPDATE agents SET recruits=recruits+1, updated_at=datetime('now') WHERE id=?",
                    (agent["id"],),
                )
            _audit(conn, actor, "KYC_VERIFIED", {
                "agent_id": agent["id"], "farmer_id": farmer["id"],
                "farmer_phone": farmer["phone"], "already_verified": was_verified,
            })

        earning = AgentIncentiveService.evaluate_event(
            "FARMER_VERIFIED", farmer["id"], agent_id=agent["id"],
            source_reference=f"FARMER:{farmer['id']}",
            metadata={"verification_actor": actor},
        )
        return {"ok": True, "already_verified": was_verified, "earning": earning}

    @staticmethod
    def _resolve_agent(farmer, event_type: str, explicit_agent_id=None):
        if explicit_agent_id:
            return _agent_by_reference(explicit_agent_id)

        if event_type == "LISTING_APPROVED" and farmer["listed_by_agent_phone"]:
            listed_by = _agent_by_reference(farmer["listed_by_agent_phone"])
            if listed_by:
                return listed_by

        priorities = {
            "FARMER_VERIFIED": ("VERIFICATION", "ONBOARDING"),
            "LISTING_APPROVED": ("LISTING_SUPPORT", "VERIFICATION", "ONBOARDING"),
            "ORDER_COMPLETED": ("ONBOARDING", "VERIFICATION", "LISTING_SUPPORT"),
            "RETENTION_MILESTONE_REACHED": (
                "ONBOARDING", "VERIFICATION", "LISTING_SUPPORT"
            ),
        }.get(event_type, ("ONBOARDING", "VERIFICATION", "LISTING_SUPPORT"))
        placeholders = ",".join("?" for _ in priorities)
        relationship = fetchone(
            f"""SELECT a.* FROM agent_farmer_relationships r
                JOIN agents a ON a.id=r.agent_id
                WHERE r.farmer_id=? AND r.active=1
                  AND r.relationship_type IN ({placeholders})
                ORDER BY CASE r.relationship_type
                  {''.join(f' WHEN ? THEN {i} ' for i, _ in enumerate(priorities))}
                END, r.assigned_at ASC, r.id ASC LIMIT 1""",
            (farmer["id"], *priorities, *priorities),
        )
        return relationship

    @staticmethod
    def _risk_flags(agent, farmer, order_id=None) -> list[str]:
        flags = []
        agent_phone = agent["normalized_phone"] or normalize_phone(agent["phone"])
        farmer_phone = farmer["normalized_phone"] or normalize_phone(farmer["phone"])
        if agent_phone and agent_phone == farmer_phone:
            flags.append("SELF_ATTRIBUTION")
        if order_id:
            order = fetchone("SELECT buyer_phone FROM escrow_ledger WHERE txn_id=?", (order_id,))
            if order and normalize_phone(order["buyer_phone"]) == agent_phone:
                flags.append("POSSIBLE_SELF_DEALING")
        return flags

    @staticmethod
    def check_eligibility(policy, agent, farmer, event_type: str,
                          listing_id=None, order_id=None) -> dict:
        if not policy or not policy["enabled"]:
            return {"eligible": False, "reason": "Incentive policy is disabled or missing."}
        if policy["event_type"] != event_type:
            return {"eligible": False, "reason": "Event does not match the policy."}
        if not agent or not agent["is_active"] or agent["account_status"] != "ACTIVE":
            return {"eligible": False, "reason": "Attributed agent is not active."}
        if policy["effective_from"] and policy["effective_from"] > datetime.now(timezone.utc).isoformat():
            return {"eligible": False, "reason": "Policy is not effective yet."}
        if policy["effective_to"] and policy["effective_to"] < datetime.now(timezone.utc).isoformat():
            return {"eligible": False, "reason": "Policy has expired."}
        if event_type == "FARMER_VERIFIED" and not (
            farmer["verification_status"] == "VERIFIED" or farmer["kyc_status"] == "VERIFIED"
        ):
            return {"eligible": False, "reason": "Farmer is not verified."}
        if event_type == "LISTING_APPROVED" and not (
            farmer["listing_status"] == "PUBLISHED"
            and farmer["price"] and farmer["price"] > 0
            and farmer["crop"]
        ):
            return {"eligible": False, "reason": "Listing is not a qualifying publication."}

        occurrence = fetchone(
            """SELECT COUNT(*) AS total FROM agent_ledger_entries
               WHERE agent_id=? AND farmer_id=? AND incentive_code=?
                 AND reversal_of IS NULL AND status!='REJECTED'""",
            (agent["id"], farmer["id"], policy["incentive_code"]),
        )["total"]
        if occurrence >= policy["max_occurrences_per_farmer"]:
            return {"eligible": False, "reason": "Milestone already recorded."}
        return {
            "eligible": True,
            "risk_flags": AgentIncentiveService._risk_flags(agent, farmer, order_id),
        }

    @staticmethod
    def create_earning(policy, agent, farmer, event_type: str,
                       source_reference: str, listing_id=None, order_id=None,
                       metadata: dict | None = None, risk_flags: list[str] | None = None) -> dict:
        key = (
            f"AGENT:{agent['id']}:FARMER:{farmer['id']}:"
            f"INCENTIVE:{policy['incentive_code']}"
        )
        status = "UNDER_REVIEW" if risk_flags else "PENDING"
        try:
            with get_db() as conn:
                cursor = conn.execute(
                    """INSERT INTO agent_ledger_entries
                       (agent_id, farmer_id, listing_id, order_id, incentive_policy_id,
                        incentive_code, description, amount_kobo, currency, status,
                        source_event, source_reference, metadata_json, risk_flags,
                        idempotency_key)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        agent["id"], farmer["id"], listing_id, order_id, policy["id"],
                        policy["incentive_code"], policy["name"], policy["amount_kobo"],
                        policy["currency"], status, event_type, source_reference,
                        json.dumps(metadata or {}, sort_keys=True),
                        json.dumps(risk_flags or []), key,
                    ),
                )
                entry_id = cursor.lastrowid
                _audit(conn, "system", "INCENTIVE_CREATED", {
                    "entry_id": entry_id, "agent_id": agent["id"],
                    "farmer_id": farmer["id"], "listing_id": listing_id,
                    "order_id": order_id, "incentive_code": policy["incentive_code"],
                    "amount_kobo": policy["amount_kobo"], "new_status": status,
                    "source_reference": source_reference, "risk_flags": risk_flags or [],
                })
            return {"ok": True, "created": True, "entry_id": entry_id, "status": status}
        except sqlite3.IntegrityError as exc:
            if "idempotency_key" not in str(exc) and "UNIQUE constraint" not in str(exc):
                raise
            existing = fetchone(
                "SELECT id, status FROM agent_ledger_entries WHERE idempotency_key=?", (key,)
            )
            return {
                "ok": True, "created": False,
                "entry_id": existing["id"] if existing else None,
                "status": existing["status"] if existing else None,
            }

    @staticmethod
    def evaluate_event(event_type: str, farmer_reference, agent_id=None,
                       listing_id=None, order_id=None, source_reference: str = "",
                       metadata: dict | None = None) -> dict:
        event_type = (event_type or "").upper()
        farmer = _farmer_by_reference(farmer_reference)
        if not farmer:
            return {"ok": False, "created": False, "reason": "Farmer was not found."}
        policy = fetchone(
            """SELECT * FROM agent_incentive_policies
               WHERE event_type=? AND enabled=1
                 AND (effective_from IS NULL OR effective_from<=datetime('now'))
                 AND (effective_to IS NULL OR effective_to>=datetime('now'))
               ORDER BY id DESC LIMIT 1""",
            (event_type,),
        )
        if not policy:
            return {"ok": True, "created": False, "reason": "No active policy."}
        agent = AgentIncentiveService._resolve_agent(farmer, event_type, agent_id)
        eligibility = AgentIncentiveService.check_eligibility(
            policy, agent, farmer, event_type, listing_id, order_id
        )
        if not eligibility["eligible"]:
            return {"ok": True, "created": False, "reason": eligibility["reason"]}
        return AgentIncentiveService.create_earning(
            policy, agent, farmer, event_type,
            source_reference or f"{event_type}:FARMER:{farmer['id']}",
            listing_id=listing_id, order_id=order_id, metadata=metadata,
            risk_flags=eligibility.get("risk_flags"),
        )

    @staticmethod
    def approve_earning(entry_id: int, actor: str) -> dict:
        with get_db() as conn:
            row = conn.execute("SELECT * FROM agent_ledger_entries WHERE id=?", (entry_id,)).fetchone()
            if not row:
                return {"ok": False, "error": "Incentive entry not found."}
            if row["status"] == "APPROVED":
                return {"ok": True, "already_approved": True}
            if row["status"] not in {"PENDING", "UNDER_REVIEW"}:
                return {"ok": False, "error": f"A {row['status']} entry cannot be approved."}
            conn.execute(
                """UPDATE agent_ledger_entries SET status='APPROVED',
                       approved_at=datetime('now'), approved_by=? WHERE id=?""",
                (actor, entry_id),
            )
            _audit(conn, actor, "INCENTIVE_APPROVED", {
                "entry_id": entry_id, "agent_id": row["agent_id"],
                "farmer_id": row["farmer_id"], "previous_status": row["status"],
                "new_status": "APPROVED", "amount_kobo": row["amount_kobo"],
            })
        return {"ok": True}

    @staticmethod
    def reject_earning(entry_id: int, actor: str, reason: str) -> dict:
        if not (reason or "").strip():
            return {"ok": False, "error": "A rejection reason is required."}
        with get_db() as conn:
            row = conn.execute("SELECT * FROM agent_ledger_entries WHERE id=?", (entry_id,)).fetchone()
            if not row:
                return {"ok": False, "error": "Incentive entry not found."}
            if row["status"] not in {"PENDING", "UNDER_REVIEW", "APPROVED"}:
                return {"ok": False, "error": f"A {row['status']} entry cannot be rejected."}
            conn.execute(
                """UPDATE agent_ledger_entries SET status='REJECTED',
                       rejected_at=datetime('now'), rejected_by=?, rejection_reason=?
                   WHERE id=?""",
                (actor, reason.strip(), entry_id),
            )
            _audit(conn, actor, "INCENTIVE_REJECTED", {
                "entry_id": entry_id, "agent_id": row["agent_id"],
                "farmer_id": row["farmer_id"], "previous_status": row["status"],
                "new_status": "REJECTED", "amount_kobo": row["amount_kobo"],
                "reason": reason.strip(),
            })
        return {"ok": True}

    @staticmethod
    def reverse_earning(entry_id: int, actor: str, reason: str) -> dict:
        if not (reason or "").strip():
            return {"ok": False, "error": "A reversal reason is required."}
        with get_db() as conn:
            row = conn.execute("SELECT * FROM agent_ledger_entries WHERE id=?", (entry_id,)).fetchone()
            if not row:
                return {"ok": False, "error": "Incentive entry not found."}
            if row["reversal_of"] or row["status"] in {"REJECTED", "REVERSED"}:
                return {"ok": False, "error": "This entry cannot be reversed."}
            if row["status"] == "PAYABLE" and row["payout_batch_id"]:
                return {"ok": False, "error": "Cancel or fail the payout batch before reversal."}
            existing = conn.execute(
                "SELECT id FROM agent_ledger_entries WHERE reversal_of=?", (entry_id,)
            ).fetchone()
            if existing:
                return {"ok": True, "already_reversed": True, "reversal_id": existing["id"]}

            reversal_status = "APPROVED" if row["paid_at"] else "REVERSED"
            cursor = conn.execute(
                """INSERT INTO agent_ledger_entries
                   (agent_id, farmer_id, listing_id, order_id, incentive_policy_id,
                    incentive_code, description, amount_kobo, currency, status,
                    source_event, source_reference, approved_at, approved_by,
                    reversal_of, metadata_json, risk_flags, idempotency_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'INCENTIVE_REVERSED', ?,
                           CASE WHEN ?='APPROVED' THEN datetime('now') END,
                           CASE WHEN ?='APPROVED' THEN ? END, ?, ?, '[]', ?)""",
                (
                    row["agent_id"], row["farmer_id"], row["listing_id"], row["order_id"],
                    row["incentive_policy_id"], row["incentive_code"],
                    f"Reversal: {row['description']}", -row["amount_kobo"], row["currency"],
                    reversal_status, f"REVERSAL:{entry_id}", reversal_status,
                    reversal_status, actor, entry_id,
                    json.dumps({"reason": reason.strip(), "original_entry_id": entry_id}),
                    f"REVERSAL:{entry_id}",
                ),
            )
            reversal_id = cursor.lastrowid
            conn.execute("UPDATE agent_ledger_entries SET status='REVERSED' WHERE id=?", (entry_id,))
            _audit(conn, actor, "INCENTIVE_REVERSED", {
                "entry_id": entry_id, "reversal_id": reversal_id,
                "agent_id": row["agent_id"], "farmer_id": row["farmer_id"],
                "previous_status": row["status"], "new_status": "REVERSED",
                "amount_kobo": row["amount_kobo"], "reason": reason.strip(),
            })
        return {"ok": True, "reversal_id": reversal_id}

    @staticmethod
    def reverse_order_incentives(order_id: str, actor: str = "system",
                                 reason: str = "Order payment or settlement reversed") -> dict:
        entries = fetchall(
            """SELECT id FROM agent_ledger_entries
               WHERE order_id=? AND reversal_of IS NULL
                 AND status NOT IN ('REJECTED', 'REVERSED')""",
            (order_id,),
        )
        reversed_ids = []
        for entry in entries:
            result = AgentIncentiveService.reverse_earning(entry["id"], actor, reason)
            if result.get("ok"):
                reversed_ids.append(entry["id"])
        return {"ok": True, "reversed_entry_ids": reversed_ids}

    @staticmethod
    def evaluate_completed_order(order_id: str) -> dict:
        """Mark an order complete only after both real settlements have succeeded."""
        order = fetchone("SELECT * FROM escrow_ledger WHERE txn_id=?", (order_id,))
        if not order:
            return {"ok": False, "completed": False, "reason": "Order not found."}
        logistics = fetchone("SELECT * FROM logistics_log WHERE txn_id=?", (order_id,))
        dispute = fetchone(
            """SELECT status FROM disputes WHERE txn_id=?
               AND status IN ('OPEN', 'UNDER_REVIEW', 'REFUND_REQUIRED', 'RESOLVED_BUYER')
               ORDER BY id DESC LIMIT 1""",
            (order_id,),
        )
        if (
            not order["payment_confirmed_at"]
            or order["payout_status"] != "success"
            or order["status"] in {"CANCELLED", "EXPIRED", "DISPUTED", "PAYOUT_FAILED"}
            or not logistics
            or logistics["status"] != "DELIVERED"
            or logistics["payout_status"] != "success"
            or dispute
        ):
            return {"ok": True, "completed": False, "reason": "Settlement is not fully eligible."}

        newly_completed = False
        with get_db() as conn:
            changed = conn.execute(
                """UPDATE escrow_ledger SET status='COMPLETED',
                       completed_at=COALESCE(completed_at, datetime('now'))
                   WHERE txn_id=? AND completed_at IS NULL""",
                (order_id,),
            ).rowcount
            newly_completed = bool(changed)
            if changed:
                _audit(conn, "system", "ORDER_COMPLETED", {
                    "order_id": order_id, "farmer_phone": order["farmer_phone"],
                    "payment_confirmed_at": order["payment_confirmed_at"],
                })

        farmer = _farmer_by_reference(order["farmer_phone"])
        completed = fetchone(
            """SELECT COUNT(*) AS total, MIN(completed_at) AS first_completed_at,
                      MAX(completed_at) AS latest_completed_at
               FROM escrow_ledger WHERE farmer_phone=? AND completed_at IS NOT NULL""",
            (order["farmer_phone"],),
        )
        rewards = []
        if completed["total"] >= 1:
            rewards.append(AgentIncentiveService.evaluate_event(
                "ORDER_COMPLETED", farmer["id"], order_id=order_id,
                source_reference=f"ORDER:{order_id}",
                metadata={"completed_transaction_count": completed["total"]},
            ))

        retention_policy = fetchone(
            """SELECT * FROM agent_incentive_policies
               WHERE event_type='RETENTION_MILESTONE_REACHED' AND enabled=1
               ORDER BY id DESC LIMIT 1"""
        )
        if retention_policy and completed["total"] >= (
            retention_policy["qualifying_transaction_count"] or 2
        ):
            inside_period = True
            if retention_policy["qualifying_period_days"]:
                period = fetchone(
                    "SELECT julianday(?) - julianday(?) AS elapsed_days",
                    (completed["latest_completed_at"], completed["first_completed_at"]),
                )["elapsed_days"]
                inside_period = period is not None and period <= retention_policy["qualifying_period_days"]
            if inside_period:
                rewards.append(AgentIncentiveService.evaluate_event(
                    "RETENTION_MILESTONE_REACHED", farmer["id"], order_id=order_id,
                    source_reference=f"RETENTION:FARMER:{farmer['id']}",
                    metadata={"completed_transaction_count": completed["total"]},
                ))
        return {"ok": True, "completed": True, "newly_completed": newly_completed, "rewards": rewards}

    @staticmethod
    def get_agent_summary(agent_reference) -> dict:
        agent = _agent_by_reference(agent_reference)
        if not agent:
            return {}
        row = fetchone(
            """SELECT
                 COALESCE(SUM(CASE WHEN status IN ('PENDING','UNDER_REVIEW') THEN amount_kobo ELSE 0 END),0) pending_kobo,
                 COALESCE(SUM(CASE WHEN status='APPROVED' THEN amount_kobo ELSE 0 END),0) approved_kobo,
                 COALESCE(SUM(CASE WHEN status='PAYABLE' THEN amount_kobo ELSE 0 END),0) payable_kobo,
                 COALESCE(SUM(CASE WHEN paid_at IS NOT NULL AND amount_kobo>0 THEN amount_kobo ELSE 0 END),0) paid_kobo,
                 ABS(COALESCE(SUM(CASE WHEN amount_kobo<0 THEN amount_kobo ELSE 0 END),0)) reversed_kobo,
                 COALESCE(SUM(CASE WHEN amount_kobo>0 AND status!='REJECTED' THEN amount_kobo ELSE 0 END),0) lifetime_gross_kobo,
                 COALESCE(SUM(CASE WHEN status!='REJECTED' THEN amount_kobo ELSE 0 END),0) lifetime_net_kobo
               FROM agent_ledger_entries WHERE agent_id=?""",
            (agent["id"],),
        )
        summary = dict(row)
        summary["agent_id"] = agent["id"]
        summary["currency"] = "NGN"
        return summary

    @staticmethod
    def list_entries(agent_id=None, farmer_id=None, status="", incentive_code="",
                     flagged_only=False, limit=200) -> list[dict]:
        clauses, params = ["1=1"], []
        if agent_id:
            clauses.append("l.agent_id=?")
            params.append(agent_id)
        if farmer_id:
            clauses.append("l.farmer_id=?")
            params.append(farmer_id)
        if status:
            clauses.append("l.status=?")
            params.append(status.upper())
        if incentive_code:
            clauses.append("l.incentive_code=?")
            params.append(incentive_code.upper())
        if flagged_only:
            clauses.append("COALESCE(l.risk_flags, '[]')!='[]'")
        params.append(int(limit))
        rows = fetchall(
            f"""SELECT l.*, a.name AS agent_name, a.phone AS agent_phone,
                       f.name AS farmer_name, f.phone AS farmer_phone
                FROM agent_ledger_entries l
                JOIN agents a ON a.id=l.agent_id
                LEFT JOIN farmers f ON f.id=l.farmer_id
                WHERE {' AND '.join(clauses)}
                ORDER BY l.created_at DESC, l.id DESC LIMIT ?""",
            tuple(params),
        )
        return [dict(row) for row in rows]

    @staticmethod
    def update_policy(policy_id: int, actor: str, **changes) -> dict:
        allowed = {
            "amount_kobo", "enabled", "effective_from", "effective_to",
            "max_occurrences_per_farmer", "requires_admin_review",
            "qualifying_transaction_count", "qualifying_period_days",
        }
        updates = {key: value for key, value in changes.items() if key in allowed}
        if not updates:
            return {"ok": False, "error": "No policy changes were supplied."}
        if "amount_kobo" in updates and int(updates["amount_kobo"]) < 0:
            return {"ok": False, "error": "Policy amount cannot be negative."}
        assignments = ", ".join(f"{key}=?" for key in updates)
        with get_db() as conn:
            current = conn.execute(
                "SELECT * FROM agent_incentive_policies WHERE id=?", (policy_id,)
            ).fetchone()
            if not current:
                return {"ok": False, "error": "Incentive policy not found."}
            conn.execute(
                f"UPDATE agent_incentive_policies SET {assignments}, updated_at=datetime('now') WHERE id=?",
                (*updates.values(), policy_id),
            )
            _audit(conn, actor, "INCENTIVE_POLICY_UPDATED", {
                "policy_id": policy_id, "incentive_code": current["incentive_code"],
                "changes": updates,
            })
        return {"ok": True}


agent_incentive_service = AgentIncentiveService()
