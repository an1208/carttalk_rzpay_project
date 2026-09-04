import os
import razorpay
from dotenv import load_dotenv

load_dotenv()

client = razorpay.Client(
    auth=(os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"])
)


def create_order(amount_paise: int, receipt: str):
    """Creates a real Razorpay test-mode order. amount_paise must be an int (paise, not rupees)."""
    return client.order.create({
        "amount": amount_paise,
        "currency": "INR",
        "receipt": receipt,
    })


def create_payment_link(amount_paise: int, description: str, reference_id: str):
    """Creates a real, payable Razorpay Payment Link. In test mode, this can actually
    be completed end-to-end using Razorpay's test UPI id 'success@razorpay'.
    Note: Payment Links API uses 'reference_id', NOT 'receipt' (that's an Orders API field)."""
    return client.payment_link.create({
        "amount": amount_paise,
        "currency": "INR",
        "description": description,
        "reference_id": reference_id,
        "reminder_enable": False,
    })


def fetch_payment_link(payment_link_id: str):
    """Fetches the current status of a payment link: 'created', 'paid', 'cancelled', or 'expired'."""
    return client.payment_link.fetch(payment_link_id)


