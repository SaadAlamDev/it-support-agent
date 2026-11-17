"""Streamlit is an API client only. All orchestration and writes live in FastAPI."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import httpx
import streamlit as st
from dotenv import load_dotenv

load_dotenv()
API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
st.set_page_config(
    page_title="ServiceDesk · IT Support",
    page_icon="🛠️",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(
    """
<style>
html, body, [class*="css"], .stApp {font-family:Inter,'Segoe UI',sans-serif;}
.stApp {background:#0b1018;}
[data-testid="stSidebar"] {background:#111925; border-right:1px solid #233044;}
.block-container {max-width:1500px;padding-top:2.2rem;}
.brand {color:#79e0c3;letter-spacing:.13em;font-size:12px;font-weight:700;margin-bottom:14px;}
.hero {font-size:40px;font-weight:700;letter-spacing:-1.5px;line-height:1.1;margin:0 0 12px;}
.subtitle {color:#9eafc4;font-size:16px;line-height:1.6;max-width:650px;}
.pill {display:inline-block;border:1px solid #304736;color:#a7e0bb;background:#15251e;border-radius:20px;padding:5px 12px;font-size:12px;margin:8px 0 16px;}
[data-testid="stChatMessage"] {background:#121c29;border:1px solid #233044;border-radius:14px;}
[data-testid="stMetric"] {background:#121c29;padding:14px;border:1px solid #233044;border-radius:12px;}
.stButton>button {border-radius:9px;}
hr {border-color:#233044;}
</style>
""",
    unsafe_allow_html=True,
)


class APIError(Exception):
    pass


def api(method, path, body=None, timeout=10):
    try:
        response = httpx.request(
            method, API_BASE + path, json=body, timeout=timeout, trust_env=False
        )
        if response.is_error:
            try:
                detail = response.json().get("detail", "Request failed")
            except ValueError:
                detail = "The backend returned an unexpected response."
            if not isinstance(detail, str):
                detail = "The request was invalid. Check the entered values."
            raise APIError(f"{detail} (HTTP {response.status_code})")
        return response.json()
    except httpx.TimeoutException as exc:
        raise APIError(
            "The backend request timed out. Refresh to inspect the saved result before retrying an approval."
        ) from exc
    except httpx.RequestError as exc:
        raise APIError("Cannot reach the backend. Start FastAPI and check API_BASE_URL.") from exc


def render_activity(records, expanded=False):
    if not records:
        st.caption("Tool calls will appear here as the investigation progresses.")
    for record in records:
        symbol = {"completed": "✓", "error": "!", "awaiting_approval": "◷"}.get(
            record["status"], "•"
        )
        label = record["tool"].replace("_", " ")
        with st.expander(
            f"{symbol} {label} · {record['status'].replace('_', ' ')}", expanded=expanded
        ):
            st.caption("VALIDATED INPUT" if record["status"] != "error" else "INPUT")
            st.json(record["arguments"], expanded=True)
            st.caption("TOOL RESULT · UNTRUSTED REFERENCE DATA")
            st.json(record["result"], expanded=True)


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
        health = api("GET", "/health")
        st.session_state.request_timeout = health["request_timeout_seconds"]
        conversations = api("GET", "/conversations")
    except APIError as exc:
        st.error(str(exc))
        st.code("uv run uvicorn support_agent.api:app --reload")
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
    st.caption(f"Provider: {health['provider']} · real model inference")
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

st.markdown(
    '<div class="brand">IT OPERATIONS / ASSISTED INVESTIGATION</div>', unsafe_allow_html=True
)
st.markdown('<div class="hero">A clearer path to resolution.</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="subtitle">Describe the problem. ServiceDesk checks evidence, guides troubleshooting, and prepares support tickets with you in control.</div>',
    unsafe_allow_html=True,
)
st.markdown('<div class="pill">● SIMULATED ENTERPRISE SYSTEMS</div>', unsafe_allow_html=True)

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
    st.stop()

chat, activity = st.columns([1.65, 1], gap="large")
with activity:
    st.subheader("Investigation activity")
    a, b = st.columns(2)
    a.metric("Tool calls", len(view["activity"]))
    b.metric("Model steps · this turn", view["usage"]["model_steps"])
    st.caption("Every input and result is recorded by the backend.")
    render_activity(view["activity"])

with chat:
    st.subheader("Support conversation")
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
