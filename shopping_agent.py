import base64
import json
import os
import sqlite3
from contextlib import closing
from typing import Optional

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_core.messages import HumanMessage
from langchain_groq import ChatGroq

from order_db import ensure_orders_schema
from reviews_api import get_product_rating

load_dotenv()

DB_PATH = os.path.join(os.path.dirname(__file__), "store.db")

with closing(sqlite3.connect(DB_PATH)) as conn:
    with conn:
        ensure_orders_schema(conn)

llm = ChatGroq(model="openai/gpt-oss-120b", temperature=0)
#llm = ChatGroq(model="qwen/qwen3-32b", temperature=0)
vision_llm = ChatGroq(model="qwen/qwen3.8-27b", temperature=0)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def search_products(query: str, max_price: Optional[float] = None, is_organic: Optional[bool] = None) -> str:
    """
    Search the product database by keyword (matched against name, description, and category).
    Optionally filter by maximum price and/or organic status.
    Returns a JSON array of matching products, each with: id, name, category, price,
    description, is_organic.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    sql = "SELECT id, name, category, price, description, is_organic FROM products WHERE 1=1"
    params: list = []

    if query:
        sql += " AND (name LIKE ? OR description LIKE ? OR category LIKE ?)"
        like = f"%{query}%"
        params.extend([like, like, like])

    if max_price is not None:
        sql += " AND price <= ?"
        params.append(max_price)

    if is_organic is not None:
        sql += " AND is_organic = ?"
        params.append(1 if is_organic else 0)

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    products = [
        {
            "id":          row[0],
            "name":        row[1],
            "category":    row[2],
            "price":       row[3],
            "description": row[4],
            "is_organic":  bool(row[5]),
        }
        for row in rows
    ]
    return json.dumps(products)


@tool
def get_rating(product_id: int) -> str:
    """
    Get the average customer rating and total review count for a product by its ID.
    Returns a JSON object with: product_id, average_rating, review_count.
    """
    result = get_product_rating(product_id)
    return json.dumps(result)


def _checkout_for_user(product_id: int, quantity: int, user_id: str) -> str:
    if not user_id:
        return "A user ID is not set in the app. Ask the user to enter it in the sidebar."
    if quantity < 1:
        return "Error: quantity must be at least 1."

    with closing(sqlite3.connect(DB_PATH)) as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name, price FROM products WHERE id = ?",
                (product_id,),
            )
            row = cursor.fetchone()

            if not row:
                return f"Error: product with ID {product_id} not found."

            name, price = row
            cursor.execute(
                """
                INSERT INTO orders (user_id, product_id, product_name, price, quantity)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, product_id, name, price, quantity),
            )
            order_id = cursor.lastrowid

    total_price = price * quantity
    return (
        f"Order number: {order_id}\n"
        f"Product: {name}\n"
        f"Quantity: {quantity}\n"
        f"Unit price: ${price:.2f}\n"
        f"Total price: ${total_price:.2f}\n"
        "Your order will arrive in 3-5 business days."
    )


def _track_order_for_user(order_id: int, user_id: str) -> str:
    if not user_id:
        return "A user ID is not set in the app. Ask the user to enter it in the sidebar."

    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT product_name, price, quantity, status, ordered_at
            FROM orders
            WHERE order_id = ? AND user_id = ?
            """,
            (order_id, user_id),
        ).fetchone()

    if not row:
        return "No order was found for the provided order ID and user ID."

    product_name, price, quantity, status, ordered_at = row
    return (
        f"Order #{order_id}: {quantity} x {product_name} "
        f"(${price:.2f} each; ${price * quantity:.2f} total) is {status.lower()}. "
        f"Order date: {ordered_at}."
    )


@tool
def describe_product_image(image_path: str) -> str:
    """
    Analyze a product image and return its key attributes as a JSON object.
    Use this when the user uploads a photo of a product they are interested in.
    The returned attributes can be used directly with search_products.
    """
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode()

    ext = os.path.splitext(image_path)[1].lower().lstrip(".")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"

    message = HumanMessage(content=[
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{image_data}"},
        },
        {
            "type": "text",
            "text": (
                "Look at this product image and extract its key attributes. "
                "Return ONLY a JSON object with these fields:\n"
                "- product_type: what kind of product it is (e.g. honey, olive oil, almonds)\n"
                "- search_query: a short keyword to search for it (e.g. 'honey', 'olive oil')\n"
                "- is_organic: true if the label says organic, false if not, null if unclear\n"
                "- description: one sentence describing the product"
            ),
        },
    ])

    response = vision_llm.invoke([message])
    return response.content


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "SCOPE: You only help with this store's products, ratings, orders and order tracking.\n"
    "For any other request, reply: \"I can only help with shopping in this store.\" Do not answer it.\n"
    "You are a helpful shopping assistant. Follow these rules strictly.\n\n"
    "ORDER TRACKING — when the user asks about an existing order:\n"
    "1. Get the order ID from the conversation. If it is missing, ask the user for it.\n"
    "2. Call track_order with the order ID. The app has already bound the user's "
    "   identity to the tool; never ask for or provide a user ID in chat.\n"
    "3. Report the returned status and details. If no matching order is found, say so "
    "   without revealing whether the order exists for another user.\n\n"
    "IMAGE SEARCH — when the user provides an image path:\n"
    "1. Call describe_product_image with the path to identify the product.\n"
    "2. Use the returned search_query and is_organic to call search_products.\n"
    "3. Continue with the BROWSING flow from step 2 onwards.\n\n"
    "BROWSING — when the user describes what they want to buy:\n"
    "1. Call search_products to find matching items (apply any price/organic filters given).\n"
    "2. For each candidate, call get_rating to retrieve its average rating.\n"
    "3. Filter by the user's minimum rating if specified.\n"
    "4. Present qualifying products as a numbered list. For each item use this exact format "
    "   (plain text, no backticks, no code blocks, no bold, no italic):\n\n"
    "   #<number>. <name> (ID:<product_id>) — $<price> ★<rating> — <organic or non-organic>\n\n"
    "   Add a blank line between each product entry for readability. "
    "   Always include (ID:X) so you can reference it later.\n"
    "5. If only one product qualifies, still show it in the list and ask: "
    "   'Would you like to order it? Just say yes or give me the number.'\n"
    "6. Do NOT call checkout at this stage.\n\n"
    "ORDERING — when the user confirms they want to buy (e.g. 'yes', 'sure', 'go ahead', "
    "'order number 2', 'the first one', 'get me #3'):\n"
    "1. Look at your previous message to find the (ID:X) for the chosen product "
    "   (if only one was listed and the user says 'yes', use that product's ID).\n"
    "2. Use the quantity requested by the user, or 1 if none was specified. "
    "   Call checkout with the product_id (the number from (ID:X)) and quantity. The app has "
    "   already bound the user's identity to the tool; never ask for or provide a "
    "   user ID in chat.\n"
    "3. Repeat the checkout result's exact product name, quantity, unit price, total "
    "   price, and order number in your confirmation. Do not substitute or omit these "
    "   details; if the product or amount is not what the user intended, make that clear "
    "   immediately. Then mention the delivery estimate.\n\n"
    "Never place an order unless the user explicitly confirms. Never guess a product_id; "
    "use only the product ID from your previous recommendation."
)


def create_shopping_agent(user_id: str):
    """Create an agent whose order tools are scoped to this app-session user."""
    bound_user_id = user_id.strip()

    @tool
    def checkout(product_id: int, quantity: int = 1) -> str:
        """Place an order for the given product and quantity using the app user's identity."""
        return _checkout_for_user(product_id, quantity, bound_user_id)

    @tool
    def track_order(order_id: int) -> str:
        """Get order status and details for the current app user."""
        return _track_order_for_user(order_id, bound_user_id)

    return create_agent(
        tools=[
            search_products,
            get_rating,
            checkout,
            track_order,
            describe_product_image,
        ],
        model=llm,
        system_prompt=SYSTEM_PROMPT,
    )


if __name__ == "__main__":
    user_id = os.getenv("SHOPPING_USER_ID", "").strip()
    if not user_id:
        raise SystemExit("Set SHOPPING_USER_ID before running the standalone agent.")

    agent = create_shopping_agent(user_id)
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "I want to buy organic honey with 4.5+ rating and less than $20 price."
                    ),
                }
            ]
        }
    )
    print(result["messages"][-1].content)
