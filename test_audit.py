

from audit_log import init_db, log_intent, log_cart, log_confirmation, log_charge, has_confirmed_cart


def test_full_chain():
    init_db()
    intent_id = log_intent("session_abc", "get me a large pepperoni pizza and a coke")
    assert intent_id is not None

    cart_id = log_cart(
        intent_id,
        [{"name": "Pepperoni Pizza", "size": "Large"}, {"name": "Coke"}],
        60000
    )
    assert cart_id is not None
    assert has_confirmed_cart(cart_id) == False

    confirmation_id = log_confirmation(cart_id, confirmed=True)
    assert has_confirmed_cart(cart_id) == True

    charge_id = log_charge(cart_id, confirmation_id, "order_TEST123", "created", {"status": "created"})
    assert charge_id is not None


def test_unconfirmed_cart_is_not_confirmed():
    init_db()
    intent_id = log_intent("session_xyz", "get me a pizza")
    cart_id = log_cart(intent_id, [{"name": "Pizza"}], 40000)
    assert has_confirmed_cart(cart_id) == False
