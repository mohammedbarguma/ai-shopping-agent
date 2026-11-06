# AI shopping assistant

This Streamlit app lets you search the sample store by chat or product image,
check product ratings, place an order after confirming with the assistant, and
track an order using its order ID.

The orders table uses `order_id` as its primary key and stores the product,
quantity, unit price, associated `user_id`, and current status. Order confirmations
include the product, quantity, unit and total prices, and order number. Enter the
user ID in the app's sidebar; it is passed
to the agent builder and captured by that session's checkout and tracking tools.
The user ID is not a chat message or model-visible tool parameter. Changing the
sidebar ID rebuilds the session's agent with the new identity. Existing databases
are migrated automatically when the shopping agent starts; existing orders get
a quantity of 1. Previously placed orders have no user ID and cannot be tracked
until they are associated with one.
The demo matches supplied IDs but does not authenticate users; production order
access should use a trusted login.

From the repository root:

1. Add `GROQ_API_KEY` to the root `.env` file.
2. Start the app:

   ```powershell
   uv run streamlit run 10_project_shopping_agent/app.py
   ```

The app uses `store.db` in this folder. If it is missing, create and seed it with:

```powershell
uv run python 10_project_shopping_agent/setup_db.py
```
