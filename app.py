import tempfile
from pathlib import Path

import streamlit as st

from shopping_agent import SYSTEM_PROMPT, create_shopping_agent


st.set_page_config(
    page_title="AI Shopping Assistant",
    page_icon="🛒",
    layout="wide",
)

if "messages" not in st.session_state:
    st.session_state.messages = []


def get_agent_messages() -> list[dict[str, str]]:
    return [
        {"role": message["role"], "content": message["content"]}
        for message in st.session_state.messages
    ]


from groq import BadRequestError


def invoke_with_retry(agent, payload, attempts=3):
    """Call the agent, retrying when the model produces a malformed tool call."""
    for attempt in range(attempts):
        try:
            return agent.invoke(payload)
        except BadRequestError as exc:
            if "tool_use_failed" in str(exc) and attempt < attempts - 1:
                continue
            raise


def get_agent_response(messages: list[dict[str, str]]) -> str:
    user_id = st.session_state.get("order_user_id", "").strip()
    prompt_changed = (
        "_shopping_agent" in st.session_state
        and st.session_state.get("_shopping_agent_system_prompt") != SYSTEM_PROMPT
    )
    if (
        st.session_state.get("_shopping_agent_user_id") != user_id
        or "_shopping_agent" not in st.session_state
        or st.session_state.get("_shopping_agent_system_prompt") != SYSTEM_PROMPT
    ):
        st.session_state["_shopping_agent"] = create_shopping_agent(user_id)
        st.session_state["_shopping_agent_user_id"] = user_id
        st.session_state["_shopping_agent_system_prompt"] = SYSTEM_PROMPT
        if prompt_changed:
            latest_message = next(
                (message for message in reversed(messages) if message["role"] == "user"),
                None,
            )
            latest_history_message = next(
                (
                    message
                    for message in reversed(st.session_state.messages)
                    if message["role"] == "user"
                ),
                None,
            )
            st.session_state.messages.clear()
            if latest_history_message is not None:
                st.session_state.messages.append(latest_history_message)
            messages = [latest_message] if latest_message is not None else []

    agent = st.session_state["_shopping_agent"]
    result = invoke_with_retry(agent, {"messages": messages})
    content = result["messages"][-1].content or ""

    if isinstance(content, str):
        response = content
    else:
        response = "".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )

    if not response.strip():
        raise RuntimeError("The shopping assistant returned an empty response.")

    return response

st.title("AI shopping assistant")
st.caption(
    "I can help with this store's products, ratings, orders, and order tracking. "
    "Describe what you need or upload a product photo. Orders are placed only after "
    "you confirm; provide a user ID to place and track orders."
)

with st.sidebar:
    st.text_input("User ID for orders", key="order_user_id")
    st.caption("Used for checkout and order tracking; it is not included in chat.")

    st.header("Shop by image")
    st.caption("Upload a product photo to find similar items in the store.")
    uploaded_file = st.file_uploader(
        "Upload a product image",
        type=["jpg", "jpeg", "png", "webp"],
    )

    if uploaded_file:
        st.image(
            uploaded_file,
            alt="Uploaded product",
            width="stretch",
        )

    if uploaded_file and st.button(
        "Find similar products",
        icon=":material/image_search:",
        width="stretch",
    ):
        suffix = Path(uploaded_file.name).suffix or ".jpg"
        image_path = None
        st.session_state.messages.append(
            {
                "role": "user",
                "content": "Find products similar to this uploaded image.",
                "image_name": uploaded_file.name,
            }
        )

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as image_file:
                image_path = image_file.name
                image_file.write(uploaded_file.getvalue())

            agent_messages = get_agent_messages()
            agent_messages[-1]["content"] = (
                "I uploaded a product image. Please analyze it and find similar "
                f"products in the store. Image path: {image_path}"
            )
            with st.spinner("Analyzing the image and searching the store..."):
                response = get_agent_response(agent_messages)
            st.session_state.messages.append(
                {"role": "assistant", "content": response}
            )
        except Exception as exc:
            st.error(f"Image search failed: {exc}", title="Could not search by image")
        finally:
            if image_path is not None:
                Path(image_path).unlink(missing_ok=True)

    if st.session_state.messages and st.button(
        "New conversation",
        icon=":material/add_comment:",
        width="stretch",
    ):
        st.session_state.messages.clear()

st.subheader("What are you shopping for?")
if not st.session_state.messages:
    with st.container(border=True):
        st.markdown("Describe a product, your budget, or the rating you are looking for.")
        st.caption(
            'For example: "Find organic honey under $15 with a 4-star rating or higher."'
        )

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if message.get("image_name"):
            st.caption(f"Product photo: {message['image_name']}")
        st.markdown(message["content"].replace("$", r"\$"))

if prompt := st.chat_input(
    "Ask about products, ratings, or an order",
    submit_mode="disable",
):
    st.session_state.messages.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Searching the store..."):
                response = get_agent_response(get_agent_messages())
            st.markdown(response.replace("$", r"\$"))
            st.session_state.messages.append(
                {"role": "assistant", "content": response}
            )
        except Exception as exc:
            st.error(f"Shopping request failed: {exc}", title="Could not complete request")
