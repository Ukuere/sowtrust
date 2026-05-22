"""
AgriHub — SMS notification service (Africa's Talking).
"""
import africastalking
from config.settings import config

africastalking.initialize(config.AT_USERNAME, config.AT_API_KEY)
_sms = africastalking.SMS


def send_sms(phone: str, message: str) -> bool:
    """Send SMS. Returns True on success, False on failure."""
    try:
        response = _sms.send(message, [phone])
        recipients = response.get("SMSMessageData", {}).get("Recipients", [])
        return any(r.get("status") == "Success" for r in recipients)
    except Exception as e:
        print(f"[SMS ERROR] {phone}: {e}")
        return False


# ── Templated notifications ─────────────────────────────────────────────────

def notify_escrow_locked(farmer_phone: str, buyer_phone: str,
                         crop: str, amount: float, txn_id: str):
    msg = (
        f"AgriHub: Payment LOCKED!\n"
        f"Crop: {crop}\nAmount: NGN {amount:,.0f}\n"
        f"TXN: {txn_id}\n"
        f"Deliver goods & collect release code from buyer."
    )
    send_sms(farmer_phone, msg)
    send_sms(buyer_phone,
             f"AgriHub: Your escrow of NGN {amount:,.0f} for {crop} is locked. TXN: {txn_id}")


def notify_release_code(buyer_phone: str, release_code: str, txn_id: str):
    send_sms(buyer_phone,
             f"AgriHub Release Code: {release_code}\nTXN: {txn_id}\n"
             f"Give this code to farmer ONLY after goods are received.")


def notify_payment_released(farmer_phone: str, amount: float, txn_id: str):
    send_sms(farmer_phone,
             f"AgriHub: NGN {amount:,.0f} credited to your wallet!\nTXN: {txn_id}\nDial *709# to withdraw.")


def notify_logistics(courier_phone: str, origin: str, destination: str,
                     crop: str, txn_id: str):
    send_sms(courier_phone,
             f"AgriHub Logistics: Pickup {crop} from {origin}. Deliver to {destination}. TXN: {txn_id}")
