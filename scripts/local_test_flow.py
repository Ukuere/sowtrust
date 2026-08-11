"""
Sowtrust — Local End-to-End Test Helper.

Automates the repetitive, non-money-moving setup steps against your
LOCAL server (http://localhost:5000), so you're not hand-typing a dozen
curl commands. Run this while `python run.py` is running in another
terminal.

Usage:
    python scripts/local_test_flow.py setup
        Registers a test farmer, sets a price, and marks KYC verified.

    python scripts/local_test_flow.py add-bank <account_number> <bank_menu_number>
        e.g. python scripts/local_test_flow.py add-bank 0123456789 5
        (5 = Access Bank in the BANKS menu — see config/settings.py for
        the full numbered list)
"""
import sys
import os
import sqlite3
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

BASE_URL = "http://localhost:5000"
FARMER_PHONE = "+2348011110000"
PIN = "1234"


def ussd(text, phone=FARMER_PHONE):
    resp = requests.post(f"{BASE_URL}/ussd", data={
        "sessionId": "localtest1",
        "serviceCode": "*709#",
        "phoneNumber": phone,
        "text": text,
    })
    print(f"  >> text='{text}'")
    print(f"  << {resp.text}\n")
    return resp.text


def setup():
    print(f"Registering test farmer {FARMER_PHONE}...\n")
    ussd("")  # initial dial
    ussd("1")  # Farmer Portal
    ussd("1*1")  # Register
    ussd("1*1*Test Farmer")  # name
    ussd("1*1*Test Farmer*Maize")  # product
    ussd("1*1*Test Farmer*Maize*Lagos")  # location
    ussd(f"1*1*Test Farmer*Maize*Lagos*{PIN}")  # pin
    ussd(f"1*1*Test Farmer*Maize*Lagos*{PIN}*{PIN}")  # confirm pin — registration complete

    print("Setting price to NGN 25,000/bag...\n")
    ussd("1")
    ussd("1*2")
    ussd(f"1*2*{PIN}")
    ussd(f"1*2*{PIN}*25000")

    print("Marking KYC as VERIFIED directly in DB (normally an agent does this)...")
    from config.settings import config
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.execute("UPDATE farmers SET kyc_status='VERIFIED' WHERE phone=?", (FARMER_PHONE,))
    conn.commit()
    conn.close()
    print("✅ Done. Farmer is registered, priced, and verified.")
    print(f"   Phone: {FARMER_PHONE}  |  PIN: {PIN}  |  Product: Maize @ NGN 25,000")
    print("\nNext: python scripts/local_test_flow.py add-bank <your_account_number> <bank_menu_number>")


def add_bank(account_number, bank_choice):
    print(f"Adding bank account {account_number} (bank menu #{bank_choice})...\n")
    ussd("4")
    ussd("4*3")
    ussd(f"4*3*{bank_choice}")
    ussd(f"4*3*{bank_choice}*{account_number}")
    result = ussd(f"4*3*{bank_choice}*{account_number}*{PIN}")

    if "Is this YOU" not in result:
        print("⚠️  Account resolution may have failed — check the response above.")
        return

    print("Account resolved — confirming save (answering '1' = yes)...\n")
    ussd(f"4*3*{bank_choice}*{account_number}*{PIN}*1")
    print("✅ Bank account should now be saved. Check the SMS/response above for confirmation.")


def buy():
    print("Placing a test order as a buyer...\n")
    buyer_phone = "+2348099990000"
    ussd("2", phone=buyer_phone)
    ussd("2*1", phone=buyer_phone)
    ussd("2*1*Maize", phone=buyer_phone)
    ussd("2*1*Maize*1", phone=buyer_phone)
    ussd("2*1*Maize*1*2", phone=buyer_phone)  # 2 bags
    result = ussd("2*1*Maize*1*2*1", phone=buyer_phone)  # confirm

    if "Acct:" in result:
        print("✅ Virtual account generated. Go check your Paystack Test Mode dashboard")
        print("   under Transactions — you should see a new PENDING transaction.")
        print("   Look for a way to simulate/complete it (exact wording varies).")
    else:
        print("⚠️  Something looks off — check the response above.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "setup":
        setup()
    elif cmd == "add-bank":
        if len(sys.argv) != 4:
            print("Usage: python scripts/local_test_flow.py add-bank <account_number> <bank_menu_number>")
            sys.exit(1)
        add_bank(sys.argv[2], sys.argv[3])
    elif cmd == "buy":
        buy()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
