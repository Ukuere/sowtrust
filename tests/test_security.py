import os
import time
import pytest

os.environ.setdefault("AT_USERNAME", "sandbox")
os.environ.setdefault("AT_API_KEY", "test_dummy_key")
os.environ.setdefault("PAYSTACK_SECRET_KEY", "sk_test_dummy_key_for_testing")


@pytest.fixture
def client(tmp_path):
    from config.settings import config
    test_db = str(tmp_path / "test_security.db")
    os.environ["DATABASE_PATH"] = test_db
    config.DATABASE_PATH = test_db

    from migrations.init_db import init_db
    init_db()
    from migrations.add_products_table import migrate as m1
    m1()
    from migrations.add_payments_columns import migrate as m2
    m2()
    from migrations.add_three_sided_fees import migrate as m3
    m3()
    from migrations.add_logistics_providers import migrate as m4
    m4()
    from migrations.add_session_table import migrate as m5
    m5()

    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ── PIN SECURITY ──────────────────────────────────────────────────────────

def test_new_pins_use_bcrypt_not_sha256(client):
    """New PINs must be bcrypt — SHA-256 on a 4-digit PIN is reversible
    almost instantly from a stolen database."""
    from app.utils.security import hash_pin
    h = hash_pin("1234")
    assert h.startswith("$2"), "PIN should be bcrypt-hashed"
    assert len(h) > 50


def test_same_pin_produces_different_hashes(client):
    """bcrypt salts each hash individually — two users with PIN 1234 must
    NOT share a hash, otherwise one cracked PIN exposes many accounts."""
    from app.utils.security import hash_pin
    assert hash_pin("1234") != hash_pin("1234")


def test_bcrypt_pin_verifies(client):
    from app.utils.security import hash_pin, verify_pin
    h = hash_pin("4321")
    assert verify_pin("4321", h) is True
    assert verify_pin("1234", h) is False


def test_legacy_sha256_pin_still_verifies(client):
    """Existing users must not be locked out by the upgrade."""
    import hashlib
    from app.utils.security import verify_pin
    legacy = hashlib.sha256("1234".encode()).hexdigest()
    assert verify_pin("1234", legacy) is True
    assert verify_pin("9999", legacy) is False


def test_legacy_pin_silently_upgrades_to_bcrypt_on_use(client):
    """The migration path: an old account logs in once, and its stored
    hash is transparently replaced with bcrypt — no forced reset."""
    import hashlib
    from app.models.database import execute, fetchone
    from app.utils.security import verify_and_upgrade_pin

    legacy = hashlib.sha256("1234".encode()).hexdigest()
    execute(
        """INSERT INTO farmers (phone, name, crop, location, pin_hash, kyc_status)
           VALUES ('+2348011112222','Old User','Maize','Lagos',?,'VERIFIED')""",
        (legacy,)
    )

    before = fetchone("SELECT pin_hash FROM farmers WHERE phone='+2348011112222'")
    assert not before["pin_hash"].startswith("$2")

    assert verify_and_upgrade_pin("farmers", "+2348011112222", "1234", legacy) is True

    after = fetchone("SELECT pin_hash FROM farmers WHERE phone='+2348011112222'")
    assert after["pin_hash"].startswith("$2"), "PIN should have been upgraded to bcrypt"

    # And the upgraded hash must still accept the same PIN
    from app.utils.security import verify_pin
    assert verify_pin("1234", after["pin_hash"]) is True


def test_wrong_pin_does_not_trigger_upgrade(client):
    """A failed attempt must never rewrite the stored hash."""
    import hashlib
    from app.models.database import execute, fetchone
    from app.utils.security import verify_and_upgrade_pin

    legacy = hashlib.sha256("1234".encode()).hexdigest()
    execute(
        """INSERT INTO farmers (phone, name, crop, location, pin_hash, kyc_status)
           VALUES ('+2348011113333','Old User','Maize','Lagos',?,'VERIFIED')""",
        (legacy,)
    )
    assert verify_and_upgrade_pin("farmers", "+2348011113333", "9999", legacy) is False
    after = fetchone("SELECT pin_hash FROM farmers WHERE phone='+2348011113333'")
    assert after["pin_hash"] == legacy, "Hash must be untouched after a failed attempt"


def test_pin_upgrade_rejects_unknown_table(client):
    """Table name is interpolated into SQL, so it must be whitelisted."""
    import hashlib
    from app.utils.security import verify_and_upgrade_pin
    legacy = hashlib.sha256("1234".encode()).hexdigest()
    with pytest.raises(ValueError):
        verify_and_upgrade_pin("farmers; DROP TABLE farmers--", "+234", "1234", legacy)


# ── RELEASE CODE SECURITY ─────────────────────────────────────────────────

def test_release_codes_are_unpredictable(client):
    """These codes release real money, so they must come from a
    cryptographically secure source, not a predictable PRNG."""
    from app.utils.security import generate_release_code
    codes = {generate_release_code() for _ in range(500)}
    assert len(codes) > 495, "Release codes should not collide meaningfully"
    assert all(len(c) == 6 for c in codes)


def test_release_code_verification(client):
    from app.utils.security import generate_release_code, hash_release_code, verify_release_code
    code = generate_release_code()
    h = hash_release_code(code)
    assert verify_release_code(code, h) is True
    assert verify_release_code(code.lower(), h) is True   # case-insensitive by design
    assert verify_release_code("ABC123", h) == (code == "ABC123")
    assert verify_release_code(code, "") is False


# ── SESSION STORE (multi-worker safety) ───────────────────────────────────

def test_session_survives_in_database_not_memory(client):
    """The whole point: session state must live in the shared DB so all
    four gunicorn workers can see it, not in one process's memory."""
    from app.utils.security import set_session, get_session
    from app.models.database import fetchone

    set_session("+2348011114444", {"crop": "Maize", "qty": 5})

    row = fetchone("SELECT * FROM ussd_sessions WHERE phone='+2348011114444'")
    assert row is not None, "Session must be persisted to the database"

    assert get_session("+2348011114444") == {"crop": "Maize", "qty": 5}


def test_session_overwrite_updates_not_duplicates(client):
    from app.utils.security import set_session, get_session
    from app.models.database import fetchall
    set_session("+2348011115555", {"step": 1})
    set_session("+2348011115555", {"step": 2})
    rows = fetchall("SELECT * FROM ussd_sessions WHERE phone='+2348011115555'")
    assert len(rows) == 1
    assert get_session("+2348011115555") == {"step": 2}


def test_session_clear(client):
    from app.utils.security import set_session, get_session, clear_session
    set_session("+2348011116666", {"a": 1})
    clear_session("+2348011116666")
    assert get_session("+2348011116666") == {}


def test_expired_session_returns_empty(client):
    from app.utils.security import get_session
    from app.models.database import execute
    from config.settings import config

    stale = time.time() - (config.USSD_SESSION_TTL + 60)
    execute(
        "INSERT INTO ussd_sessions (phone, data, last_active) VALUES (?, ?, ?)",
        ("+2348011117777", '{"crop": "Yam"}', stale)
    )
    assert get_session("+2348011117777") == {}


def test_purge_expired_sessions(client):
    from app.utils.security import purge_expired_sessions, set_session
    from app.models.database import execute, fetchall
    from config.settings import config

    stale = time.time() - (config.USSD_SESSION_TTL + 60)
    execute("INSERT INTO ussd_sessions (phone, data, last_active) VALUES (?, ?, ?)",
            ("+234801111888", '{}', stale))
    set_session("+234801111999", {"live": True})   # fresh one

    removed = purge_expired_sessions()
    assert removed == 1

    remaining = fetchall("SELECT * FROM ussd_sessions")
    assert len(remaining) == 1
    assert remaining[0]["phone"] == "+234801111999"


def test_corrupt_session_data_does_not_crash(client):
    """A malformed session row must not take down a farmer's USSD call."""
    from app.utils.security import get_session
    from app.models.database import execute
    execute("INSERT INTO ussd_sessions (phone, data, last_active) VALUES (?, ?, ?)",
            ("+2348011110001", 'not valid json{{{', time.time()))
    assert get_session("+2348011110001") == {}


def test_multi_step_ussd_flow_persists_across_requests(client):
    """Integration check: the buyer flow depends on session state carrying
    across separate HTTP requests — exactly what broke with in-memory
    sessions under multiple workers."""
    from app.models.database import execute

    execute(
        """INSERT INTO farmers
           (phone, name, crop, location, pin_hash, price, kyc_status, listing_status)
           VALUES ('+2348011110002','Test Farmer','Maize','Lagos','x',25000,'VERIFIED','PUBLISHED')"""
    )

    def ussd(text, phone="+2348099990001"):
        return client.post("/ussd", data={
            "sessionId": "s1", "serviceCode": "*709#", "phoneNumber": phone, "text": text
        })

    ussd("2")
    ussd("2*1")
    ussd("2*1*Maize")            # sets session: farmer list
    r = ussd("2*1*Maize*1")      # reads session to resolve chosen farmer
    assert b"Test Farmer" in r.data
    r2 = ussd("2*1*Maize*1*2")   # reads session again before delivery input
    assert b"delivery address" in r2.data
    r3 = ussd("2*1*Maize*1*2*Lagos")
    assert b"Quote Request" in r3.data
    assert b"1,250" in r3.data
