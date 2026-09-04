# from razorpay_client import create_order

# def test_order_creation(): 
#     order = create_order(8000, "receipt_no_2")
#     assert order["status"] == "created"
#     print(order)

# if __name__ == "__main__":
#     test_order_creation()

import sqlite3

conn = sqlite3.connect("store.db")
rows = conn.execute(
    "SELECT name, size, COUNT(*) as count FROM items GROUP BY name, size"
).fetchall()
for row in rows:
    print(row)
conn.close()