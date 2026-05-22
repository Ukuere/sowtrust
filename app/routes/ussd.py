"""
AgriHub Global — USSD Route Handler v6.1
=========================================
KEY FIX (v6.1):
  Buyer portal no longer asks for a farmer phone number.
  Instead, the system shows a LIVE NUMBERED LISTING of
  verified farmers selling the chosen crop. The buyer
  simply picks a number (1-5). The phone number match
  happens internally — the buyer never needs to know it.

  New Buyer Flow:
    *709# > 2 > 1 > [crop] > [pick farmer #] > [qty] > confirm > LOCKED

All portals: Farmer, Buyer, Logistics, Wallet, Withdraw, Agent.
"""
from flask import Blueprint, request
from app.models.database import get_db, fetchone, fetchall
from app.utils.security import (
    hash_pin, verify_pin,
    get_session, set_session, clear_session
)
from app.services.escrow_service import (
    lock_escrow, release_escrow,
    get_active_escrow, get_farmer_history
)
from app.services.sms_service import send_sms, notify_logistics
from config.settings import config

ussd_bp = Blueprint("ussd", __name__)
CROPS = config.CROPS


# ── Helpers ────────────────────────────────────────────────────────────────
def _farmer(phone):
    return fetchone("SELECT * FROM farmers WHERE phone=? AND is_active=1", (phone,))

def _agent(phone):
    return fetchone("SELECT * FROM agents WHERE phone=? AND is_active=1", (phone,))

def _crop_menu():
    return "\n".join(f"{k}. {v}" for k, v in CROPS.items())

def CON(msg): return f"CON {msg}"
def END(msg): return f"END {msg}"


# ── Main USSD Entry Point ──────────────────────────────────────────────────
@ussd_bp.route("/ussd", methods=["POST"])
def ussd_handler():
    text  = request.values.get("text", "").strip()
    phone = request.values.get("phoneNumber", "").strip()
    steps = text.split("*") if text else []
    level = len(steps)

    # ── Level 0 — Main Menu ────────────────────────────────────────────────
    if level == 0 or text == "":
        clear_session(phone)
        return CON(
            "AgriHub Global\n"
            "1. Farmer Portal\n"
            "2. Buyer Portal\n"
            "3. Logistics\n"
            "4. Wallet/PIN\n"
            "5. Withdraw Funds\n"
            "6. Agent Portal"
        )

    choice = steps[0]

    # ══════════════════════════════════════════════════════════════════════
    #  PORTAL 1 — FARMER
    # ══════════════════════════════════════════════════════════════════════
    if choice == "1":
        if level == 1:
            return CON(
                "Farmer Portal\n"
                "1. Register\n"
                "2. Update Price\n"
                "3. Release Escrow\n"
                "4. View History"
            )

        sub = steps[1]

        # 1.1 Registration
        if sub == "1":
            if level == 2: return CON("Enter Full Name:")
            if level == 3: return CON(f"Select Crop:\n{_crop_menu()}")
            if level == 4: return CON("Enter Your Location (LGA/Town):")
            if level == 5: return CON("Set 4-digit PIN:")
            if level == 6: return CON("Confirm PIN:")
            if level == 7:
                name     = steps[2]
                crop_key = steps[3]
                loc      = steps[4]
                pin      = steps[5]
                pin2     = steps[6]
                if pin != pin2:
                    return END("PINs do not match. Dial *709# to retry.")
                if len(pin) != 4 or not pin.isdigit():
                    return END("PIN must be exactly 4 digits.")
                crop = CROPS.get(crop_key)
                if not crop:
                    return END("Invalid crop selection.")
                if _farmer(phone):
                    return END("Account already exists. Dial *709# to use it.")
                try:
                    with get_db() as conn:
                        conn.execute(
                            "INSERT INTO farmers (name,phone,crop,location,pin_hash) VALUES (?,?,?,?,?)",
                            (name.title(), phone, crop, loc.title(), hash_pin(pin))
                        )
                        conn.execute(
                            "INSERT INTO audit_log(actor,action,details) VALUES(?,?,?)",
                            (phone, "FARMER_REGISTERED", f"Crop:{crop}")
                        )
                    send_sms(phone,
                        f"Welcome to AgriHub, {name.title()}!\n"
                        f"Account active. Dial *709# > 1 > 2\n"
                        f"to set your price so buyers can find you.")
                    return END(
                        f"Registration Successful!\n"
                        f"Welcome {name.title()}.\n"
                        f"Crop: {crop}\n"
                        f"Next: dial *709# > 1 > 2 to set your price."
                    )
                except Exception as e:
                    return END(f"Registration failed: {e}")

        # 1.2 Update Price
        elif sub == "2":
            if level == 2: return CON("Enter your 4-digit PIN:")
            if level == 3:
                farmer = _farmer(phone)
                if not farmer or not verify_pin(steps[2], farmer["pin_hash"]):
                    return END("Invalid PIN or account not found.")
                return CON(
                    f"Crop: {farmer['crop']}\n"
                    f"Current Price: NGN {farmer['price']:,.0f}/bag\n\n"
                    f"Enter New Price per Bag (NGN):"
                )
            if level == 4:
                price_str = steps[3].replace(",", "")
                if not price_str.isdigit():
                    return END("Invalid price. Enter numbers only.")
                price = float(price_str)
                with get_db() as conn:
                    conn.execute(
                        "UPDATE farmers SET price=? WHERE phone=?", (price, phone)
                    )
                return END(
                    f"Price updated to NGN {price:,.0f}/bag.\n"
                    f"Buyers can now see your listing."
                )

        # 1.3 Release Escrow
        elif sub == "3":
            if level == 2: return CON("Enter your PIN:")
            if level == 3:
                farmer = _farmer(phone)
                if not farmer or not verify_pin(steps[2], farmer["pin_hash"]):
                    return END("Invalid PIN.")
                active = get_active_escrow(phone)
                if not active:
                    return END("No active escrow found on your account.")
                return CON(
                    f"Active Escrow:\n"
                    f"TXN: {active['txn_id']}\n"
                    f"Crop: {active['crop']}\n"
                    f"Amount: NGN {active['amount']:,.0f}\n\n"
                    f"Enter Release Code from Buyer:"
                )
            if level == 4:
                release_code = steps[3].strip().upper()
                active = get_active_escrow(phone)
                if not active:
                    return END("No active escrow.")
                result = release_escrow(phone, active["txn_id"], release_code)
                if result["ok"]:
                    return END(
                        f"Payment Released!\n"
                        f"NGN {result['net_payout']:,.0f} added to wallet.\n"
                        f"Dial *709# > 5 to withdraw."
                    )
                return END(f"Release Failed: {result['error']}")

        # 1.4 View History
        elif sub == "4":
            if level == 2: return CON("Enter your PIN:")
            if level == 3:
                farmer = _farmer(phone)
                if not farmer or not verify_pin(steps[2], farmer["pin_hash"]):
                    return END("Invalid PIN.")
                history = get_farmer_history(phone)
                if not history:
                    return END("No transactions yet.")
                lines = [
                    f"{r['txn_id'][:8]} | {r['status']} | NGN{r['amount']:,.0f}"
                    for r in history[:5]
                ]
                return END("Last 5 Transactions:\n" + "\n".join(lines))

    # ══════════════════════════════════════════════════════════════════════
    #  PORTAL 2 — BUYER  (Marketplace Model)
    #
    #  THE FIX EXPLAINED:
    #  ─────────────────────────────────────────────────────────────────────
    #  OLD (broken): Buyer had to type farmer's phone number manually.
    #                Buyer and farmer don't know each other → impossible.
    #
    #  NEW (fixed):  Buyer picks a CROP. System fetches all VERIFIED
    #                farmers listing that crop from the database and
    #                shows them as a numbered menu:
    #
    #                  1. Emeka | Ogun State | NGN150,000/bag
    #                  2. Bello | Kano State | NGN145,000/bag
    #                  3. Ngozi | Enugu State | NGN148,000/bag
    #
    #                Buyer types 1, 2, or 3. System retrieves the
    #                farmer's phone from the database internally.
    #                Buyer NEVER types a phone number. Ever.
    #
    #  Step map:
    #    level 2 → select crop
    #    level 3 → show farmer listing; buyer picks a number
    #    level 4 → enter quantity (bags)
    #    level 5 → show full summary; confirm or cancel
    #    level 6 → lock escrow, send SMS to both parties
    # ══════════════════════════════════════════════════════════════════════
    elif choice == "2":
        if level == 1:
            return CON(
                "Buyer Portal\n"
                "1. Browse & Buy (Escrow)\n"
                "2. Post Crop Request\n"
                "3. My Orders"
            )

        sub = steps[1]

        # ──────────────────────────────────────────────────────────────────
        # 2.1  BROWSE MARKETPLACE → PICK FARMER → LOCK ESCROW
        # ──────────────────────────────────────────────────────────────────
        if sub == "1":

            # Step 2 — buyer selects which crop they want to buy
            if level == 2:
                return CON(f"Select Crop to Buy:\n{_crop_menu()}")

            # Step 3 — system fetches verified farmers and shows numbered list
            if level == 3:
                crop = CROPS.get(steps[2])
                if not crop:
                    return END("Invalid crop. Dial *709# to try again.")

                rows = fetchall(
                    """SELECT name, location, price, phone
                       FROM   farmers
                       WHERE  crop = ?
                         AND  price > 0
                         AND  kyc_status = 'VERIFIED'
                         AND  is_active  = 1
                       ORDER  BY price ASC
                       LIMIT  5""",
                    (crop,)
                )

                if not rows:
                    return END(
                        f"No verified farmers listing {crop} now.\n"
                        f"Dial *709# > 2 > 2 to post a request.\n"
                        f"An agent will match you within 24hrs."
                    )

                # Store farmer list in session keyed by menu number.
                # This is how we retrieve the farmer's phone later
                # without the buyer ever seeing or typing it.
                farmer_map = {
                    str(i + 1): dict(r) for i, r in enumerate(rows)
                }
                set_session(phone, {"crop": crop, "farmers": farmer_map})

                lines = [
                    f"{i+1}. {r['name']} | {r['location']} | NGN{r['price']:,.0f}/bag"
                    for i, r in enumerate(rows)
                ]
                return CON(
                    f"Verified {crop} Sellers:\n"
                    + "\n".join(lines)
                    + "\n\nEnter number to select:"
                )

            # Step 4 — buyer picked a number; retrieve farmer from session
            if level == 4:
                sess = get_session(phone)
                if not sess:
                    return END("Session expired. Dial *709# to start again.")

                farmer_map = sess.get("farmers", {})
                pick = steps[3].strip()

                if pick not in farmer_map:
                    return END(
                        f"Invalid selection. Choose a number from the list.\n"
                        f"Dial *709# to try again."
                    )

                chosen = farmer_map[pick]
                sess["chosen"] = chosen
                set_session(phone, sess)

                return CON(
                    f"Selected:\n"
                    f"Farmer:   {chosen['name']}\n"
                    f"Location: {chosen['location']}\n"
                    f"Price:    NGN {chosen['price']:,.0f}/bag\n\n"
                    f"Enter quantity (bags):"
                )

            # Step 5 — calculate total and show escrow summary
            if level == 5:
                sess = get_session(phone)
                if not sess or "chosen" not in sess:
                    return END("Session expired. Dial *709# to start again.")

                qty_str = steps[4].strip()
                if not qty_str.isdigit() or int(qty_str) < 1:
                    return END("Invalid quantity. Enter a whole number e.g. 5")

                chosen  = sess["chosen"]
                qty     = int(qty_str)
                total   = chosen["price"] * qty
                fee     = round(total * config.SERVICE_FEE_PERCENT / 100, 2)
                payable = total + fee

                sess.update({"qty": qty, "total": total, "fee": fee})
                set_session(phone, sess)

                return CON(
                    f"-- Escrow Summary --\n"
                    f"Crop:    {sess['crop']}\n"
                    f"Farmer:  {chosen['name']}\n"
                    f"Bags:    {qty}\n"
                    f"Goods:   NGN {total:,.0f}\n"
                    f"Fee:     NGN {fee:,.0f} (2.5%)\n"
                    f"TOTAL:   NGN {payable:,.0f}\n"
                    f"──────────────────\n"
                    f"1. Confirm & Lock\n"
                    f"2. Cancel"
                )

            # Step 6 — confirmed; lock the escrow
            if level == 6:
                sess = get_session(phone)
                if not sess or "chosen" not in sess:
                    return END("Session expired. Dial *709# to start again.")

                if steps[5] != "1":
                    clear_session(phone)
                    return END("Cancelled. Dial *709# whenever you are ready.")

                chosen = sess["chosen"]
                result = lock_escrow(
                    buyer_phone   = phone,
                    farmer_phone  = chosen["phone"],
                    crop          = sess["crop"],
                    quantity_bags = sess["qty"],
                    amount        = sess["total"]
                )
                clear_session(phone)

                if result["ok"]:
                    return END(
                        f"Escrow Locked!\n"
                        f"TXN: {result['txn_id']}\n"
                        f"Farmer {chosen['name']} notified by SMS.\n\n"
                        f"Your release code sent to your phone.\n"
                        f"Give it to farmer ONLY after delivery.\n"
                        f"Do NOT share before goods arrive."
                    )
                return END(
                    f"Transaction failed: {result['error']}\n"
                    f"Dial *709# to try again."
                )

        # ──────────────────────────────────────────────────────────────────
        # 2.2  POST CROP REQUEST
        #      When no farmer is listed yet for that crop.
        #      Agent will manually match and notify buyer by SMS.
        # ──────────────────────────────────────────────────────────────────
        elif sub == "2":
            if level == 2: return CON(f"Select Crop Needed:\n{_crop_menu()}")
            if level == 3: return CON("Enter Quantity (bags):")
            if level == 4: return CON("Enter Max Price per Bag (NGN):")
            if level == 5: return CON("Enter Your Delivery Location:")
            if level == 6:
                crop      = CROPS.get(steps[2])
                qty       = steps[3]
                max_price = steps[4]
                location  = steps[5]
                if not crop:
                    return END("Invalid crop selection.")
                with get_db() as conn:
                    conn.execute(
                        "INSERT INTO buyer_requests "
                        "(buyer_phone, crop, qty_bags, max_price, location) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (phone, crop, qty, max_price, location)
                    )
                return END(
                    f"Request Posted!\n"
                    f"Crop: {crop} | Qty: {qty} bags\n"
                    f"An agent will match you with a verified\n"
                    f"{crop} farmer near {location}\n"
                    f"and notify you by SMS within 24 hours."
                )

        # ──────────────────────────────────────────────────────────────────
        # 2.3  MY ORDERS
        # ──────────────────────────────────────────────────────────────────
        elif sub == "3":
            orders = fetchall(
                """SELECT txn_id, crop, amount, status, locked_at
                   FROM   escrow_ledger
                   WHERE  buyer_phone = ?
                   ORDER  BY locked_at DESC
                   LIMIT  5""",
                (phone,)
            )
            if not orders:
                return END(
                    "No orders found.\n"
                    "Dial *709# > 2 > 1 to make your first purchase."
                )
            lines = [
                f"{r['txn_id'][:8]} | {r['crop']} | NGN{r['amount']:,.0f} | {r['status']}"
                for r in orders
            ]
            return END("Your Recent Orders:\n" + "\n".join(lines))

    # ══════════════════════════════════════════════════════════════════════
    #  PORTAL 3 — LOGISTICS
    # ══════════════════════════════════════════════════════════════════════
    elif choice == "3":
        if level == 1:
            return CON("Logistics\n1. Track Shipment\n2. Dispatch Goods")

        sub = steps[1]

        if sub == "1":
            if level == 2: return CON("Enter TXN ID:")
            if level == 3:
                row = fetchone(
                    "SELECT * FROM logistics_log WHERE txn_id=?",
                    (steps[2].upper(),)
                )
                if not row:
                    return END("No logistics record found for that TXN ID.")
                return END(
                    f"TXN: {row['txn_id']}\n"
                    f"Status: {row['status']}\n"
                    f"From: {row['origin']}\n"
                    f"To: {row['destination']}\n"
                    f"Dispatched: {row['dispatched_at'] or 'Pending'}"
                )

        elif sub == "2":
            if level == 2: return CON("Enter TXN ID to Dispatch:")
            if level == 3: return CON("Enter Courier Name:")
            if level == 4: return CON("Enter Courier Phone:")
            if level == 5: return CON("Enter Origin Location:")
            if level == 6: return CON("Enter Destination Location:")
            if level == 7:
                txn_id        = steps[2].upper()
                courier_name  = steps[3]
                courier_phone = steps[4]
                origin        = steps[5]
                dest          = steps[6]
                escrow = fetchone(
                    "SELECT * FROM escrow_ledger WHERE txn_id=?", (txn_id,)
                )
                if not escrow:
                    return END("No escrow found for this TXN ID.")
                with get_db() as conn:
                    conn.execute(
                        """INSERT INTO logistics_log
                           (txn_id, courier_name, courier_phone,
                            origin, destination, status, dispatched_at)
                           VALUES (?,?,?,?,?,'IN_TRANSIT',datetime('now'))""",
                        (txn_id, courier_name, courier_phone, origin, dest)
                    )
                notify_logistics(
                    courier_phone, origin, dest, escrow["crop"], txn_id
                )
                return END(
                    f"Dispatch Logged!\n"
                    f"Courier: {courier_name}\n"
                    f"Route: {origin} to {dest}\n"
                    f"SMS sent to courier."
                )

    # ══════════════════════════════════════════════════════════════════════
    #  PORTAL 4 — WALLET / PIN
    # ══════════════════════════════════════════════════════════════════════
    elif choice == "4":
        if level == 1:
            return CON("Wallet & PIN\n1. Check Balance\n2. Change PIN")

        sub = steps[1]

        if sub == "1":
            if level == 2: return CON("Enter PIN:")
            if level == 3:
                farmer = _farmer(phone)
                if not farmer or not verify_pin(steps[2], farmer["pin_hash"]):
                    return END("Invalid PIN or account not found.")
                return END(
                    f"Balance:      NGN {farmer['balance']:,.0f}\n"
                    f"Credit Score: {farmer['credit_score']}\n"
                    f"KYC Status:   {farmer['kyc_status']}"
                )

        elif sub == "2":
            if level == 2: return CON("Enter Current PIN:")
            if level == 3: return CON("Enter New 4-digit PIN:")
            if level == 4: return CON("Confirm New PIN:")
            if level == 5:
                farmer = _farmer(phone)
                if not farmer or not verify_pin(steps[2], farmer["pin_hash"]):
                    return END("Invalid current PIN.")
                new_pin = steps[3]
                confirm = steps[4]
                if new_pin != confirm:
                    return END("PINs do not match. Dial *709# to retry.")
                if len(new_pin) != 4 or not new_pin.isdigit():
                    return END("PIN must be exactly 4 digits.")
                with get_db() as conn:
                    conn.execute(
                        "UPDATE farmers SET pin_hash=? WHERE phone=?",
                        (hash_pin(new_pin), phone)
                    )
                send_sms(phone, "AgriHub: Your PIN was changed successfully.")
                return END("PIN changed successfully.")

    # ══════════════════════════════════════════════════════════════════════
    #  PORTAL 5 — WITHDRAW
    # ══════════════════════════════════════════════════════════════════════
    elif choice == "5":
        if level == 1: return CON("Enter Amount to Withdraw (NGN):")
        if level == 2: return CON("Enter Wallet PIN:")
        if level == 3:
            amount_str = steps[1].replace(",", "")
            if not amount_str.isdigit():
                return END("Invalid amount. Enter numbers only.")
            amount = float(amount_str)
            farmer = _farmer(phone)
            if not farmer or not verify_pin(steps[2], farmer["pin_hash"]):
                return END("Invalid PIN or account not found.")
            if farmer["balance"] < amount:
                return END(
                    f"Insufficient balance.\n"
                    f"Available: NGN {farmer['balance']:,.0f}"
                )
            with get_db() as conn:
                conn.execute(
                    "UPDATE farmers SET balance = balance - ? WHERE phone=?",
                    (amount, phone)
                )
                conn.execute(
                    "INSERT INTO audit_log(actor,action,details) VALUES(?,?,?)",
                    (phone, "WITHDRAWAL", f"AMT:{amount}")
                )
            send_sms(
                phone,
                f"AgriHub: Withdrawal of NGN {amount:,.0f} is processing.\n"
                f"Funds arrive within 24 hours."
            )
            return END(
                f"Withdrawal of NGN {amount:,.0f} initiated.\n"
                f"SMS confirmation coming shortly."
            )

    # ══════════════════════════════════════════════════════════════════════
    #  PORTAL 6 — AGENT
    # ══════════════════════════════════════════════════════════════════════
    elif choice == "6":
        if level == 1:
            return CON(
                "Agent Portal\n"
                "1. Register as Agent\n"
                "2. Verify Farmer KYC\n"
                "3. View My Recruits"
            )

        sub = steps[1]

        if sub == "1":
            if level == 2: return CON("Enter Your Full Name:")
            if level == 3: return CON("Enter Your Location:")
            if level == 4: return CON("Set Agent 4-digit PIN:")
            if level == 5:
                name     = steps[2]
                location = steps[3]
                pin      = steps[4]
                if _agent(phone):
                    return END("Agent account already exists.")
                with get_db() as conn:
                    conn.execute(
                        "INSERT INTO agents (name,phone,pin_hash,location) VALUES (?,?,?,?)",
                        (name.title(), phone, hash_pin(pin), location.title())
                    )
                send_sms(
                    phone,
                    f"Welcome Agent {name.title()}!\n"
                    f"Account active. Start verifying farmers\n"
                    f"via *709# > 6 > 2."
                )
                return END(
                    f"Agent Registration Successful!\n"
                    f"Name: {name.title()}\n"
                    f"Location: {location}\n"
                    f"Dial *709# > 6 > 2 to verify farmers."
                )

        elif sub == "2":
            if level == 2: return CON("Enter Agent PIN:")
            if level == 3:
                agent = _agent(phone)
                if not agent or not verify_pin(steps[2], agent["pin_hash"]):
                    return END("Invalid Agent PIN.")
                return CON("Enter Farmer Phone Number to Verify:")
            if level == 4:
                farmer_phone = steps[3].strip()
                farmer = fetchone(
                    "SELECT * FROM farmers WHERE phone=?", (farmer_phone,)
                )
                if not farmer:
                    return END(
                        f"Farmer not found.\n"
                        f"Ask them to register first via *709# > 1 > 1."
                    )
                with get_db() as conn:
                    conn.execute(
                        "UPDATE farmers SET kyc_status='VERIFIED' WHERE phone=?",
                        (farmer_phone,)
                    )
                    conn.execute(
                        "UPDATE agents SET recruits = recruits + 1 WHERE phone=?",
                        (phone,)
                    )
                    conn.execute(
                        "INSERT INTO audit_log(actor,action,details) VALUES(?,?,?)",
                        (phone, "KYC_VERIFIED", f"Farmer:{farmer_phone}")
                    )
                send_sms(
                    farmer_phone,
                    f"AgriHub: {farmer['name']}, your account is VERIFIED!\n"
                    f"Set your price: *709# > 1 > 2\n"
                    f"Buyers can now find and purchase from you."
                )
                return END(
                    f"Farmer {farmer['name']} is now VERIFIED.\n"
                    f"They appear in buyer searches immediately."
                )

        elif sub == "3":
            if level == 2: return CON("Enter Agent PIN:")
            if level == 3:
                agent = _agent(phone)
                if not agent or not verify_pin(steps[2], agent["pin_hash"]):
                    return END("Invalid PIN.")
                return END(
                    f"Agent: {agent['name']}\n"
                    f"Location: {agent['location']}\n"
                    f"Farmers Verified: {agent['recruits']}\n"
                    f"Balance: NGN {agent['balance']:,.0f}"
                )

    return END("Invalid option. Dial *709# to start again.")
