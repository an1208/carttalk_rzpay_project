# CartTalk

A conversational checkout agent that replaces the browse → cart → pay flow with a single chat interface — built on Razorpay's test-mode APIs, with a full audit trail behind every action.

> Built for [Buildathon name] — [date].

## Why

Wiring an LLM to a product catalog is the easy part. The real question this project explores: if an AI agent is allowed to move money on someone's behalf, can you prove — after the fact, from something other than its own words — exactly why it did what it did?

CartTalk answers that by treating every checkout as a strict, append-only state machine:

```
Intent → Cart → Confirmation → Charge → Payment
```

Nothing in this chain is ever overwritten. The agent only ever appends the next event, and the receipt/audit trail you see is read directly from those records — never from the model's own summary of what it did.

## How it works

- **Tool-scoped agent** (`agent.py`) — the model has exactly four tools: search the catalog, build a cart, request confirmation, and create a charge. Nothing else.
- **Enforced confirmation gate** — `create_charge` checks the database for a matching confirmation record before it will call Razorpay at all. This is a runtime check, not a prompt instruction, so it holds even if the model's reasoning goes off the rails.
- **Ground-truth receipts** — the receipt card in `app.py` is built from the raw JSON returned by the charge tool, not from the model's generated text. The model can narrate the outcome; it doesn't get to invent it.
- **Immutable audit trail** (`audit_log.py`) — every intent, cart, confirmation, and charge is timestamped and foreign-keyed to the step before it. The Audit Trail tab in the app replays this directly from `audit.db`.
- **Real payments, not just requests** — orders are followed by an actual Razorpay Payment Link, completable end-to-end through test-mode UPI, so a "successful" order in the app corresponds to an actual captured payment, not just an order object with `amount_paid: 0`.
- **Guardrails backed by tests** — a cart can't be charged without a confirmation, and can't be charged twice; both are enforced with database constraints and covered by `test_gating.py`. A hard spend cap is enforced server-side, independent of the model.

## Project structure

```
.
├── app.py              # Streamlit UI: chat tab + audit trail tab
├── agent.py             # Agent loop, tool schema, system prompt, tool dispatch
├── audit_log.py          # Append-only audit trail (intent/cart/confirmation/charge)
├── catalog.py            # Product catalog data
├── seed_catalog.py       # Idempotent catalog seeding (drop + recreate on every run)
├── razorpay_client.py     # Razorpay Orders + Payment Links API wrapper
├── test.py                # Core agent/tool tests
├── test_audit.py           # Audit trail integrity tests
├── test_gating.py           # Confirmation-gating and double-charge prevention tests
└── requirements.txt
```

## Setup

**1. Clone and install dependencies**

```bash
git clone https://github.com/an1208/rzpay_checkout_ag.git
cd rzpay_checkout_ag
python -m venv venv
source venv/bin/activate   # venv\Scripts\activate on Windows
pip install -r requirements.txt
```

**2. Configure environment variables**

Create a `.env` file in the project root:

```
GROQ_API_KEY=your_groq_api_key
RAZORPAY_KEY_ID=your_razorpay_test_key_id
RAZORPAY_KEY_SECRET=your_razorpay_test_key_secret
```

Use **test-mode** Razorpay keys — all payment flows in this app run against Razorpay's test environment.

If deploying to Streamlit Cloud, set the same three keys under the app's **Secrets** instead of a `.env` file; `app.py` reads from `st.secrets` automatically when present.

**3. Run the app**

```bash
streamlit run app.py
```

The catalog and audit databases are created automatically on first run — no manual setup needed.

## Running tests

```bash
pytest test.py test_audit.py test_gating.py -v
```

`test_gating.py` specifically covers the two guarantees this project cares most about: that a charge can't fire without a matching confirmation, and that a cart can't be charged twice.

## Known limitations

- Runs against Razorpay's **test mode** only — no live payment processing.
- Single-session state in the Streamlit app; not designed for concurrent multi-user checkout sessions.
- Catalog is a small, hardcoded product set for demo purposes rather than a real inventory system.

## What I'd build next

- Webhook-based payment status updates instead of polling, so the audit trail reflects Razorpay-side confirmation in real time.
- A second LLM provider as a fallback if the primary one is unavailable mid-session.
- Multi-user session isolation for the audit trail.