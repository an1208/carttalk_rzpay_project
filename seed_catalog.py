import sqlite3

DB_PATH = "store.db"


def seed():
    """DROP + recreate on every run -- structurally impossible to duplicate rows,
    no matter how many times this script is executed."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DROP TABLE IF EXISTS items")
    conn.execute("""
        CREATE TABLE items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            size TEXT,
            price_paise INTEGER NOT NULL,
            category TEXT,
            stock INTEGER DEFAULT 100
        )
    """)

    items = [
    ("Margherita Pizza", "Large", 45000,  "pizza", 50), 
    ("Pomodoro Pizza", "Small", 25000, "pizza", 50),
    ("Cheese Pizza", "Medium", 35000, "pizza", 50),
    ("Paneer Burger", "Large", 40000, "burger", 50), 
    ("Peri-peri Burger", "Large", 45000, "burger", 50), 
    ("Salsa Taco", "Small", 25000, "taco", 50), 
    ("Bean Taco", "Large", 15000, "taco", 50), 
    ("Pepperoni Pizza", "Large", 45000, "pizza", 50),
    ("Coke", "1ltr", 15000, "beverage", 50),
    ("Ice cold latte", "Large", 25000, "beverage", 50),
    ]

    conn.executemany(
        "INSERT INTO items (name, size, price_paise, category, stock) VALUES (?,?,?,?,?)",
        items
    )
    conn.commit()
    conn.close()
    print(f"Seeded {len(items)} items.")


if __name__ == "__main__":
    seed()
