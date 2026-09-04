import sqlite3

DB_PATH = "store.db"


def search_catalog(query: str):
    """Word-level AND/OR match across name and size columns.
    e.g. 'Large Pepperoni' matches a row where size='Large' AND name contains 'Pepperoni'."""
    conn = sqlite3.connect(DB_PATH)
    words = query.split()

    conditions = []
    params = []
    for word in words:
        conditions.append("(name LIKE ? OR size LIKE ?)")
        params.extend([f"%{word}%", f"%{word}%"])

    sql = f"SELECT * FROM items WHERE {' AND '.join(conditions)}"
    cur = conn.execute(sql, params)
    result = [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]
    conn.close()
    return result