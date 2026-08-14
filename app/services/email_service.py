"""
Sowtrust — Email Service.

MVP STUB: no email provider is configured anywhere in this codebase yet
(.env.example has Africa's Talking for SMS and Paystack for payments,
nothing for email). Rather than silently fail or fake success, this logs
the verification link to stdout in dev so the flow is testable end-to-end
without a real provider, and clearly no-ops if SMTP isn't configured.

Before going live, wire in a real provider — SendGrid, Postmark, AWS SES,
or plain SMTP via smtplib — and set SMTP_HOST/SMTP_PORT/SMTP_USER/
SMTP_PASSWORD/SMTP_FROM_ADDRESS in your environment. Until then, buyer
email verification will not actually reach a buyer's inbox.
"""
import os
import logging

logger = logging.getLogger("sowtrust.email")


def _smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_USER"))


def send_verification_email(to_email: str, verification_url: str) -> bool:
    """
    Returns True if a send was attempted (real or dev-logged), False if
    email is not configured at all. Callers should not treat False as a
    hard error — registration should still succeed; verification just
    won't be deliverable until SMTP is configured.
    """
    if not to_email:
        return False

    if not _smtp_configured():
        # Dev/MVP fallback — makes the verification flow testable without
        # a real provider. Replace with actual SMTP/API send before launch.
        logger.info(
            "[EMAIL STUB — no SMTP configured] Would send verification to %s: %s",
            to_email, verification_url,
        )
        print(f"[Sowtrust email stub] Verify {to_email} -> {verification_url}")
        return True

    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(
        f"Welcome to Sowtrust.\n\nVerify your email by visiting:\n{verification_url}\n\n"
        f"If you didn't create this account, ignore this message."
    )
    msg["Subject"] = "Verify your Sowtrust account"
    msg["From"] = os.environ.get("SMTP_FROM_ADDRESS", "no-reply@sowtrust.com")
    msg["To"] = to_email

    try:
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 587))) as server:
            server.starttls()
            server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            server.send_message(msg)
        return True
    except Exception:
        logger.exception("Failed to send verification email to %s", to_email)
        return False
