import sqlite3


def ensure_orders_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            price REAL NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'Processing',
            ordered_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
        """
    )

    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(orders)").fetchall()
    }
    if "id" in columns and "order_id" not in columns:
        connection.execute("ALTER TABLE orders RENAME COLUMN id TO order_id")
        columns.remove("id")
        columns.add("order_id")

    if "user_id" not in columns:
        connection.execute("ALTER TABLE orders ADD COLUMN user_id TEXT")

    if "status" not in columns:
        connection.execute(
            "ALTER TABLE orders ADD COLUMN status TEXT NOT NULL DEFAULT 'Processing'"
        )

    if "quantity" not in columns:
        connection.execute(
            "ALTER TABLE orders ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1"
        )
