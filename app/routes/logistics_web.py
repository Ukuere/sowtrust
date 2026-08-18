"""
Sowtrust — Logistics Provider Web Dashboard (spec section 12: "mobile-
friendly web dashboard" for logistics, distinct from farmers' USSD-first
approach).

Auth is phone + 4-digit PIN against logistics_providers.pin_hash — the
SAME field and verify_pin()/verify_and_upgrade_pin() the step 2 build
already created for this table. No new password system; a provider
account created any other way (future USSD, agent-assisted signup) logs
into this dashboard with the same PIN.

Routes:
  GET/POST /logistics/register
  GET/POST /logistics/login
  GET      /logistics/logout
  GET      /logistics/                    -> dashboard home (status + available jobs)
  GET/POST /logistics/kyc                 -> submit ID/vehicle documents
  GET/POST /logistics/bank-account        -> set payout destination
  POST     /logistics/jobs/<txn_id>/accept
  GET      /logistics/jobs                -> my assigned/active jobs
  GET/POST /logistics/jobs/<txn_id>/confirm-delivery

Every money-moving/status-changing action (accept job, confirm delivery)
calls straight into the existing logistics_service functions — this file
adds zero new business logic beyond form handling and the checkout-style
KYC gate pattern already used on the buyer side.
"""
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash

from app.services import logistics_service, document_storage, identity_service
from app.utils.phone import normalize_phone

logistics_web_bp = Blueprint(
    "logistics_web", __name__, url_prefix="/logistics", template_folder="templates"
)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("provider_phone"):
            return redirect(url_for("logistics_web.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@logistics_web_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        phone = normalize_phone(request.form.get("phone", ""))
        if not phone:
            flash("Enter a valid phone number, e.g. 08011112222.", "error")
            return render_template("logistics/register.html", form=request.form), 400

        result = logistics_service.register_provider(
            phone=phone,
            name=request.form.get("name", "").strip(),
            operating_area=request.form.get("operating_area", "").strip(),
            vehicle_type=request.form.get("vehicle_type", "").strip(),
            pin=request.form.get("pin", "").strip(),
            business_name=request.form.get("business_name", "").strip() or None,
            registration_channel="WEB",
        )
        if not result["ok"]:
            flash(result["error"], "error")
            return render_template("logistics/register.html", form=request.form), 400
        session["provider_phone"] = phone
        flash("Account created — submit your verification documents to start accepting jobs.", "success")
        return redirect(url_for("logistics_web.kyc"))
    return render_template("logistics/register.html", form={})


@logistics_web_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        phone = normalize_phone(request.form.get("phone", ""))
        pin = request.form.get("pin", "").strip()
        result = identity_service.authenticate_role_pin(phone, "LOGISTICS", pin) if phone else {
            "ok": False, "error": "Enter a valid phone number."
        }
        if not result["ok"]:
            flash(result["error"], "error")
            return render_template("logistics/login.html"), 400
        session["provider_phone"] = result["phone"]
        session.permanent = True
        return redirect(request.args.get("next") or url_for("logistics_web.dashboard"))
    return render_template("logistics/login.html")


@logistics_web_bp.route("/logout")
def logout():
    session.pop("provider_phone", None)
    flash("Logged out.", "success")
    return redirect(url_for("logistics_web.login"))


@logistics_web_bp.route("/")
@login_required
def dashboard():
    provider = logistics_service.get_provider(session["provider_phone"])
    if not provider:
        session.pop("provider_phone", None)
        flash("Account not found.", "error")
        return redirect(url_for("logistics_web.login"))

    jobs = []
    if provider["kyc_status"] == "VERIFIED" and provider["bank_verified_at"]:
        jobs = logistics_service.get_available_jobs(session["provider_phone"], limit=20)

    return render_template("logistics/dashboard.html", provider=dict(provider), jobs=jobs)


@logistics_web_bp.route("/kyc", methods=["GET", "POST"])
@login_required
def kyc():
    provider = logistics_service.get_provider(session["provider_phone"])

    if request.method == "POST":
        id_doc_result = document_storage.save_kyc_document(
            request.files.get("id_document"), subfolder="logistics_kyc"
        )
        if not id_doc_result["ok"]:
            flash(id_doc_result["error"], "error")
            return render_template("logistics/kyc.html", provider=provider,
                                    id_types=logistics_service.ID_TYPES)

        vehicle_doc_result = document_storage.save_kyc_document(
            request.files.get("vehicle_registration_document"), subfolder="logistics_kyc"
        )
        if not vehicle_doc_result["ok"]:
            flash(f"Vehicle registration document: {vehicle_doc_result['error']}", "error")
            return render_template("logistics/kyc.html", provider=provider,
                                    id_types=logistics_service.ID_TYPES)

        license_doc_path = ""
        if request.files.get("drivers_license_document") and request.files["drivers_license_document"].filename:
            license_result = document_storage.save_kyc_document(
                request.files.get("drivers_license_document"), subfolder="logistics_kyc"
            )
            if license_result["ok"]:
                license_doc_path = license_result["path"]

        result = logistics_service.submit_provider_kyc(
            phone=session["provider_phone"],
            id_type=request.form.get("id_type", ""),
            id_number=request.form.get("id_number", ""),
            id_document_path=id_doc_result["path"],
            drivers_license_number=request.form.get("drivers_license_number", ""),
            drivers_license_path=license_doc_path,
            vehicle_registration_document_path=vehicle_doc_result["path"],
        )
        if not result["ok"]:
            flash(result["error"], "error")
            return render_template("logistics/kyc.html", provider=provider,
                                    id_types=logistics_service.ID_TYPES)

        flash("Verification submitted — we'll review it shortly.", "success")
        return redirect(url_for("logistics_web.dashboard"))

    return render_template("logistics/kyc.html", provider=provider,
                            id_types=logistics_service.ID_TYPES)


@logistics_web_bp.route("/bank-account", methods=["GET", "POST"])
@login_required
def bank_account():
    if request.method == "POST":
        bank_code = request.form.get("bank_code", "").strip()
        account_number = request.form.get("account_number", "").strip()

        check = logistics_service.save_provider_bank_account(
            session["provider_phone"], bank_code, account_number
        )
        if not check["ok"]:
            flash(check["error"], "error")
            return render_template("logistics/bank_account.html")

        result = logistics_service.commit_provider_bank_account(
            session["provider_phone"], bank_code, account_number, check["account_name"]
        )
        if not result["ok"]:
            flash(result.get("error", "Could not save account."), "error")
            return render_template("logistics/bank_account.html")

        flash(f"Payout account saved: {check['account_name']}.", "success")
        return redirect(url_for("logistics_web.dashboard"))

    return render_template("logistics/bank_account.html")


@logistics_web_bp.route("/jobs")
@login_required
def jobs():
    from app.models.database import fetchall
    rows = fetchall(
        """SELECT l.*, e.crop, e.quantity_bags, e.buyer_phone
           FROM logistics_log l
           JOIN escrow_ledger e ON e.txn_id = l.txn_id
           JOIN logistics_providers p ON p.id = l.provider_id
           WHERE p.phone = ?
           ORDER BY l.dispatched_at DESC""",
        (session["provider_phone"],),
    )
    return render_template("logistics/jobs.html", jobs=[dict(r) for r in rows])


@logistics_web_bp.route("/jobs/<txn_id>/accept", methods=["POST"])
@login_required
def accept_job(txn_id):
    result = logistics_service.assign_provider(txn_id, session["provider_phone"])
    if not result["ok"]:
        flash(result["error"], "error")
        return redirect(url_for("logistics_web.dashboard"))
    flash("Job accepted — delivery code sent to the buyer.", "success")
    return redirect(url_for("logistics_web.jobs"))


@logistics_web_bp.route("/jobs/<txn_id>/confirm-delivery", methods=["GET", "POST"])
@login_required
def confirm_delivery(txn_id):
    if request.method == "POST":
        code = request.form.get("delivery_code", "").strip()
        result = logistics_service.confirm_delivery(session["provider_phone"], txn_id, code)
        if not result["ok"]:
            flash(result["error"], "error")
            return render_template("logistics/confirm_delivery.html", txn_id=txn_id)
        flash(f"Delivery confirmed — NGN {result['settlement_amount']:,.0f} payout initiated.", "success")
        return redirect(url_for("logistics_web.jobs"))
    return render_template("logistics/confirm_delivery.html", txn_id=txn_id)
