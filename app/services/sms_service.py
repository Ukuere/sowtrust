"""
Sowtrust — SMS notification service (Africa's Talking).
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
        f"Sowtrust: Payment RECEIVED & LOCKED!\n"
        f"Crop: {crop}\nAmount: NGN {amount:,.0f}\n"
        f"TXN: {txn_id}\n"
        f"Deliver the goods, then collect the\n"
        f"release code from the buyer to get paid."
    )
    send_sms(farmer_phone, msg)
    send_sms(buyer_phone,
             f"Sowtrust: Your payment of NGN {amount:,.0f} for {crop} was received. "
             f"Escrow locked. TXN: {txn_id}")


def notify_release_code(buyer_phone: str, release_code: str, txn_id: str):
    send_sms(buyer_phone,
             f"Sowtrust Release Code: {release_code}\nTXN: {txn_id}\n"
             f"Give this code to farmer ONLY after goods are received.")


def notify_payment_released(farmer_phone: str, amount: float, txn_id: str):
    send_sms(farmer_phone,
             f"Sowtrust: NGN {amount:,.0f} has been paid to your "
             f"bank/wallet account!\nTXN: {txn_id}")


def notify_logistics(courier_phone: str, origin: str, destination: str,
                     crop: str, txn_id: str):
    send_sms(courier_phone,
             f"Sowtrust Logistics: Pickup {crop} from {origin}. Deliver to {destination}. TXN: {txn_id}")
