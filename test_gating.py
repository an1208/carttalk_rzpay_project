from audit_log import init_db, log_intent, log_cart, log_confirmation
from agent import tool_create_charge, tool_build_cart, current_session
from seed_catalog import seed as seed_catalog

# The build_cart tests below depend on the catalog actually being seeded
# (specifically the deliberately-ambiguous "Pizza" no-size row). Reseed once
# when this test module loads, so these tests don't depend on having run
# seed_catalog.py manually beforehand.
seed_catalog()


def test_charge_blocked_without_confirmation():
    init_db()
    intent_id = log_intent("s1", "get a pizza")
    cart_id = log_cart(intent_id, [{"name": "Pizza"}], 40000)
    result = tool_create_charge(cart_id)
    assert "error" in result
    assert "not confirmed" in result["error"].lower()


def test_charge_blocked_after_explicit_rejection():
    init_db()
    intent_id = log_intent("s2", "get a pizza")
    cart_id = log_cart(intent_id, [{"name": "Pizza"}], 40000)
    log_confirmation(cart_id, confirmed=False)
    result = tool_create_charge(cart_id)
    assert "error" in result


def test_charge_succeeds_once_confirmed():
    init_db()
    intent_id = log_intent("s3", "get a pizza")
    cart_id = log_cart(intent_id, [{"name": "Pizza"}], 40000)
    log_confirmation(cart_id, confirmed=True)
    result = tool_create_charge(cart_id)
    assert "charge_id" in result
    assert "order_id" in result
    assert "amount_rupees" in result
    assert "currency" in result
    assert "status" in result
    assert isinstance(result["amount_rupees"], (int, float))  # numeric, no currency symbol baked in


def test_double_charge_blocked():
    init_db()
    intent_id = log_intent("s4", "get a pizza")
    cart_id = log_cart(intent_id, [{"name": "Pizza"}], 40000)
    log_confirmation(cart_id, confirmed=True)
    first = tool_create_charge(cart_id)
    assert "charge_id" in first
    second = tool_create_charge(cart_id)
    assert "error" in second
    assert "already been charged" in second["error"].lower()


def test_charge_on_nonexistent_cart():
    init_db()
    result = tool_create_charge(99999)
    assert "error" in result


def test_charge_over_spend_cap_blocked():
    init_db()
    intent_id = log_intent("s5", "get a huge order")
    # 300000 paise = Rs 3000, above the Rs 2000 MAX_ORDER_PAISE cap in agent.py
    cart_id = log_cart(intent_id, [{"name": "Huge Order"}], 300000)
    log_confirmation(cart_id, confirmed=True)
    result = tool_create_charge(cart_id)
    assert "error" in result
    assert "limit" in result["error"].lower()


def test_build_cart_ambiguous_item_returns_clarifying_error():
    init_db()
    current_session["intent_id"] = log_intent("s6", "get a pizza")
    # "Pizza" alone matches the deliberately ambiguous no-size row AND sized rows
    result = tool_build_cart(items=[{"name": "Pizza"}])
    assert "error" in result
    assert "ambiguous" in result["error"].lower()


def test_build_cart_no_match_returns_error_not_crash():
    init_db()
    current_session["intent_id"] = log_intent("s7", "get a spaceship")
    result = tool_build_cart(items=[{"name": "Spaceship"}])
    assert "error" in result
    assert "no catalog match" in result["error"].lower()
