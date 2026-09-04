import os
import json
import re
import time
from groq import Groq
from dotenv import load_dotenv

from catalog import search_catalog
from audit_log import (
    log_intent, log_cart, log_confirmation, log_charge,
    has_confirmed_cart, get_latest_confirmation_id,
    get_cart_total, has_existing_charge,
    log_payment, update_payment_status, get_latest_payment
)
from razorpay_client import create_order, create_payment_link, fetch_payment_link

load_dotenv()
client = Groq(api_key=os.environ["GROQ_API_KEY"])

MODEL = "openai/gpt-oss-20b"
MAX_ORDER_PAISE = 200000  # rupees 2000 is set as the cap for the test mode 

current_session = {"intent_id": None, "cart_id": None, "charge_id": None}


def start_session(session_id, user_message):
    intent_id = log_intent(session_id, user_message)
    current_session["intent_id"] = intent_id


# ---------------------------------------------------------------------------
# Model places rate aware calls.
# The rate limit was getting hit with a fixed wait time of 3s before every try.
# Reads Groq's "try again in X s" message and waits that long instead of a
# fixed guess. fixes both the 429 rate-limit case and the intermittent
# malformed-tool-name (Harmony channel leakage) case, since both are usually
# transient and succeed on a retry.
# ---------------------------------------------------------------------------


def call_model_with_retry(messages, tools, max_retries=3):
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools,
            )
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            error_str = str(e)
            wait_time = 3.0
            match = re.search(r"try again in ([\d.]+)s", error_str)
            if match:
                wait_time = float(match.group(1)) + 0.5
            print(f"Retrying after error ({wait_time:.1f}s wait): {e}")
            time.sleep(wait_time)


# ---------------------------------------------------------------------------
# Tool functions.
# Each accepts **kwargs defensively -- gpt-oss-20b on Groq occasionally
# invents extra/renamed arguments (e.g. a stray 'confirm' kwarg) that don't
# match the declared schema. Rather than crash, these fall back to
# current_session state where possible.
# ---------------------------------------------------------------------------
def tool_search_catalog(query=None, **kwargs):
    if query is None:
        return {"error": "No search query provided."}
    return search_catalog(query)


def tool_build_cart(items=None, **kwargs):
    if not items:
        return {"error": "No items provided."}

    resolved = []
    total = 0
    for item in items:
        matches = search_catalog(f"{item.get('size', '')} {item.get('name', '')}".strip())

        if not matches:
            return {"error": f"No catalog match for {item}"}

        if len(matches) > 1:
            options = ", ".join(
                f"{m['name']} ({m['size'] or 'no size specified'})" for m in matches
            )
            return {
                "error": (
                    f"Ambiguous item '{item.get('name')}' -- multiple matches found. "
                    f"Ask the user to pick one of: {options}"
                )
            }

        chosen = matches[0]
        resolved.append(chosen)
        total += chosen["price_paise"]

    cart_id = log_cart(current_session["intent_id"], resolved, total)
    current_session["cart_id"] = cart_id
    return {"cart_id": cart_id, "items": resolved, "total_paise": total}


def tool_request_confirmation(cart_id=None, **kwargs):
    if cart_id is None:
        cart_id = current_session.get("cart_id")
    return {"cart_id": cart_id, "message": "Please confirm this cart (yes/no)."}


def tool_create_charge(cart_id=None, **kwargs):
    if cart_id is None:
        cart_id = current_session.get("cart_id")

    confirmation_id, confirmed = get_latest_confirmation_id(cart_id)
    if not confirmed:
        return {"error": "Cart is not confirmed. Cannot charge."}

    if has_existing_charge(cart_id):
        return {"error": "This cart has already been charged."}

    try:
        total = get_cart_total(cart_id)
    except ValueError:
        return {"error": f"No such cart: {cart_id}"}

    if total > MAX_ORDER_PAISE:
        return {"error": f"Order total exceeds the allowed limit of Rs {MAX_ORDER_PAISE / 100:.0f}."}

    order = create_order(amount_paise=total, receipt=f"cart_{cart_id}")
    charge_id = log_charge(cart_id, confirmation_id, order["id"], order["status"], order)
    current_session["charge_id"] = charge_id

    return {
        "charge_id": charge_id,
        "order_id": order["id"],
        "amount_rupees": round(order["amount"] / 100, 2),  # plain numeric, no currency symbol baked in
        "currency": order["currency"],
        "status": order["status"],
    }


def tool_create_payment_link(charge_id=None, **kwargs):
    """Creates a real, payable Razorpay Payment Link tied to an already-created charge/order.
    This is the step that actually lets money move -- create_charge only creates the order."""
    if charge_id is None:
        charge_id = current_session.get("charge_id")
    if charge_id is None:
        return {"error": "No charge exists yet -- call create_charge first."}

    cart_id = current_session.get("cart_id")
    if cart_id is None:
        return {"error": "No cart found for this session."}

    try:
        total = get_cart_total(cart_id)
    except ValueError:
        return {"error": "Could not find the cart for this charge."}

    link = create_payment_link(
        amount_paise=total,
        description=f"Order for cart {cart_id}",
        reference_id=f"charge_{charge_id}",
    )
    log_payment(charge_id, link["id"], link["short_url"], link["status"])

    return {
        "payment_link_url": link["short_url"],
        "status": link["status"],
    }


def tool_check_payment_status(charge_id=None, **kwargs):
    """Checks whether a previously created payment link has actually been paid.
    In Razorpay test mode, paying with UPI id 'success@razorpay' completes instantly."""
    if charge_id is None:
        charge_id = current_session.get("charge_id")
    if charge_id is None:
        return {"error": "No charge exists yet."}

    latest = get_latest_payment(charge_id)
    if latest is None:
        return {"error": "No payment link has been created for this charge yet."}

    payment_link_id, payment_link_url, _old_status = latest
    link = fetch_payment_link(payment_link_id)
    update_payment_status(payment_link_id, link["status"])

    return {
        "status": link["status"],  # 'created', 'paid', 'cancelled', or 'expired'
        "payment_link_url": payment_link_url,
    }


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------
tools = [
    {
        "type": "function",
        "function": {
            "name": "search_catalog",
            "description": "Search the product catalog by name or description.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_cart",
            "description": "Build a cart from resolved item names/sizes and compute the total.",
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "size": {"type": "string"},
                            },
                        },
                    }
                },
                "required": ["items"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_confirmation",
            "description": (
                "Present the built cart to the user and ask them to confirm before charging. "
                "Call this with ONLY the cart_id integer. Do not pass any other argument -- "
                "the user's yes/no answer is collected separately, not by this function."
            ),
            "parameters": {
                "type": "object",
                "properties": {"cart_id": {"type": "integer"}},
                "required": ["cart_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_charge",
            "description": (
                "Creates the Razorpay order for the confirmed cart. This does NOT complete "
                "payment by itself -- it only sets up the order. Only callable if the cart "
                "has been confirmed. After this succeeds, call create_payment_link next."
            ),
            "parameters": {
                "type": "object",
                "properties": {"cart_id": {"type": "integer"}},
                "required": ["cart_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_payment_link",
            "description": (
                "Creates a real, payable Razorpay payment link for an order created by "
                "create_charge. Call this immediately after create_charge succeeds. Give "
                "the returned payment_link_url to the user and tell them to complete it "
                "using the Razorpay test UPI id 'success@razorpay' to simulate a real payment."
            ),
            "parameters": {
                "type": "object",
                "properties": {"charge_id": {"type": "integer"}},
                "required": ["charge_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_payment_status",
            "description": (
                "Checks whether the payment link for a charge has actually been paid. "
                "Call this when the user says they have completed payment, or when they "
                "ask about their payment status."
            ),
            "parameters": {
                "type": "object",
                "properties": {"charge_id": {"type": "integer"}},
                "required": ["charge_id"],
            },
        },
    },
]


def dispatch_tool(name, args):
    # Defensive: strip any Harmony channel-tag leakage (e.g. 'create_charge<|channel|>commentary')
    clean_name = name.split("<")[0].strip()
    if clean_name == "search_catalog":
        return tool_search_catalog(**args)
    elif clean_name == "build_cart":
        return tool_build_cart(**args)
    elif clean_name == "request_confirmation":
        return tool_request_confirmation(**args)
    elif clean_name == "create_charge":
        return tool_create_charge(**args)
    elif clean_name == "create_payment_link":
        return tool_create_payment_link(**args)
    elif clean_name == "check_payment_status":
        return tool_check_payment_status(**args)
    else:
        return {"error": f"Unknown tool {name}"}


SYSTEM_PROMPT = (
    "You help users order food via a checkout agent. "
    "Always call request_confirmation before create_charge. "
    "Never call create_charge without a prior confirmed cart. "
    "create_charge only creates the order -- it does NOT complete payment. "
    "Immediately after create_charge succeeds, call create_payment_link, then give the "
    "user the payment_link_url exactly as returned and tell them: complete the payment "
    "using the Razorpay test UPI id 'success@razorpay' to simulate a real payment. "
    "Do NOT invent, guess, or template any URL yourself -- only use a URL that a tool "
    "actually returned. Do NOT include placeholder text like '[insert link]'. "
    "When the user says they've paid, or asks about payment status, call check_payment_status "
    "with the charge_id. Report its status field exactly ('created' means not yet paid, "
    "'paid' means payment is complete). Do not claim payment is complete unless "
    "check_payment_status returned status 'paid'. "
    "Use the amount_rupees field exactly as given -- do not divide or multiply it further. "
    "If build_cart returns an error about an ambiguous item, ask the user to clarify which "
    "option they want rather than guessing or retrying automatically. "
    "If create_charge returns an error about exceeding the order limit, tell the user plainly "
    "and do not retry automatically. "
    "Confirmation should only happen via the request_confirmation tool call, not restated "
    "separately in your own words."
)


# ---------------------------------------------------------------------------
# Streamlit-compatible turn runner: runs tool calls until the model produces
# plain text or needs confirmation, then RETURNS instead of blocking on
# input(). This is what app.py calls.
# ---------------------------------------------------------------------------
def run_agent_turn(messages, tools):
    last_tool_called = None

    while True:
        response = call_model_with_retry(messages, tools)
        msg = response.choices[0].message

        if not msg.tool_calls:
            needs_confirmation = last_tool_called == "request_confirmation"
            cart_id = current_session.get("cart_id") if needs_confirmation else None
            return messages, msg.content, needs_confirmation, cart_id

        messages.append(msg)
        for call in msg.tool_calls:
            clean_name = call.function.name.split("<")[0].strip()
            args = json.loads(call.function.arguments)
            result = dispatch_tool(clean_name, args)
            last_tool_called = clean_name
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result),
            })


# ---------------------------------------------------------------------------
# Terminal-only conversation loop, for quick command-line testing.
# Guarded behind __main__ so importing this module (e.g. from app.py or
# test_gating.py) never triggers an interactive session.
# ---------------------------------------------------------------------------
def run_conversation(session_id, user_message):
    start_session(session_id, user_message)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
    last_tool_called = None

    while True:
        response = call_model_with_retry(messages, tools)
        msg = response.choices[0].message

        if not msg.tool_calls:
            if last_tool_called == "request_confirmation":
                print("AGENT:", msg.content)
                user_reply = input("Your answer (yes/no): ")
                confirmed = user_reply.strip().lower() in ("yes", "y")
                log_confirmation(current_session["cart_id"], confirmed=confirmed)
                messages.append({"role": "assistant", "content": msg.content})
                messages.append({"role": "user", "content": user_reply})
                last_tool_called = None
                continue
            else:
                print("AGENT:", msg.content)
                return

        messages.append(msg)
        for call in msg.tool_calls:
            clean_name = call.function.name.split("<")[0].strip()
            args = json.loads(call.function.arguments)
            result = dispatch_tool(clean_name, args)
            last_tool_called = clean_name
            print(f"[tool: {clean_name}] -> {result}")
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result),
            })


if __name__ == "__main__":
    run_conversation("session_1", input("What would you like to order? "))