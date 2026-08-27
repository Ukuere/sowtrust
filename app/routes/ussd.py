"""
Sowtrust Global — USSD Route Handler v6.1
=========================================
Buyer portal shows a live numbered list of published products. Seller
verification remains visible as a separate trust signal; it does not hide
an otherwise valid listing.

  Buyer Flow:
    *709# > 2 > 1 > [crop] > [seller] > [qty] > [address] > quote request
    Operations locks quote > buyer accepts quote > payment initializes

All portals: Farmer, Buyer, Logistics, Wallet, Withdraw, Agent.
"""
from flask import Blueprint, request
import uuid
from app.models.database import get_db, fetchone, fetchall
from app.utils.security import (
    hash_pin, verify_pin, verify_and_upgrade_pin,
    get_session, set_session, clear_session
)
from app.services.escrow_service import (
    create_order_awaiting_quote, initiate_payment_for_order, release_escrow,
    get_active_escrow, get_farmer_history
)
from app.services.payment_service import (
    resolve_account_number, create_transfer_recipient, initiate_transfer
)
from app.services.fee_service import calculate_full_order
from app.services.logistics_service import (
    register_provider, get_provider, get_available_jobs, assign_provider,
    confirm_delivery, save_provider_bank_account, commit_provider_bank_account,
    create_quote_request, get_quote_for_order, accept_locked_quote,
)
from app.services.sms_service import send_sms, notify_logistics
from app.services.product_service import (
    get_or_create_product, list_active_products, find_farmers_for_product
)
from app.services import buyer_service, identity_service, notification_service
from app.services.agent_incentive_service import AgentIncentiveService
from app.utils.phone import normalize_phone
from config.settings import config

ussd_bp = Blueprint("ussd", __name__)


# ── Helpers ────────────────────────────────────────────────────────────────
def _farmer(phone):
    return fetchone(
        """SELECT * FROM farmers
           WHERE (normalized_phone=? OR phone=?) AND is_active=1""",
        (phone, phone),
    )

def _agent(phone):
    return fetchone(
        """SELECT * FROM agents
           WHERE (normalized_phone=? OR phone=?) AND is_active=1""",
        (phone, phone),
    )

def CON(msg): return f"CON {msg}"
def END(msg): return f"END {msg}"


# ── Main USSD Entry Point ──────────────────────────────────────────────────
@ussd_bp.route("/ussd", methods=["POST"])
def ussd_handler():
    text  = request.values.get("text", "").strip()
    raw_phone = request.values.get("phoneNumber", "").strip()
    phone = normalize_phone(raw_phone)
    if not phone:
        return END("We could not verify your phone number. Please contact SowTrust support.")
    steps = text.split("*") if text else []
    level = len(steps)

    # ── Level 0 — Main Menu ────────────────────────────────────────────────
    if level == 0 or text == "":
        clear_session(phone)
        return CON(
            "Sowtrust Global\n"
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
            if level == 3: return CON(
                "What do you grow or sell?\n"
                "Type the name, e.g. Maize, Tomatoes,\n"
                "Plantain, Ginger — anything."
            )
            if level == 4: return CON("Enter Your Location (LGA/Town):")
            if level == 5: return CON("Set 4-digit PIN:")
            if level == 6: return CON("Confirm PIN:")
            if level == 7:
                name      = steps[2]
                crop_raw  = steps[3]
                loc       = steps[4]
                pin       = steps[5]
                pin2      = steps[6]
                if pin != pin2:
                    return END("PINs do not match. Dial *709# to retry.")
                if len(pin) != 4 or not pin.isdigit():
                    return END("PIN must be exactly 4 digits.")
                crop = get_or_create_product(crop_raw)
                if not crop:
                    return END(
                        "That doesn't look like a valid product name.\n"
                        "Use letters only, 2-40 characters.\n"
                        "Dial *709# to retry."
                    )
                if _farmer(phone):
                    return END("Account already exists. Dial *709# to use it.")
                try:
                    with get_db() as conn:
                        conn.execute(
                            """INSERT INTO farmers
                               (name, phone, normalized_phone, registration_channel,
                                verification_status, account_status, phone_verified,
                                crop, location, pin_hash, listing_status)
                               VALUES (?, ?, ?, 'USSD', 'UNVERIFIED', 'ACTIVE', 1,
                                       ?, ?, ?, 'DRAFT')""",
                            (name.title(), phone, phone, crop, loc.title(), hash_pin(pin))
                        )
                        farmer_id = conn.execute(
                            "SELECT id FROM farmers WHERE normalized_phone=?", (phone,)
                        ).fetchone()[0]
                        conn.execute(
                            "INSERT INTO audit_log(actor,action,details) VALUES(?,?,?)",
                            (phone, "FARMER_REGISTERED", f"Crop:{crop}")
                        )
                    identity_service.ensure_user_role(
                        phone, "FARMER", name.title(), "USSD", True, farmer_id
                    )
                    send_sms(phone,
                        f"Welcome to Sowtrust, {name.title()}!\n"
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
                if not farmer or not verify_and_upgrade_pin("farmers", phone, steps[2], farmer["pin_hash"]):
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
                        """UPDATE farmers
                           SET price=?, listing_status='PUBLISHED',
                               verification_status=CASE
                                 WHEN verification_status='VERIFIED' THEN 'VERIFIED'
                                 ELSE 'PENDING' END,
                               listing_published_at=COALESCE(listing_published_at, datetime('now')),
                               listing_updated_at=datetime('now')
                           WHERE normalized_phone=? OR phone=?""",
                        (price, phone, phone),
                    )
                farmer = _farmer(phone)
                notification_service.notify_new_product_listing(dict(farmer))
                return END(
                    f"Price updated to NGN {price:,.0f}/bag.\n"
                    f"Your listing is now visible to buyers.\n"
                    f"An agent can add a real product photo later."
                )

        # 1.3 Release Escrow
        elif sub == "3":
            if level == 2: return CON("Enter your PIN:")
            if level == 3:
                farmer = _farmer(phone)
                if not farmer or not verify_and_upgrade_pin("farmers", phone, steps[2], farmer["pin_hash"]):
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
                        f"Release confirmed!\n"
                        f"NGN {result['net_payout']:,.0f} is being sent to your\n"
                        f"registered bank/wallet account now.\n"
                        f"You'll get an SMS once it lands (usually\n"
                        f"within minutes)."
                    )
                return END(f"Release Failed: {result['error']}")

        # 1.4 View History
        elif sub == "4":
            if level == 2: return CON("Enter your PIN:")
            if level == 3:
                farmer = _farmer(phone)
                if not farmer or not verify_and_upgrade_pin("farmers", phone, steps[2], farmer["pin_hash"]):
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
    #  NEW (fixed):  Buyer picks a CROP. System fetches all PUBLISHED
    #                farmer listings for that crop from the database and
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
    #    level 2 -> select crop
    #    level 3 -> show farmer listing; buyer picks a number
    #    level 4 -> enter quantity (bags)
    #    level 5 -> enter delivery address
    #    level 6 -> review quote request
    #    level 7 -> create order; Operations quotes before payment
    # ══════════════════════════════════════════════════════════════════════
    elif choice == "2":
        buyer_account = buyer_service.ensure_ussd_buyer(phone)
        if not buyer_account["ok"]:
            return END(buyer_account["error"] or "Could not open your buyer account.")
        if level == 1:
            return CON(
                "Buyer Portal\n"
                "1. Browse & Buy (Escrow)\n"
                "2. Post Crop Request\n"
                "3. My Orders\n"
                "4. Accept Quote & Pay"
            )

        sub = steps[1]

        # ──────────────────────────────────────────────────────────────────
        # 2.1  BROWSE MARKETPLACE → PICK FARMER → LOCK ESCROW
        # ──────────────────────────────────────────────────────────────────
        if sub == "1":

            # Step 2 — show currently-listed products (most sellers first),
            # AND accept free-text product names in the same input —
            # covers both "browse what's popular" and "search for X".
            if level == 2:
                products = list_active_products(limit=8)
                product_map = {str(i + 1): p["name"] for i, p in enumerate(products)}
                set_session(phone, {"products": product_map})

                if products:
                    lines = [f"{i+1}. {p['name']} ({p['seller_count']} sellers)"
                             for i, p in enumerate(products)]
                    return CON(
                        "What do you want to buy?\n"
                        + "\n".join(lines)
                        + "\n\nPick a number, or type a\nproduct name to search:"
                    )
                return CON(
                    "No listings yet. Type the name of\n"
                    "what you're looking for, e.g. Maize:"
                )

            # Step 3 — resolve their answer (menu number OR typed name)
            # to a product, then show published sellers for it.
            if level == 3:
                sess = get_session(phone)
                product_map = sess.get("products", {})
                answer = steps[2].strip()

                crop = product_map.get(answer)          # picked a number
                if not crop:
                    crop = answer                        # typed a name instead

                rows, matched_crop = find_farmers_for_product(crop, limit=5)
                if not rows:
                    return END(
                        f"No published sellers for '{crop}' right now.\n"
                        f"Dial *709# > 2 > 2 to post a request —\n"
                        f"an agent will match you within 24hrs."
                    )
                crop = matched_crop

                # Store farmer list in session keyed by menu number.
                # This is how we retrieve the farmer's phone later
                # without the buyer ever seeing or typing it.
                farmer_map = {
                    str(i + 1): r for i, r in enumerate(rows)
                }
                set_session(phone, {"crop": crop, "farmers": farmer_map})

                lines = [
                    f"{i+1}. {r['name']} | {r['location']} | NGN{r['price']:,.0f}/bag"
                    for i, r in enumerate(rows)
                ]
                return CON(
                    f"Published {crop} Sellers:\n"
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

            # Step 5 — quantity, then collect the delivery destination.
            if level == 5:
                sess = get_session(phone)
                if not sess or "chosen" not in sess:
                    return END("Session expired. Dial *709# to start again.")

                qty_str = steps[4].strip()
                if not qty_str.isdigit() or int(qty_str) < 1:
                    return END("Invalid quantity. Enter a whole number e.g. 5")

                chosen  = sess["chosen"]
                qty     = int(qty_str)
                product_amount = chosen["price"] * qty
                fees = calculate_full_order(product_amount)

                sess.update({"qty": qty, "total": product_amount, "fees": fees})
                set_session(phone, sess)

                return CON("Enter delivery address / town:")

            # Step 6 — show product subtotal and request quote creation.
            if level == 6:
                sess = get_session(phone)
                if not sess or "chosen" not in sess:
                    return END("Session expired. Dial *709# to start again.")
                destination = steps[5].strip()
                if len(destination) < 3:
                    return END("Enter a valid delivery address or town.")
                sess["destination"] = destination
                set_session(phone, sess)
                fees = sess["fees"]

                return CON(
                    f"-- Quote Request --\n"
                    f"Crop:    {sess['crop']}\n"
                    f"Farmer:  {sess['chosen']['name']}\n"
                    f"Bags:    {sess['qty']}\n"
                    f"Goods:   NGN {fees['product_amount']:,.0f}\n"
                    f"Fee:     NGN {fees['buyer_platform_fee']:,.0f} (buyer fee)\n"
                    f"Delivery: quoted by Operations\n"
                    f"You will approve the FINAL total before payment.\n"
                    f"1. Request Quote\n"
                    f"2. Cancel"
                )

            # Step 7 — create order and logistics quote request. No Paystack call.
            if level == 7:
                sess = get_session(phone)
                if not sess or "chosen" not in sess:
                    return END("Session expired. Dial *709# to start again.")

                if steps[6] != "1":
                    clear_session(phone)
                    return END("Cancelled. Dial *709# whenever you are ready.")

                chosen = sess["chosen"]
                result = create_order_awaiting_quote(
                    buyer_phone    = phone,
                    farmer_phone   = chosen["phone"],
                    crop           = sess["crop"],
                    quantity_bags  = sess["qty"],
                    product_amount = sess["total"],
                    delivery_address = sess["destination"],
                )
                if not result["ok"]:
                    clear_session(phone)
                    return END(f"Could not create order: {result['error']}")
                quote = create_quote_request(
                    result["txn_id"], chosen["location"], sess["destination"],
                    requested_by="buyer_ussd",
                )
                clear_session(phone)
                if not quote["ok"]:
                    return END(
                        f"Order {result['txn_id']} was created, but Operations must repair its quote request."
                    )
                return END(
                    f"Quote requested! TXN: {result['txn_id']}\n"
                    f"SowTrust Operations will confirm delivery cost.\n"
                    f"Dial *709# > 2 > 4 later to review the final total and pay."
                )

        # ──────────────────────────────────────────────────────────────────
        # 2.2  POST CROP REQUEST
        #      When no farmer is listed yet for that crop.
        #      Agent will manually match and notify buyer by SMS.
        # ──────────────────────────────────────────────────────────────────
        elif sub == "2":
            if level == 2: return CON("What crop/product do you need?\nType the name, e.g. Maize:")
            if level == 3: return CON("Enter Quantity (bags):")
            if level == 4: return CON("Enter Max Price per Bag (NGN):")
            if level == 5: return CON("Enter Your Delivery Location:")
            if level == 6:
                crop      = get_or_create_product(steps[2])
                qty       = steps[3]
                max_price = steps[4]
                location  = steps[5]
                if not crop:
                    return END(
                        "That doesn't look like a valid product name.\n"
                        "Use letters only, 2-40 characters."
                    )
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

        # 2.4 Explicitly accept a locked logistics quote before Paystack.
        elif sub == "4":
            if level == 2:
                rows = fetchall(
                    """SELECT e.txn_id, e.crop, e.buyer_total,
                              q.quoted_amount, q.expires_at
                       FROM escrow_ledger e
                       JOIN logistics_quotes q ON q.order_id=e.txn_id
                       WHERE e.buyer_phone=? AND e.status='QUOTE_READY'
                         AND q.status='LOCKED'
                         AND (q.expires_at IS NULL OR q.expires_at > datetime('now'))
                       ORDER BY q.locked_at DESC LIMIT 5""",
                    (phone,),
                )
                if not rows:
                    return END("No quote is ready yet. SowTrust will notify you when Operations locks one.")
                order_map = {str(i + 1): dict(row) for i, row in enumerate(rows)}
                set_session(phone, {"quote_orders": order_map})
                lines = [
                    f"{i+1}. {row['crop']} | Total NGN{row['buyer_total']:,.0f}"
                    for i, row in enumerate(rows)
                ]
                return CON("Quotes ready:\n" + "\n".join(lines) + "\nSelect an order:")
            if level == 3:
                order = (get_session(phone).get("quote_orders") or {}).get(steps[2])
                if not order:
                    return END("Invalid selection. Dial *709# to retry.")
                set_session(phone, {"selected_quote_order": order})
                return CON(
                    f"TXN: {order['txn_id']}\n"
                    f"Product: {order['crop']}\n"
                    f"Logistics: NGN {order['quoted_amount']:,.0f}\n"
                    f"FINAL TOTAL: NGN {order['buyer_total']:,.0f}\n"
                    f"1. Accept & Initialize Payment\n2. Cancel"
                )
            if level == 4:
                sess = get_session(phone)
                order = sess.get("selected_quote_order") if sess else None
                if not order:
                    return END("Session expired. Dial *709# to start again.")
                if steps[3] != "1":
                    clear_session(phone)
                    return END("Cancelled. Your quote was not changed.")
                accepted = accept_locked_quote(order["txn_id"], phone)
                if not accepted["ok"]:
                    clear_session(phone)
                    return END(f"Could not accept quote: {accepted['error']}")
                payment = initiate_payment_for_order(order["txn_id"], phone)
                clear_session(phone)
                if not payment["ok"]:
                    return END(f"Quote accepted, but payment setup failed: {payment['error']}")
                return END(
                    f"Transfer exactly NGN {payment['buyer_total']:,.0f} to:\n"
                    f"Acct: {payment['account_number']}\n"
                    f"Bank: {payment['bank_name']}\n"
                    f"TXN: {payment['txn_id']}"
                )

    # ══════════════════════════════════════════════════════════════════════
    #  PORTAL 3 — LOGISTICS
    # ══════════════════════════════════════════════════════════════════════
    elif choice == "3":
        if level == 1:
            return CON(
                "Logistics\n"
                "1. Track Shipment\n"
                "2. Register as Provider\n"
                "3. Available Jobs\n"
                "4. Confirm Delivery\n"
                "5. Add Bank Account"
            )

        sub = steps[1]

        # 3.1 Track a shipment (open to anyone with the TXN ID)
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

        # 3.2 Provider registration
        elif sub == "2":
            if level == 2: return CON("Enter Your Full Name:")
            if level == 3: return CON("Enter Operating Area (e.g. Lagos-Ibadan):")
            if level == 4: return CON("Enter Vehicle Type (e.g. Truck, Van, Bike):")
            if level == 5: return CON("Set 4-digit PIN:")
            if level == 6: return CON("Confirm PIN:")
            if level == 7:
                if steps[5] != steps[6]:
                    return END("PINs do not match. Dial *709# to retry.")
                result = register_provider(
                    phone=phone, name=steps[2], operating_area=steps[3],
                    vehicle_type=steps[4], pin=steps[5], registration_channel="USSD"
                )
                if not result["ok"]:
                    return END(result["error"])
                return END(
                    f"Registered! Welcome {steps[2]}.\n"
                    f"An agent will verify you shortly.\n"
                    f"Next: dial *709# > 3 > 5 to add your\n"
                    f"bank account so you can be paid."
                )

        # 3.3 Browse and accept available jobs
        elif sub == "3":
            if level == 2:
                jobs = get_available_jobs(limit=5)
                if not jobs:
                    return END("No delivery jobs available right now.\nCheck back soon.")
                job_map = {str(i + 1): dict(j) for i, j in enumerate(jobs)}
                set_session(phone, {"jobs": job_map})
                lines = [
                    f"{i+1}. {j['origin']}>{j['destination']} "
                    f"NGN{j['settlement_amount']:,.0f}"
                    for i, j in enumerate(jobs)
                ]
                return CON("Available Jobs:\n" + "\n".join(lines) + "\n\nEnter number to accept:")
            if level == 3:
                sess = get_session(phone)
                job = (sess.get("jobs") or {}).get(steps[2])
                if not job:
                    return END("Invalid selection. Dial *709# to retry.")
                clear_session(phone)
                result = assign_provider(job["txn_id"], phone)
                if not result["ok"]:
                    return END(result["error"])
                return END(
                    f"Job accepted!\nTXN: {job['txn_id']}\n"
                    f"Route: {job['origin']} to {job['destination']}\n"
                    f"You earn NGN {result['settlement_amount']:,.0f} on delivery.\n"
                    f"Collect the delivery code from the buyer\n"
                    f"AFTER handing over the goods."
                )

        # 3.4 Confirm delivery with the buyer's code -> triggers real payout
        elif sub == "4":
            if level == 2: return CON("Enter TXN ID:")
            if level == 3: return CON("Enter Delivery Code from Buyer:")
            if level == 4: return CON("Enter your PIN:")
            if level == 5:
                provider = get_provider(phone)
                if not provider or not verify_and_upgrade_pin("logistics_providers", phone, steps[4], provider["pin_hash"]):
                    return END("Invalid PIN or provider account not found.")
                result = confirm_delivery(phone, steps[2].upper(), steps[3].strip().upper())
                if not result["ok"]:
                    return END(f"Failed: {result['error']}")
                return END(
                    f"Delivery confirmed!\n"
                    f"NGN {result['settlement_amount']:,.0f} is being sent to\n"
                    f"your bank/wallet account now.\n"
                    f"You'll get an SMS once it lands."
                )

        # 3.5 Add/verify provider payout account
        elif sub == "5":
            bank_lines = "\n".join(f"{k}. {v['name']}" for k, v in config.BANKS.items())
            if level == 2: return CON(f"Select Bank/Wallet:\n{bank_lines}")
            if level == 3: return CON("Enter Account Number (10 digits):")
            if level == 4: return CON("Enter PIN to confirm:")
            if level == 5:
                provider = get_provider(phone)
                if not provider or not verify_and_upgrade_pin("logistics_providers", phone, steps[4], provider["pin_hash"]):
                    return END("Invalid PIN or provider account not found.")
                bank = config.BANKS.get(steps[2])
                if not bank:
                    return END("Invalid bank selection.")
                acct = steps[3].strip()
                if len(acct) != 10 or not acct.isdigit():
                    return END("Account number must be exactly 10 digits.")
                result = save_provider_bank_account(phone, bank["code"], acct)
                if not result["ok"]:
                    return END(f"Could not verify that account: {result['error']}")
                set_session(phone, {
                    "p_bank": bank["name"], "p_bank_code": bank["code"],
                    "p_acct": acct, "p_name": result["account_name"],
                })
                return CON(
                    f"Account Name: {result['account_name']}\n"
                    f"Bank: {bank['name']}\n\n"
                    f"Is this YOU?\n1. Yes, save\n2. No, cancel"
                )
            if level == 6:
                sess = get_session(phone)
                if not sess or "p_acct" not in sess:
                    return END("Session expired. Dial *709# to start again.")
                if steps[5] != "1":
                    clear_session(phone)
                    return END("Cancelled. No account was saved.")
                commit_provider_bank_account(
                    phone, sess["p_bank_code"], sess["p_acct"], sess["p_name"]
                )
                clear_session(phone)
                return END(f"Saved! Payouts will go to:\n{sess['p_name']}\n{sess['p_bank']}")

    # ══════════════════════════════════════════════════════════════════════
    #  PORTAL 4 — WALLET / PIN
    # ══════════════════════════════════════════════════════════════════════
    elif choice == "4":
        if level == 1:
            return CON("Wallet & PIN\n1. Check Balance\n2. Change PIN\n3. Add/Update Bank Account")

        sub = steps[1]

        if sub == "1":
            if level == 2: return CON("Enter PIN:")
            if level == 3:
                farmer = _farmer(phone)
                if not farmer or not verify_and_upgrade_pin("farmers", phone, steps[2], farmer["pin_hash"]):
                    return END("Invalid PIN or account not found.")
                payout_line = (
                    f"Payout Account: {farmer['bank_account_name']}"
                    if farmer["bank_verified_at"] else
                    "Payout Account: NOT SET (dial *709#>4>3)"
                )
                return END(
                    f"Balance:      NGN {farmer['balance']:,.0f}\n"
                    f"Credit Score: {farmer['credit_score']}\n"
                    f"KYC Status:   {farmer['kyc_status']}\n"
                    f"{payout_line}"
                )

        elif sub == "2":
            if level == 2: return CON("Enter Current PIN:")
            if level == 3: return CON("Enter New 4-digit PIN:")
            if level == 4: return CON("Confirm New PIN:")
            if level == 5:
                farmer = _farmer(phone)
                if not farmer or not verify_and_upgrade_pin("farmers", phone, steps[2], farmer["pin_hash"]):
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
                send_sms(phone, "Sowtrust: Your PIN was changed successfully.")
                return END("PIN changed successfully.")

        # 4.3 Add/Update Bank or Wallet Account — required before any real payout
        elif sub == "3":
            bank_lines = "\n".join(f"{k}. {v['name']}" for k, v in config.BANKS.items())
            if level == 2: return CON(f"Select Bank/Wallet:\n{bank_lines}")
            if level == 3: return CON("Enter Account Number (10 digits):")
            if level == 4: return CON("Enter PIN to confirm:")
            if level == 5:
                bank_choice = steps[2]
                acct_number = steps[3].strip()
                pin = steps[4]

                farmer = _farmer(phone)
                if not farmer or not verify_and_upgrade_pin("farmers", phone, pin, farmer["pin_hash"]):
                    return END("Invalid PIN.")
                bank = config.BANKS.get(bank_choice)
                if not bank:
                    return END("Invalid bank selection. Dial *709# to retry.")
                if len(acct_number) != 10 or not acct_number.isdigit():
                    return END("Account number must be exactly 10 digits.")

                result = resolve_account_number(acct_number, bank["code"])
                if not result["ok"]:
                    return END(
                        f"Could not verify that account: {result['error']}\n"
                        f"Check the number and try again."
                    )

                set_session(phone, {
                    "pending_bank": bank["name"], "pending_bank_code": bank["code"],
                    "pending_acct": acct_number, "pending_name": result["account_name"],
                })
                return CON(
                    f"Account Name: {result['account_name']}\n"
                    f"Bank: {bank['name']}\n\n"
                    f"Is this YOU? (Do not confirm someone\n"
                    f"else's account)\n"
                    f"1. Yes, save this account\n"
                    f"2. No, cancel"
                )
            if level == 6:
                sess = get_session(phone)
                if not sess or "pending_acct" not in sess:
                    return END("Session expired. Dial *709# to start again.")
                if steps[5] != "1":
                    clear_session(phone)
                    return END("Cancelled. No account was saved.")
                with get_db() as conn:
                    conn.execute(
                        """UPDATE farmers
                           SET bank_code=?, bank_account_number=?, bank_account_name=?,
                               bank_verified_at=datetime('now')
                           WHERE phone=?""",
                        (sess["pending_bank_code"], sess["pending_acct"],
                         sess["pending_name"], phone),
                    )
                clear_session(phone)
                send_sms(phone, f"Sowtrust: Payout account saved — {sess['pending_bank']} "
                                 f"({sess['pending_name']}). You can now receive real payouts.")
                return END(
                    f"Saved! Payments will now go to:\n"
                    f"{sess['pending_name']}\n{sess['pending_bank']}"
                )

    # ══════════════════════════════════════════════════════════════════════
    #  PORTAL 5 — WITHDRAW  (legacy/manual balance only — e.g. bonus credits.
    #  Normal sales settle automatically at escrow release, see Portal 1.3)
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
            if not farmer or not verify_and_upgrade_pin("farmers", phone, steps[2], farmer["pin_hash"]):
                return END("Invalid PIN or account not found.")
            if farmer["balance"] < amount:
                return END(
                    f"Insufficient balance.\n"
                    f"Available: NGN {farmer['balance']:,.0f}"
                )
            if not farmer["bank_account_number"] or not farmer["bank_verified_at"]:
                return END(
                    "No verified payout account on file.\n"
                    "Dial *709# > 4 > 3 to add your bank/wallet\n"
                    "account first, then try withdrawing again."
                )

            recipient = create_transfer_recipient(
                farmer["bank_account_name"], farmer["bank_account_number"], farmer["bank_code"]
            )
            if not recipient["ok"]:
                return END(f"Could not process withdrawal: {recipient['error']}")

            payout_ref = f"WD-{uuid.uuid4().hex[:12].upper()}"
            transfer = initiate_transfer(
                recipient["recipient_code"], amount, payout_ref,
                f"Sowtrust wallet withdrawal — {phone}"
            )
            if not transfer["ok"]:
                return END(f"Withdrawal failed: {transfer['error']}")

            with get_db() as conn:
                conn.execute(
                    "UPDATE farmers SET balance = balance - ? WHERE phone=?",
                    (amount, phone)
                )
                conn.execute(
                    "INSERT INTO audit_log(actor,action,details) VALUES(?,?,?)",
                    (phone, "WITHDRAWAL_INITIATED", f"AMT:{amount} REF:{payout_ref}")
                )
            send_sms(
                phone,
                f"Sowtrust: Withdrawal of NGN {amount:,.0f} to "
                f"{farmer['bank_account_name']} is processing."
            )
            return END(
                f"Withdrawal of NGN {amount:,.0f} initiated to\n"
                f"{farmer['bank_account_name']}.\n"
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
                "3. View My Recruits\n"
                "4. Verify Logistics Provider"
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
                if len(pin) != 4 or not pin.isdigit():
                    return END("PIN must be exactly 4 digits.")
                if _agent(phone):
                    return END("Agent account already exists.")
                with get_db() as conn:
                    conn.execute(
                        """INSERT INTO agents
                           (name, phone, normalized_phone, registration_channel,
                            verification_status, account_status, phone_verified,
                            pin_hash, location)
                           VALUES (?, ?, ?, 'USSD', 'UNVERIFIED', 'ACTIVE', 1, ?, ?)""",
                        (name.title(), phone, phone, hash_pin(pin), location.title())
                    )
                    agent_id = conn.execute(
                        "SELECT id FROM agents WHERE normalized_phone=?", (phone,)
                    ).fetchone()[0]
                    conn.execute(
                        "INSERT INTO audit_log(actor, action, details) VALUES (?, 'AGENT_REGISTERED', 'CHANNEL:USSD')",
                        (phone,),
                    )
                identity_service.ensure_user_role(
                    phone, "AGENT", name.title(), "USSD", True, agent_id
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
                if not agent or not verify_and_upgrade_pin("agents", phone, steps[2], agent["pin_hash"]):
                    return END("Invalid Agent PIN.")
                return CON("Enter Farmer Phone Number to Verify:")
            if level == 4:
                farmer_phone = normalize_phone(steps[3])
                if not farmer_phone:
                    return END("Enter a valid farmer phone number.")
                farmer = fetchone(
                    "SELECT * FROM farmers WHERE normalized_phone=? OR phone=?",
                    (farmer_phone, farmer_phone),
                )
                if not farmer:
                    return END(
                        f"Farmer not found.\n"
                        f"Ask them to register first via *709# > 1 > 1."
                    )
                verified = AgentIncentiveService.record_farmer_verification(
                    phone, farmer_phone, actor=phone
                )
                if not verified["ok"]:
                    return END(verified["error"])
                send_sms(
                    farmer_phone,
                    f"Sowtrust: {farmer['name']}, your account is VERIFIED!\n"
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
                if not agent or not verify_and_upgrade_pin("agents", phone, steps[2], agent["pin_hash"]):
                    return END("Invalid PIN.")
                earnings = AgentIncentiveService.get_agent_summary(agent["id"])
                return END(
                    f"Agent: {agent['name']}\n"
                    f"Location: {agent['location']}\n"
                    f"Farmers Verified: {agent['recruits']}\n"
                    f"Approved Earnings: NGN {earnings.get('approved_kobo', 0) / 100:,.0f}\n"
                    f"Paid to Date: NGN {earnings.get('paid_kobo', 0) / 100:,.0f}"
                )

        # 6.4 Verify a logistics provider — same trust model as farmers.
        # Providers can't accept jobs or be paid until an agent verifies them.
        elif sub == "4":
            if level == 2: return CON("Enter Agent PIN:")
            if level == 3:
                agent = _agent(phone)
                if not agent or not verify_and_upgrade_pin("agents", phone, steps[2], agent["pin_hash"]):
                    return END("Invalid Agent PIN.")
                return CON("Enter Provider Phone Number to Verify:")
            if level == 4:
                provider_phone = normalize_phone(steps[3])
                if not provider_phone:
                    return END("Enter a valid provider phone number.")
                provider = fetchone(
                    """SELECT * FROM logistics_providers
                       WHERE normalized_phone=? OR phone=?""",
                    (provider_phone, provider_phone),
                )
                if not provider:
                    return END(
                        "Provider not found.\n"
                        "Ask them to register via *709# > 3 > 2."
                    )
                with get_db() as conn:
                    conn.execute(
                        """UPDATE logistics_providers
                           SET kyc_status='VERIFIED', verification_status='VERIFIED',
                               updated_at=datetime('now') WHERE id=?""",
                        (provider["id"],)
                    )
                    conn.execute(
                        "INSERT INTO audit_log(actor,action,details) VALUES(?,?,?)",
                        (phone, "LOGISTICS_KYC_VERIFIED", f"Provider:{provider_phone}")
                    )
                send_sms(
                    provider_phone,
                    f"Sowtrust: {provider['name']}, your logistics account is VERIFIED!\n"
                    f"View jobs: *709# > 3 > 3"
                )
                return END(f"Provider {provider['name']} is now VERIFIED.")

    return END("Invalid option. Dial *709# to start again.")
