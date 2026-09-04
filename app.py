import os
import json
import sqlite3
import streamlit as st
import pandas as pd

# ---------------------------------------------------------------------------
# Bridge Streamlit Cloud secrets into environment variables, if available.
# Locally (no secrets.toml), this is skipped and .env + load_dotenv() inside
# agent.py / razorpay_client.py handles it instead.
# ---------------------------------------------------------------------------
try:
    if "RAZORPAY_KEY_ID" in st.secrets:
        os.environ["RAZORPAY_KEY_ID"] = st.secrets["RAZORPAY_KEY_ID"]
        os.environ["RAZORPAY_KEY_SECRET"] = st.secrets["RAZORPAY_KEY_SECRET"]
        os.environ["GROQ_API_KEY"] = st.secrets["GROQ_API_KEY"]
except Exception:
    pass

from agent import (
    start_session, run_agent_turn, tools,
    current_session, log_confirmation, SYSTEM_PROMPT,
    tool_check_payment_status,
)
from audit_log import init_db as init_audit_db
from seed_catalog import seed as seed_catalog

SESSION_ID = "streamlit_session"


# ---------------------------------------------------------------------------
# One-time database setup.
# ---------------------------------------------------------------------------
@st.cache_resource
def initialize_databases():
    init_audit_db()
    seed_catalog()
    return True


initialize_databases()


# ---------------------------------------------------------------------------
# Helper: find the most recent successful create_charge result in the LLM
# message history, so I can render a proper receipt card instead of relying
# on the model's free-text summary alone.
# ---------------------------------------------------------------------------
def extract_last_receipt(messages):
    if not messages:
        return None
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "tool":
            try:
                data = json.loads(msg["content"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict) and "order_id" in data:
                return data
    return None


def extract_last_payment_info(messages):
    """Finds the most recent create_payment_link or check_payment_status tool result."""
    if not messages:
        return None
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "tool":
            try:
                data = json.loads(msg["content"])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict) and "payment_link_url" in data:
                return data
    return None


# ---------------------------------------------------------------------------
# Helper: render the full audit trail for this session as a readable
# timeline, reading straight from audit.db.
# ---------------------------------------------------------------------------
def render_audit_trail(session_id):
    conn = sqlite3.connect("audit.db")

    intents = pd.read_sql(
        "SELECT * FROM intent WHERE session_id = ? ORDER BY id", conn, params=(session_id,)
    )

    if intents.empty:
        st.info("No activity logged yet for this session.")
        conn.close()
        return

    for _, intent_row in intents.iterrows():
        st.markdown(f"**Intent #{intent_row['id']}** &nbsp;`{intent_row['created_at']}`")
        st.caption(f"\u201c{intent_row['raw_text']}\u201d")

        carts = pd.read_sql(
            "SELECT * FROM cart WHERE intent_id = ? ORDER BY id", conn, params=(int(intent_row["id"]),)
        )
        for _, cart_row in carts.iterrows():
            st.markdown(
                f"&nbsp;&nbsp;&nbsp;&nbsp;\u2192 **Cart #{cart_row['id']}** "
                f"&mdash; Rs {cart_row['total_paise'] / 100:.2f} &nbsp;`{cart_row['created_at']}`"
            )
            try:
                items = json.loads(cart_row["items_json"])
                item_names = ", ".join(f"{i.get('name')} ({i.get('size') or '-'})" for i in items)
                st.caption(f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Items: {item_names}")
            except (json.JSONDecodeError, TypeError):
                pass

            confirmations = pd.read_sql(
                "SELECT * FROM confirmation WHERE cart_id = ? ORDER BY id",
                conn, params=(int(cart_row["id"]),)
            )
            for _, c in confirmations.iterrows():
                status = "\u2705 Confirmed" if c["confirmed"] else "\u274c Rejected"
                st.markdown(
                    f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;\u2192 **{status}** &nbsp;`{c['created_at']}`"
                )

            charges = pd.read_sql(
                "SELECT * FROM charge WHERE cart_id = ? ORDER BY id",
                conn, params=(int(cart_row["id"]),)
            )
            for _, ch in charges.iterrows():
                st.markdown(
                    f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;\u2192 **Charged** "
                    f"&mdash; `{ch['razorpay_order_id']}` ({ch['status']}) &nbsp;`{ch['created_at']}`"
                )
        st.divider()

    conn.close()


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------
st.title("Checkout Agent")

tab_chat, tab_audit = st.tabs(["\U0001F4AC Chat", "\U0001F4CB Audit Trail"])


# ================================= CHAT TAB =================================
with tab_chat:

    # ---------- Session state setup ----------
    if "history" not in st.session_state:
        st.session_state.history = []
    if "messages" not in st.session_state:
        st.session_state.messages = None
    if "awaiting_confirmation" not in st.session_state:
        st.session_state.awaiting_confirmation = False
    if "pending_cart_id" not in st.session_state:
        st.session_state.pending_cart_id = None
    if "pending_user_input" not in st.session_state:
        st.session_state.pending_user_input = None
    if "pending_confirmation_reply" not in st.session_state:
        st.session_state.pending_confirmation_reply = None
    if "last_receipt" not in st.session_state:
        st.session_state.last_receipt = None
    if "last_payment" not in st.session_state:
        st.session_state.last_payment = None
    if "last_charge_id" not in st.session_state:
        st.session_state.last_charge_id = None

    # ---------- Input box ----------
    user_input = st.chat_input(
        "What would you like to order?",
        disabled=st.session_state.awaiting_confirmation,
    )

    # ---------- PHASE 1: user just typed something ----------
    if user_input and not st.session_state.awaiting_confirmation:
        st.session_state.history.append({"role": "user", "content": user_input})
        st.session_state.pending_user_input = user_input
        # clear old receipt/payment state once a new order starts
        st.session_state.last_receipt = None
        st.session_state.last_payment = None
        st.session_state.last_charge_id = None
        st.rerun()

    # ---------- Render chat history (exactly ONE loop) ----------
    for entry in st.session_state.history:
        with st.chat_message(entry["role"]):
            st.write(entry["content"])

    # ---------- Receipt card, shown separately from the chat bubble text ----------
    if st.session_state.last_receipt:
        r = st.session_state.last_receipt
        st.success(
            f"**Order created**\n\n"
            f"Order ID: `{r['order_id']}`  \n"
            f"Amount: Rs {r['amount_rupees']}  \n"
            f"Currency: {r['currency']}  \n"
            f"Order status: {r['status']}"
        )

        payment = st.session_state.last_payment
        if payment:
            paid = payment.get("status") == "paid"
            if paid:
                st.success(f"**Payment status: PAID** \u2705")
            else:
                st.warning(
                    f"**Payment status: {payment.get('status', 'unknown')}** -- not yet paid.\n\n"
                    f"[Click here to pay (test mode)]({payment['payment_link_url']})\n\n"
                    f"Use UPI id `success@razorpay` to simulate a completed payment."
                )
                if st.button("Check payment status now"):
                    result = tool_check_payment_status(charge_id=st.session_state.last_charge_id)
                    if "error" not in result:
                        st.session_state.last_payment = result
                    st.rerun()

    # ---------- PHASE 2: process a new user message ----------
    if st.session_state.pending_user_input:
        try:
            with st.spinner("Thinking..."):
                start_session(SESSION_ID, st.session_state.pending_user_input)
                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": st.session_state.pending_user_input},
                ]
                messages, final_text, needs_confirmation, cart_id = run_agent_turn(messages, tools)

            st.session_state.messages = messages
            st.session_state.history.append({"role": "assistant", "content": final_text})

            if needs_confirmation:
                st.session_state.awaiting_confirmation = True
                st.session_state.pending_cart_id = cart_id

        except Exception as e:
            # Never let a raw traceback reach the page -- show a plain message and
            # log the real error to the terminal for debugging.
            print(f"[ERROR in Phase 2] {type(e).__name__}: {e}")
            st.session_state.history.append({
                "role": "assistant",
                "content": "Sorry, something went wrong processing that request. Please try again.",
            })

        st.session_state.pending_user_input = None
        st.rerun()

    # ---------- Confirm / Cancel buttons ----------
    if st.session_state.awaiting_confirmation:
        col1, col2 = st.columns(2)
        if col1.button("Confirm order", use_container_width=True):
            st.session_state.history.append({"role": "user", "content": "yes"})
            st.session_state.pending_confirmation_reply = "yes"
            st.session_state.awaiting_confirmation = False
            st.rerun()
        if col2.button("Cancel order", use_container_width=True):
            st.session_state.history.append({"role": "user", "content": "no"})
            st.session_state.pending_confirmation_reply = "no"
            st.session_state.awaiting_confirmation = False
            st.rerun()

    # ---------- PHASE 3: process a confirm/cancel reply ----------
    if st.session_state.pending_confirmation_reply:
        reply = st.session_state.pending_confirmation_reply
        confirmed = reply == "yes"

        try:
            with st.spinner("Processing..."):
                log_confirmation(st.session_state.pending_cart_id, confirmed=confirmed)
                st.session_state.messages.append({"role": "user", "content": reply})
                messages, final_text, needs_confirmation, cart_id = run_agent_turn(
                    st.session_state.messages, tools
                )

            st.session_state.messages = messages
            st.session_state.history.append({"role": "assistant", "content": final_text})

            if needs_confirmation:
                st.session_state.awaiting_confirmation = True
                st.session_state.pending_cart_id = cart_id
            else:
                # conversation settled -- check if a charge actually succeeded, for the receipt card
                receipt = extract_last_receipt(messages)
                if receipt:
                    st.session_state.last_receipt = receipt
                    st.session_state.last_charge_id = receipt.get("charge_id")

                payment_info = extract_last_payment_info(messages)
                if payment_info:
                    st.session_state.last_payment = payment_info

        except Exception as e:
            print(f"[ERROR in Phase 3] {type(e).__name__}: {e}")
            st.session_state.history.append({
                "role": "assistant",
                "content": (
                    "Sorry, something went wrong while processing your confirmation. "
                    "Your cart was not charged. Please try again."
                ),
            })
            st.session_state.awaiting_confirmation = False

        st.session_state.pending_confirmation_reply = None
        st.rerun()


# ================================ AUDIT TAB =================================
with tab_audit:
    st.subheader("Session Timeline")
    st.caption("Every intent, cart, confirmation, and charge for this session, in order.")
    render_audit_trail(SESSION_ID)
