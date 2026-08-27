"""Acceptance tests for SowTrust's internal agent incentive ledger."""
from unittest.mock import patch

import pytest


@pytest.fixture
def incentives(tmp_path):
    from config.settings import config

    original = {
        "DATABASE_PATH": config.DATABASE_PATH,
        "ENV": config.ENV,
        "CANONICAL_HOST": config.CANONICAL_HOST,
    }
    config.DATABASE_PATH = str(tmp_path / "agent-incentives.db")
    config.ENV = "testing"
    config.CANONICAL_HOST = ""

    from migrations.init_db import init_db
    init_db()
    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    yield app

    for key, value in original.items():
        setattr(config, key, value)


def _seed_agent_farmer(agent_status="ACTIVE", farmer_verified=False):
    from app.models.database import get_db
    from app.utils.security import hash_pin

    with get_db() as conn:
        agent_id = conn.execute(
            """INSERT INTO agents
               (name, phone, normalized_phone, verification_status, account_status,
                phone_verified, pin_hash, location, is_active)
               VALUES ('Ada Agent', '+2348020000101', '+2348020000101', 'VERIFIED', ?,
                       1, ?, 'Lagos', ?)""",
            (agent_status, hash_pin("1234"), 1 if agent_status == "ACTIVE" else 0),
        ).lastrowid
        farmer_id = conn.execute(
            """INSERT INTO farmers
               (name, phone, normalized_phone, verification_status, account_status,
                phone_verified, crop, location, pin_hash, price, quantity_available,
                listing_status, kyc_status, is_active)
               VALUES ('Femi Farmer', '+2348030000102', '+2348030000102', ?, 'ACTIVE',
                       1, 'Maize', 'Kaduna', ?, 5000, 20, 'DRAFT', ?, 1)""",
            (
                "VERIFIED" if farmer_verified else "UNVERIFIED", hash_pin("4321"),
                "VERIFIED" if farmer_verified else "PENDING",
            ),
        ).lastrowid
    return agent_id, farmer_id


def _assign(agent_id, farmer_id, relationship="VERIFICATION", verified=True):
    from app.services.agent_incentive_service import AgentIncentiveService

    result = AgentIncentiveService.assign_relationship(
        agent_id, farmer_id, relationship, "test", verified=verified
    )
    assert result["ok"] is True


def _insert_settled_order(farmer_phone, buyer_phone, status="DELIVERED",
                          dispute_status=None, completed_at=None):
    from app.models.database import get_db

    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO escrow_ledger
               (farmer_phone, buyer_phone, crop, quantity_bags, amount, service_fee,
                release_code_hash, status, payment_confirmed_at, payout_status, completed_at)
               VALUES (?, ?, 'Maize', 1, 5000, 125, 'test-hash', ?,
                       datetime('now'), 'success', ?)""",
            (farmer_phone, buyer_phone, status, completed_at),
        )
        txn_id = conn.execute(
            "SELECT txn_id FROM escrow_ledger WHERE id=?", (cursor.lastrowid,)
        ).fetchone()["txn_id"]
        conn.execute(
            """INSERT INTO logistics_log
               (txn_id, status, payout_status, settlement_amount)
               VALUES (?, 'DELIVERED', 'success', 1000)""",
            (txn_id,),
        )
        if dispute_status:
            conn.execute(
                """INSERT INTO disputes
                   (dispute_id, txn_id, raised_by_type, raised_by_id, reason, status)
                   VALUES (?, ?, 'BUYER', ?, 'Quality issue', ?)""",
                (f"DSP-{txn_id}", txn_id, buyer_phone, dispute_status),
            )
    return txn_id


def _verification_reward(agent_id, farmer_id):
    from app.services.agent_incentive_service import AgentIncentiveService

    _assign(agent_id, farmer_id)
    return AgentIncentiveService.evaluate_event(
        "FARMER_VERIFIED", farmer_id, agent_id=agent_id,
        source_reference=f"FARMER:{farmer_id}",
    )


def test_farmer_registration_does_not_generate_reward(incentives):
    from app.models.database import fetchone

    _seed_agent_farmer()
    assert fetchone("SELECT COUNT(*) AS n FROM agent_ledger_entries")["n"] == 0


def test_verified_farmer_generates_one_configured_reward(incentives):
    from app.models.database import fetchone
    from app.services.agent_incentive_service import AgentIncentiveService

    agent_id, farmer_id = _seed_agent_farmer()
    result = AgentIncentiveService.record_farmer_verification(agent_id, farmer_id, "test-agent")
    assert result["ok"] is True
    entry = fetchone("SELECT * FROM agent_ledger_entries")
    policy = fetchone(
        "SELECT amount_kobo FROM agent_incentive_policies WHERE incentive_code='FARMER_VERIFIED'"
    )
    assert entry["amount_kobo"] == policy["amount_kobo"]
    assert entry["status"] == "PENDING"


def test_repeated_verification_is_idempotent(incentives):
    from app.models.database import fetchone
    from app.services.agent_incentive_service import AgentIncentiveService

    agent_id, farmer_id = _seed_agent_farmer()
    AgentIncentiveService.record_farmer_verification(agent_id, farmer_id, "test-agent")
    second = AgentIncentiveService.record_farmer_verification(agent_id, farmer_id, "test-agent")
    assert second["ok"] is True
    assert fetchone(
        "SELECT COUNT(*) AS n FROM agent_ledger_entries WHERE incentive_code='FARMER_VERIFIED'"
    )["n"] == 1
    assert fetchone("SELECT recruits FROM agents WHERE id=?", (agent_id,))["recruits"] == 1


def test_first_approved_listing_generates_configured_reward(incentives):
    from app.models.database import execute, fetchone
    from app.services import product_service

    agent_id, farmer_id = _seed_agent_farmer(farmer_verified=True)
    _assign(agent_id, farmer_id, "LISTING_SUPPORT")
    execute(
        "UPDATE farmers SET listed_by_agent_phone='+2348020000101' WHERE id=?", (farmer_id,)
    )
    result = product_service.review_product_listing(
        "+2348030000102", "PUBLISHED", "reviewer"
    )
    assert result["ok"] is True
    entry = fetchone(
        "SELECT * FROM agent_ledger_entries WHERE incentive_code='FIRST_LISTING_APPROVED'"
    )
    policy = fetchone(
        "SELECT amount_kobo FROM agent_incentive_policies WHERE incentive_code='FIRST_LISTING_APPROVED'"
    )
    assert entry["amount_kobo"] == policy["amount_kobo"]


def test_second_listing_approval_does_not_duplicate_first_listing_reward(incentives):
    from app.models.database import execute, fetchone
    from app.services import product_service

    agent_id, farmer_id = _seed_agent_farmer(farmer_verified=True)
    _assign(agent_id, farmer_id, "LISTING_SUPPORT")
    execute(
        "UPDATE farmers SET listed_by_agent_phone='+2348020000101' WHERE id=?", (farmer_id,)
    )
    product_service.review_product_listing("+2348030000102", "PUBLISHED", "reviewer")
    execute("UPDATE farmers SET crop='Beans', listing_status='DRAFT' WHERE id=?", (farmer_id,))
    product_service.review_product_listing("+2348030000102", "PUBLISHED", "reviewer")
    assert fetchone(
        """SELECT COUNT(*) AS n FROM agent_ledger_entries
           WHERE incentive_code='FIRST_LISTING_APPROVED'"""
    )["n"] == 1


def test_payment_confirmation_alone_does_not_generate_transaction_reward(incentives):
    from app.models.database import get_db, fetchone
    from app.services.agent_incentive_service import AgentIncentiveService

    agent_id, farmer_id = _seed_agent_farmer(farmer_verified=True)
    _assign(agent_id, farmer_id)
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO escrow_ledger
               (farmer_phone, buyer_phone, crop, quantity_bags, amount, service_fee,
                release_code_hash, status, payment_confirmed_at)
               VALUES ('+2348030000102', '+2348040000103', 'Maize', 1, 5000, 125,
                       'hash', 'ESCROW_LOCKED', datetime('now'))"""
        )
        txn_id = conn.execute(
            "SELECT txn_id FROM escrow_ledger WHERE id=?", (cursor.lastrowid,)
        ).fetchone()["txn_id"]
    result = AgentIncentiveService.evaluate_completed_order(txn_id)
    assert result["completed"] is False
    assert fetchone(
        "SELECT COUNT(*) AS n FROM agent_ledger_entries WHERE incentive_code='FIRST_TRANSACTION_COMPLETED'"
    )["n"] == 0


def test_fully_settled_order_generates_first_transaction_reward(incentives):
    from app.models.database import fetchone
    from app.services.agent_incentive_service import AgentIncentiveService

    agent_id, farmer_id = _seed_agent_farmer(farmer_verified=True)
    _assign(agent_id, farmer_id)
    txn_id = _insert_settled_order("+2348030000102", "+2348040000103")
    result = AgentIncentiveService.evaluate_completed_order(txn_id)
    assert result["completed"] is True
    assert fetchone("SELECT status FROM escrow_ledger WHERE txn_id=?", (txn_id,))["status"] == "COMPLETED"
    assert fetchone(
        "SELECT COUNT(*) AS n FROM agent_ledger_entries WHERE incentive_code='FIRST_TRANSACTION_COMPLETED'"
    )["n"] == 1


def test_second_completed_order_triggers_configured_retention_bonus(incentives):
    from app.models.database import fetchone
    from app.services.agent_incentive_service import AgentIncentiveService

    agent_id, farmer_id = _seed_agent_farmer(farmer_verified=True)
    _assign(agent_id, farmer_id)
    first = _insert_settled_order("+2348030000102", "+2348040000103")
    second = _insert_settled_order("+2348030000102", "+2348040000104")
    AgentIncentiveService.evaluate_completed_order(first)
    AgentIncentiveService.evaluate_completed_order(second)
    assert fetchone(
        "SELECT COUNT(*) AS n FROM agent_ledger_entries WHERE incentive_code='FARMER_RETENTION_BONUS'"
    )["n"] == 1


@pytest.mark.parametrize("status,dispute", [
    ("CANCELLED", None), ("EXPIRED", None), ("DELIVERED", "OPEN"),
])
def test_cancelled_refunded_or_disputed_orders_do_not_reward(incentives, status, dispute):
    from app.models.database import fetchone
    from app.services.agent_incentive_service import AgentIncentiveService

    agent_id, farmer_id = _seed_agent_farmer(farmer_verified=True)
    _assign(agent_id, farmer_id)
    txn_id = _insert_settled_order(
        "+2348030000102", "+2348040000103", status=status, dispute_status=dispute
    )
    assert AgentIncentiveService.evaluate_completed_order(txn_id)["completed"] is False
    assert fetchone(
        "SELECT COUNT(*) AS n FROM agent_ledger_entries WHERE incentive_code='FIRST_TRANSACTION_COMPLETED'"
    )["n"] == 0


def test_suspended_agent_cannot_receive_new_incentive(incentives):
    from app.models.database import fetchone

    agent_id, farmer_id = _seed_agent_farmer(agent_status="SUSPENDED", farmer_verified=True)
    result = _verification_reward(agent_id, farmer_id)
    assert result["created"] is False
    assert fetchone("SELECT COUNT(*) AS n FROM agent_ledger_entries")["n"] == 0


def test_approved_entries_are_included_in_payout_calculation(incentives):
    from app.models.database import fetchone
    from app.services.agent_incentive_service import AgentIncentiveService
    from app.services.agent_payout_service import AgentPayoutService

    agent_id, farmer_id = _seed_agent_farmer(farmer_verified=True)
    earning = _verification_reward(agent_id, farmer_id)
    AgentIncentiveService.approve_earning(earning["entry_id"], "reviewer")
    calculated = AgentPayoutService.calculate_agent_payout(agent_id)
    assert calculated["net_kobo"] > 0
    batch = AgentPayoutService.create_batch(
        "2020-01-01", "2099-12-31", "MONTHLY", "operations"
    )
    assert batch["ok"] is True
    assert fetchone(
        "SELECT status FROM agent_ledger_entries WHERE id=?", (earning["entry_id"],)
    )["status"] == "PAYABLE"


def test_paid_batch_cannot_be_paid_twice(incentives):
    from app.models.database import fetchone
    from app.services.agent_incentive_service import AgentIncentiveService
    from app.services.agent_payout_service import AgentPayoutService

    agent_id, farmer_id = _seed_agent_farmer(farmer_verified=True)
    earning = _verification_reward(agent_id, farmer_id)
    AgentIncentiveService.approve_earning(earning["entry_id"], "reviewer")
    batch = AgentPayoutService.create_batch(
        "2020-01-01", "2099-12-31", "MONTHLY", "operations"
    )
    AgentPayoutService.approve_batch(batch["batch_id"], "operations")
    assert AgentPayoutService.mark_paid(batch["batch_id"], "operations", "MANUAL-001")["ok"]
    duplicate = AgentPayoutService.mark_paid(batch["batch_id"], "operations", "MANUAL-002")
    assert duplicate["already_paid"] is True
    assert fetchone(
        "SELECT payment_reference FROM agent_payout_batches WHERE id=?", (batch["batch_id"],)
    )["payment_reference"] == "MANUAL-001"


def test_reversal_entry_offsets_original_earning(incentives):
    from app.models.database import fetchone
    from app.services.agent_incentive_service import AgentIncentiveService

    agent_id, farmer_id = _seed_agent_farmer(farmer_verified=True)
    earning = _verification_reward(agent_id, farmer_id)
    AgentIncentiveService.approve_earning(earning["entry_id"], "reviewer")
    result = AgentIncentiveService.reverse_earning(
        earning["entry_id"], "operations", "Duplicate KYC identity"
    )
    assert result["ok"] is True
    original = fetchone("SELECT * FROM agent_ledger_entries WHERE id=?", (earning["entry_id"],))
    reversal = fetchone("SELECT * FROM agent_ledger_entries WHERE id=?", (result["reversal_id"],))
    assert original["status"] == "REVERSED"
    assert reversal["amount_kobo"] == -original["amount_kobo"]
    assert AgentIncentiveService.get_agent_summary(agent_id)["lifetime_net_kobo"] == 0


def test_admin_authorization_is_enforced(incentives):
    app = incentives
    with app.test_client() as client:
        anonymous = client.get("/admin/incentives/")
        assert anonymous.status_code == 302
        with client.session_transaction() as user_session:
            user_session["agent_phone"] = "+2348020000101"
        assert client.get("/admin/incentives/").status_code == 403
        with client.session_transaction() as staff_session:
            staff_session.clear()
            staff_session["staff_user_id"] = 1
            staff_session["staff_username"] = "reviewer"
            staff_session["staff_role"] = "REVIEWER"
        assert client.get("/admin/incentives/").status_code == 200


def test_ledger_financial_fields_are_immutable(incentives):
    import sqlite3

    from app.models.database import get_db

    agent_id, farmer_id = _seed_agent_farmer(farmer_verified=True)
    earning = _verification_reward(agent_id, farmer_id)
    with pytest.raises(sqlite3.IntegrityError):
        with get_db() as conn:
            conn.execute(
                "UPDATE agent_ledger_entries SET amount_kobo=1 WHERE id=?",
                (earning["entry_id"],),
            )
