"""Streamlit is an API client only. All orchestration and writes live in FastAPI."""

import secrets
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import partial
from pathlib import Path

# Community Cloud installs only frontend/requirements.txt. Make the repository's
# frontend package importable when Streamlit launches this file directly.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from frontend.api_client import APIError, request
from frontend.components import render_activity
from frontend.theme import apply_theme

st.set_page_config(
    page_title="ServiceDesk · IT Support",
    page_icon="🛠️",
    layout="wide",
    initial_sidebar_state="expanded",
)
apply_theme()

# Capture the opaque visitor token before launching a worker; Streamlit session
# state must never be accessed from ThreadPoolExecutor threads.
if "demo_session" not in st.session_state:
    st.session_state.demo_session = secrets.token_urlsafe(32)
api = partial(request, session_token=st.session_state.demo_session)


def run_action(path, body=None):
    """Poll read-only activity while a worker waits for the backend response."""
    timeout = st.session_state.get("request_timeout", 600)
    live = st.empty()
    try:
        with st.status("Investigating with the model…", expanded=True) as status:
            st.caption(
                "Checking evidence and recording tool activity. Ticket writes pause for your approval."
            )
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(api, "POST", path, body, timeout)
                previous = None
                while not future.done():
                    try:
                        records = api(
                            "GET", f"/conversations/{st.session_state.conversation_id}/activity"
                        )
                        if records != previous:
                            with live.container():
                                render_activity(records[-4:])
                            previous = records
                    except APIError:
                        pass
                    time.sleep(0.5)
                result = future.result()
            status.update(
                label="Waiting for your approval"
                if result["pending_approval"]
                else "Investigation complete",
                state="complete",
                expanded=False,
            )
        st.session_state.pop("action_error", None)
    except APIError as exc:
        st.session_state.action_error = str(exc)
    live.empty()
    st.rerun()


with st.sidebar:
    st.markdown('<div class="brand">◈ SERVICEDESK</div>', unsafe_allow_html=True)
    st.caption("YOUR IT WORKSPACE")
    if st.button("＋ New investigation", use_container_width=True, type="primary"):
        try:
            st.session_state.conversation_id = api("POST", "/conversations")["id"]
            st.session_state.pop("action_error", None)
            st.rerun()
        except APIError as exc:
            st.error(str(exc))
    try:
        with st.spinner("Connecting to the support service…"):
            health = api("GET", "/health", timeout=60)
        instance = health.get("demo_instance_id")
        previous_instance = st.session_state.get("demo_instance_id")
        if instance and previous_instance and instance != previous_instance:
            st.session_state.pop("conversation_id", None)
            st.session_state.pop("action_error", None)
            st.session_state.demo_reset_notice = True
        st.session_state.demo_instance_id = instance
        st.session_state.request_timeout = health["request_timeout_seconds"]
        conversations = api("GET", "/conversations")
    except APIError as exc:
        st.error(str(exc))
        st.caption("A free cloud backend can take a minute to wake up.")
        if st.button("Retry connection"):
            st.rerun()
        st.stop()
    if conversations:
        ids = [c["id"] for c in conversations]
        names = {c["id"]: c["title"] for c in conversations}
        active = st.session_state.get("conversation_id", ids[0])
        selected = st.selectbox(
            "Recent investigations",
            ids,
            index=ids.index(active) if active in ids else 0,
            format_func=lambda value: names[value],
        )
        st.session_state.conversation_id = selected
    st.divider()
    st.caption("MODEL CONNECTION")
    st.write(f"**{health['model']}**")
    st.caption(f"Provider: {health['provider']} · configured model")
    if st.button("Check model server", use_container_width=True):
        with st.spinner("Checking connection…"):
            try:
                result = api("GET", "/health/model", timeout=15)
                (st.success if result["available"] else st.warning)(result["message"])
            except APIError as exc:
                st.warning(str(exc))
    st.divider()
    st.caption("DEMO ENVIRONMENT")
    st.info(
        "Enterprise services, incidents, articles, and tickets are simulated. Do not enter real credentials or sensitive employee information."
    )
    st.caption(
        f"Limits per turn: {health['max_model_steps']} model calls · {health['max_tool_calls']} tools"
    )
    if health.get("session_isolation"):
        st.caption(
            "Private demo sandbox for this browser session. A fresh browser session starts a new sandbox. Sample ticket 1 is read-only."
        )
    if health.get("storage_mode") == "ephemeral":
        st.warning(
            "Disposable demo: conversations and tickets may reset when the backend sleeps, restarts, or redeploys."
        )
    elif health.get("storage_mode") == "persistent":
        st.caption("Storage configured for a persistent mount. Use synthetic data only.")

st.markdown(
    '<div class="brand">IT OPERATIONS / ASSISTED INVESTIGATION</div>', unsafe_allow_html=True
)
st.markdown('<div class="hero">IT support, with evidence.</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="subtitle">Investigate issues, follow the tool trace, and review every ticket change before it happens.</div>',
    unsafe_allow_html=True,
)
st.markdown('<div class="pill">● SIMULATED ENTERPRISE SYSTEMS</div>', unsafe_allow_html=True)
if st.session_state.pop("demo_reset_notice", False):
    st.info(
        "The disposable demo restarted. A fresh investigation is ready; previous tickets and proposals are no longer available."
    )

if "conversation_id" not in st.session_state:
    try:
        st.session_state.conversation_id = api("POST", "/conversations")["id"]
    except APIError as exc:
        st.error(str(exc))
        st.stop()
conversation_id = st.session_state.conversation_id
try:
    view = api("GET", f"/conversations/{conversation_id}")
except APIError as exc:
    st.error(str(exc))
    if health.get("storage_mode") == "ephemeral":
        st.info(
            "The demo backend may have reset. Use New investigation in the sidebar to start again."
        )
    st.stop()

chat, activity = st.columns([1.65, 1], gap="large")
with activity:
    st.subheader("Investigation activity")
    a, b = st.columns(2)
    a.metric("Tool attempts", len(view["activity"]))
    b.metric("Model steps · this turn", view["usage"]["model_steps"])
    st.caption("Every input and result is recorded by the backend.")
    render_activity(view["activity"])

with chat:
    st.subheader("Support conversation")
    st.caption("Saved investigation · " + conversation_id[:8])
    if st.session_state.get("action_error"):
        st.error(st.session_state.action_error)
        if st.button("Refresh saved state"):
            st.session_state.pop("action_error", None)
            st.rerun()
    for message in view["messages"]:
        with st.chat_message(
            message["role"],
            avatar=":material/support_agent:" if message["role"] == "assistant" else None,
        ):
            if message["application_notice"]:
                st.error(message["content"])
                st.caption("Application notice · no model answer was generated for this step")
            else:
                st.markdown(message["content"])

    pending = view["pending_approval"]
    if pending and pending["status"] == "pending":
        with st.container(border=True):
            st.warning("Your approval is required before this ticket change.")
            st.write(f"**{pending['tool'].replace('_', ' ').title()}**")
            st.json(pending["arguments"], expanded=True)
            if pending["ticket_version"]:
                st.caption(
                    f"Reviewed ticket version: {pending['ticket_version']}. A concurrent change will require a fresh proposal."
                )
            st.caption(
                "Expires at "
                + datetime.fromtimestamp(pending["expires_at"]).strftime("%H:%M:%S")
                + " (server local time). Approval applies only to the fields shown above."
            )
            yes, no = st.columns(2)
            if yes.button(
                "Approve this change",
                key="approve_" + pending["id"],
                type="primary",
                use_container_width=True,
            ):
                run_action(
                    f"/conversations/{conversation_id}/approvals/{pending['id']}",
                    {"approved": True},
                )
            if no.button("Reject", key="reject_" + pending["id"], use_container_width=True):
                run_action(
                    f"/conversations/{conversation_id}/approvals/{pending['id']}",
                    {"approved": False},
                )
    if view["recoverable"]:
        st.warning(
            "A saved run was interrupted. Resume it to read any committed approval receipt and continue."
        )
        if st.button("Resume saved run"):
            run_action(f"/conversations/{conversation_id}/resume")

    if not view["messages"]:
        st.caption("START WITH A COMMON ISSUE")
        for example in [
            "My VPN disconnects every few minutes. Is there an incident?",
            "I am connected to Wi-Fi but internal websites do not load.",
            "ExpenseHub shows HTTP 503. Investigate and prepare a high priority ticket.",
        ]:
            if st.button(example, use_container_width=True):
                run_action(f"/conversations/{conversation_id}/messages", {"message": example})
    prompt = st.chat_input("Describe your IT issue…", disabled=bool(pending) or view["recoverable"])
    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)
        run_action(f"/conversations/{conversation_id}/messages", {"message": prompt})
