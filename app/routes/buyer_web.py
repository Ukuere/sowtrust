"""
Sowtrust — Buyer Web App.

Server-rendered (Flask + Jinja2) rather than a separate API + frontend —
same Flask service that already handles /ussd and /webhooks/paystack, so
no new hosting, no CORS, and it reuses the existing session/security
utilities.

Routes:
  GET/POST /buyer/register
  GET/POST /buyer/login
  GET      /buyer/logout
  GET      /buyer                          -> browse products
  GET      /buyer/product/<crop>           -> farmers selling that crop
  GET/POST /buyer/checkout/<farmer_phone>/<crop> -> confirm qty, start payment
  GET/POST /buyer/kyc                      -> submit ID/business verification
  GET      /buyer/orders                   -> order history
  GET      /buyer/orders/<txn_id>          -> order status detail

Auth: Flask's signed-cookie session (uses FLASK_SECRET_KEY, already in
your config) holding session["buyer_phone"] — deliberately NOT the same
mechanism as the USSD session_store (that's DB-backed and keyed per-call,
built for a stateless USSD gateway; a browser can hold a normal signed
cookie across requests, so there's no gunicorn-multi-worker problem here).
"""
from functools import wraps
from flask import Blueprint, current_app, render_template, request, redirect, url_for, session, flash

from app.services import (
    buyer_service, product_service, fee_service, escrow_service,
    logistics_service, email_service, document_storage, dispute_service,
)
from app.models.database import fetchall, fetchone

buyer_web_bp = Blueprint(
    "buyer_web", __name__, url_prefix="/buyer", template_folder="templates"
)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("buyer_phone"):
            return redirect(url_for("buyer_web.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@buyer_web_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        result = buyer_service.register_buyer(
            phone=request.form.get("phone", ""),
            password=request.form.get("password", ""),
            name=request.form.get("name", ""),
            business_name=request.form.get("business_name", ""),
            email=request.form.get("email", ""),
            delivery_address=request.form.get("delivery_address", ""),
            city=request.form.get("city", ""),
            state=request.form.get("state", ""),
            buyer_type=request.form.get("buyer_type", ""),
        )
        if not result["ok"]:
            if result.get("activation_required"):
                flash(result["error"], "success")
                return redirect(url_for(
                    "account_activation.activate_buyer", phone=result["phone"]
                ))
            flash(result["error"], "error")
            return render_template(
                "buyer/register.html", form=request.form, buyer_types=buyer_service.BUYER_TYPES
            ), 400

        verify_url = url_for("buyer_web.verify_email", token=result["verification_token"], _external=True)
        email_service.send_verification_email(result["email"], verify_url)

        if current_app.config.get("PHONE_OTP_TEST_BYPASS"):
            buyer_service.mark_phone_verified_for_test(result["phone"])
            session["buyer_phone"] = result["phone"]
            return redirect(url_for("buyer_web.browse"))

        from app.services import identity_service
        otp = identity_service.request_otp(
            result["phone"], "BUYER", "ACTIVATE",
            request.remote_addr or "",
        )
        if otp["ok"]:
            session["activation_phone_BUYER"] = result["phone"]
            flash("Account created. Verify your phone, then check your email.", "success")
        else:
            flash(f"Account created, but the verification code was not delivered: {otp['error']}", "error")
        return redirect(url_for("account_activation.activate_buyer", phone=result["phone"]))
    return render_template("buyer/register.html", form={}, buyer_types=buyer_service.BUYER_TYPES)


@buyer_web_bp.route("/verify-email/<token>")
def verify_email(token):
    result = buyer_service.verify_email(token)
    if not result["ok"]:
        flash(result["error"], "error")
    else:
        flash("Email verified — thanks.", "success")
    return redirect(url_for("buyer_web.login") if not session.get("buyer_phone")
                     else url_for("buyer_web.browse"))


@buyer_web_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        result = buyer_service.authenticate_buyer(
            phone=request.form.get("phone", ""),
            password=request.form.get("password", ""),
        )
        if not result["ok"]:
            if result.get("activation_required"):
                flash(result["error"], "success")
                return redirect(url_for(
                    "account_activation.activate_buyer", phone=result["phone"]
                ))
            flash(result["error"], "error")
            return render_template("buyer/login.html"), 400
        session["buyer_phone"] = result["buyer"]["phone"]
        session.permanent = True
        return redirect(request.args.get("next") or url_for("buyer_web.browse"))
    return render_template("buyer/login.html")


@buyer_web_bp.route("/logout")
def logout():
    session.pop("buyer_phone", None)
    flash("Logged out.", "success")
    return redirect(url_for("buyer_web.login"))


@buyer_web_bp.route("/")
@login_required
def browse():
    products = product_service.list_active_products(limit=24)
    buyer = buyer_service.get_buyer(session["buyer_phone"])
    return render_template("buyer/browse.html", products=products,
                            buyer_kyc_status=buyer.get("kyc_status") if buyer else None)


@buyer_web_bp.route("/product/<crop>")
@login_required
def product_detail(crop):
    farmers, canonical_crop = product_service.find_farmers_for_product(crop, limit=15)
    buyer = buyer_service.get_buyer(session["buyer_phone"])
    return render_template(
        "buyer/product_detail.html", crop=canonical_crop or crop, farmers=farmers,
        buyer=buyer
    )


@buyer_web_bp.route("/checkout/<farmer_phone>/<crop>", methods=["GET", "POST"])
@login_required
def checkout(farmer_phone, crop):
    # Spec section 1 & 7: KYC must be checked server-side, not just hidden
    # in the frontend, and checked again here even though login_required
    # already ran — a buyer's status can change between page loads.
    if not buyer_service.is_checkout_eligible(session["buyer_phone"]):
        flash("Complete identity verification before placing an order.", "error")
        return redirect(url_for("buyer_web.kyc"))

    farmer = fetchone(
        """SELECT * FROM farmers
           WHERE phone = ? AND LOWER(crop) = LOWER(?)
             AND price > 0 AND kyc_status = 'VERIFIED' AND is_active = 1
             AND COALESCE(listing_status, 'PUBLISHED') = 'PUBLISHED'""",
        (farmer_phone, crop),
    )
    if not farmer:
        flash("That listing is no longer available.", "error")
        return redirect(url_for("buyer_web.browse"))
    farmer = dict(farmer)

    if request.method == "POST":
        # Re-check immediately before the money-moving call too — the
        # decorator/redirect above covers page loads, this covers the
        # actual submit, closing the gap where a buyer opens checkout
        # while verified and submits after being suspended.
        if not buyer_service.is_checkout_eligible(session["buyer_phone"]):
            flash("Complete identity verification before placing an order.", "error")
            return redirect(url_for("buyer_web.kyc"))

        try:
            quantity = int(request.form.get("quantity", "0"))
        except ValueError:
            quantity = 0
        if quantity <= 0:
            flash("Enter a valid quantity.", "error")
            return render_template("buyer/checkout.html", farmer=farmer, quantity=1, preview=None)

        buyer = buyer_service.get_buyer(session["buyer_phone"])
        product_amount = farmer["price"] * quantity
        result = escrow_service.create_order_awaiting_quote(
            buyer_phone=session["buyer_phone"],
            farmer_phone=farmer["phone"],
            crop=farmer["crop"],
            quantity_bags=quantity,
            product_amount=product_amount,
            buyer_name=buyer.get("name"),
            delivery_address=buyer.get("delivery_address"),
            delivery_city=buyer.get("city"),
            delivery_state=buyer.get("state"),
            # Logistics quoting is the other half of step 3 (the logistics
            # dashboard) — until that's wired in, orders are product-only,
            # matching how the USSD flow works today.
        )
        if not result["ok"]:
            flash(f"Could not create order: {result['error']}", "error")
            preview = fee_service.calculate_full_order(product_amount, 0.0)
            return render_template("buyer/checkout.html", farmer=farmer, quantity=quantity, preview=preview)

        # Snapshot delivery info onto the order itself (spec section 7) —
        # a plain post-insert UPDATE rather than changing
        # initiate_escrow_payment()'s signature, so escrow_service's
        # already-reviewed fee logic stays untouched.
        quote_result = logistics_service.create_quote_request(
            result["txn_id"],
            farmer["location"],
            ", ".join(part for part in [
                buyer.get("delivery_address"), buyer.get("city"), buyer.get("state")
            ] if part),
            requested_by="buyer_web",
        )
        if not quote_result["ok"]:
            flash(f"Order created, but quote request failed: {quote_result['error']}", "error")
            return redirect(url_for("buyer_web.order_detail", txn_id=result["txn_id"]))

        flash("Order created. SowTrust Operations will confirm logistics before payment.", "success")
        return redirect(url_for("buyer_web.order_detail", txn_id=result["txn_id"]))

    quantity = max(1, int(request.args.get("quantity", 1) or 1))
    preview = fee_service.calculate_full_order(farmer["price"] * quantity, 0.0)
    return render_template("buyer/checkout.html", farmer=farmer, quantity=quantity, preview=preview)


@buyer_web_bp.route("/kyc", methods=["GET", "POST"])
@login_required
def kyc():
    buyer = buyer_service.get_buyer(session["buyer_phone"])
    is_business = buyer.get("buyer_type") in buyer_service.BUSINESS_BUYER_TYPES

    if request.method == "POST":
        id_doc_result = document_storage.save_kyc_document(request.files.get("id_document"))
        if not id_doc_result["ok"]:
            flash(id_doc_result["error"], "error")
            return render_template("buyer/kyc.html", buyer=buyer, is_business=is_business,
                                    id_types=buyer_service.ID_TYPES)

        business_doc_path = ""
        if is_business:
            # Check the plain text field before attempting the file save —
            # a buyer who left both blank should see "enter your CAC
            # number", not a generic "no file uploaded" that doesn't tell
            # them which of the two business fields is the problem.
            if not request.form.get("business_reg_number", "").strip():
                flash("Enter your CAC registration number.", "error")
                return render_template("buyer/kyc.html", buyer=buyer, is_business=is_business,
                                        id_types=buyer_service.ID_TYPES)

            biz_doc_result = document_storage.save_kyc_document(
                request.files.get("business_reg_document"), subfolder="kyc_business"
            )
            if not biz_doc_result["ok"]:
                flash(f"CAC document: {biz_doc_result['error']}", "error")
                return render_template("buyer/kyc.html", buyer=buyer, is_business=is_business,
                                        id_types=buyer_service.ID_TYPES)
            business_doc_path = biz_doc_result["path"]

        result = buyer_service.submit_kyc(
            phone=session["buyer_phone"],
            id_type=request.form.get("id_type", ""),
            id_number=request.form.get("id_number", ""),
            id_document_path=id_doc_result["path"],
            business_reg_number=request.form.get("business_reg_number", ""),
            business_reg_document_path=business_doc_path,
            authorized_rep_name=request.form.get("authorized_rep_name", ""),
            authorized_rep_id_number=request.form.get("authorized_rep_id_number", ""),
        )
        if not result["ok"]:
            flash(result["error"], "error")
            return render_template("buyer/kyc.html", buyer=buyer, is_business=is_business,
                                    id_types=buyer_service.ID_TYPES)

        flash("Verification submitted — we'll review it shortly.", "success")
        return redirect(url_for("buyer_web.browse"))

    return render_template("buyer/kyc.html", buyer=buyer, is_business=is_business,
                            id_types=buyer_service.ID_TYPES)


@buyer_web_bp.route("/orders")
@login_required
def orders():
    rows = fetchall(
        "SELECT * FROM escrow_ledger WHERE buyer_phone = ? ORDER BY txn_id DESC LIMIT 50",
        (session["buyer_phone"],),
    )
    return render_template("buyer/orders.html", orders=[dict(r) for r in rows])


@buyer_web_bp.route("/orders/<txn_id>")
@login_required
def order_detail(txn_id):
    row = fetchone(
        "SELECT * FROM escrow_ledger WHERE txn_id = ? AND buyer_phone = ?",
        (txn_id, session["buyer_phone"]),
    )
    if not row:
        flash("Order not found.", "error")
        return redirect(url_for("buyer_web.orders"))
    quote = logistics_service.get_quote_for_order(txn_id)
    quote_replacement = logistics_service.get_pending_quote_replacement(txn_id)
    dispute = dispute_service.get_dispute_for_order(txn_id)
    return render_template(
        "buyer/order_detail.html",
        order=dict(row),
        quote=quote,
        quote_replacement=quote_replacement,
        dispute=dispute,
        dispute_reasons=dispute_service.DISPUTE_REASONS,
    )


@buyer_web_bp.route("/orders/<txn_id>/accept-quote", methods=["POST"])
@login_required
def accept_quote(txn_id):
    accept = logistics_service.accept_locked_quote(txn_id, session["buyer_phone"])
    if not accept["ok"]:
        flash(accept["error"], "error")
        return redirect(url_for("buyer_web.order_detail", txn_id=txn_id))

    payment = escrow_service.initiate_payment_for_order(txn_id, session["buyer_phone"])
    if not payment["ok"]:
        flash(payment["error"], "error")
        return redirect(url_for("buyer_web.order_detail", txn_id=txn_id))

    flash("Quote accepted. Transfer the exact amount shown to lock escrow.", "success")
    return redirect(url_for("buyer_web.order_detail", txn_id=txn_id))


@buyer_web_bp.post("/orders/<txn_id>/accept-replacement-quote")
@login_required
def accept_replacement_quote(txn_id):
    approval = logistics_service.approve_quote_replacement(
        txn_id, session["buyer_phone"]
    )
    if not approval["ok"]:
        flash(approval["error"], "error")
        return redirect(url_for("buyer_web.order_detail", txn_id=txn_id))
    payment = escrow_service.initiate_payment_for_order(txn_id, session["buyer_phone"])
    if not payment["ok"]:
        flash(payment["error"], "error")
    else:
        flash("Replacement quote approved. Transfer the exact updated total shown.", "success")
    return redirect(url_for("buyer_web.order_detail", txn_id=txn_id))


@buyer_web_bp.route("/product/<crop>/notify", methods=["POST"])
@login_required
def notify_product(crop):
    buyer = buyer_service.get_buyer(session["buyer_phone"])
    result = product_service.create_buyer_product_interest(
        buyer_phone=session["buyer_phone"],
        crop=crop,
        quantity=request.form.get("quantity", 1),
        location=", ".join(part for part in [
            buyer.get("delivery_address"), buyer.get("city"), buyer.get("state")
        ] if part),
    )
    if not result["ok"]:
        flash(result["error"], "error")
    else:
        flash("We'll notify you when this product is published by a farmer.", "success")
    return redirect(url_for("buyer_web.product_detail", crop=crop))


@buyer_web_bp.route("/orders/<txn_id>/dispute", methods=["POST"])
@login_required
def open_dispute(txn_id):
    result = dispute_service.create_buyer_dispute(
        txn_id=txn_id,
        buyer_phone=session["buyer_phone"],
        reason=request.form.get("reason", ""),
        details=request.form.get("details", ""),
    )
    if not result["ok"]:
        flash(result["error"], "error")
    else:
        flash(f"Dispute {result['dispute_id']} opened. Operations will review it.", "success")
    return redirect(url_for("buyer_web.order_detail", txn_id=txn_id))
