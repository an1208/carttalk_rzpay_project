import sqlite3
import json
from datetime import datetime, timezone

DB_PATH = "audit.db"


def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_conn()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS intent (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        raw_text TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS cart (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        intent_id INTEGER NOT NULL,
        items_json TEXT NOT NULL,
        total_paise INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (intent_id) REFERENCES intent(id)
    );

    CREATE TABLE IF NOT EXISTS confirmation (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cart_id INTEGER NOT NULL,
        confirmed INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (cart_id) REFERENCES cart(id)
    );

    CREATE TABLE IF NOT EXISTS charge (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cart_id INTEGER NOT NULL,
        confirmation_id INTEGER NOT NULL,
        razorpay_order_id TEXT,
        status TEXT NOT NULL,
        raw_response_json TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (cart_id) REFERENCES cart(id),
        FOREIGN KEY (confirmation_id) REFERENCES confirmation(id)
    );

    CREATE TABLE IF NOT EXISTS payment (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        charge_id INTEGER NOT NULL,
        payment_link_id TEXT NOT NULL,
        payment_link_url TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (charge_id) REFERENCES charge(id)
    );
    """)
    conn.commit()
    conn.close()


def log_intent(session_id: str, raw_text: str) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO intent (session_id, raw_text, created_at) VALUES (?, ?, ?)",
        (session_id, raw_text, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    intent_id = cur.lastrowid
    conn.close()
    return intent_id


def log_cart(intent_id: int, items: list, total_paise: int) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO cart (intent_id, items_json, total_paise, created_at) VALUES (?, ?, ?, ?)",
        (intent_id, json.dumps(items), total_paise, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    cart_id = cur.lastrowid
    conn.close()
    return cart_id


def log_confirmation(cart_id: int, confirmed: bool) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO confirmation (cart_id, confirmed, created_at) VALUES (?, ?, ?)",
        (cart_id, int(confirmed), datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    confirmation_id = cur.lastrowid
    conn.close()
    return confirmation_id


def log_charge(cart_id: int, confirmation_id: int, razorpay_order_id: str, status: str, raw_response: dict) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO charge (cart_id, confirmation_id, razorpay_order_id, status, raw_response_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (cart_id, confirmation_id, razorpay_order_id, status, json.dumps(raw_response),
         datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    charge_id = cur.lastrowid
    conn.close()
    return charge_id


def has_confirmed_cart(cart_id: int) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT confirmed FROM confirmation WHERE cart_id = ? ORDER BY id DESC LIMIT 1",
        (cart_id,)
    ).fetchone()
    conn.close()
    return row is not None and row[0] == 1


def get_latest_confirmation_id(cart_id: int):
    """Returns (confirmation_id, confirmed_bool). (None, False) if no confirmation exists yet."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id, confirmed FROM confirmation WHERE cart_id = ? ORDER BY id DESC LIMIT 1",
        (cart_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None, False
    return row[0], bool(row[1])


def get_cart_total(cart_id: int) -> int:
    conn = get_conn()
    row = conn.execute(
        "SELECT total_paise FROM cart WHERE id = ?", (cart_id,)
    ).fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"No cart found with id {cart_id}")
    return row[0]


def has_existing_charge(cart_id: int) -> bool:
    conn = get_conn()
    row = conn.execute("SELECT id FROM charge WHERE cart_id = ?", (cart_id,)).fetchone()
    conn.close()
    return row is not None


def log_payment(charge_id: int, payment_link_id: str, payment_link_url: str, status: str) -> int:
    conn = get_conn()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO payment (charge_id, payment_link_id, payment_link_url, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (charge_id, payment_link_id, payment_link_url, status, now, now)
    )
    conn.commit()
    payment_id = cur.lastrowid
    conn.close()
    return payment_id


def update_payment_status(payment_link_id: str, status: str):
    conn = get_conn()
    conn.execute(
        "UPDATE payment SET status = ?, updated_at = ? WHERE payment_link_id = ?",
        (status, datetime.now(timezone.utc).isoformat(), payment_link_id)
    )
    conn.commit()
    conn.close()


def get_latest_payment(charge_id: int):
    """Returns (payment_link_id, payment_link_url, status) for the most recent
    payment link tied to this charge, or None if no link has been created yet."""
    conn = get_conn()
    row = conn.execute(
        "SELECT payment_link_id, payment_link_url, status FROM payment "
        "WHERE charge_id = ? ORDER BY id DESC LIMIT 1",
        (charge_id,)
    ).fetchone()
    conn.close()
    return row


if __name__ == "__main__":
    init_db()
    print("audit.db initialized.")
