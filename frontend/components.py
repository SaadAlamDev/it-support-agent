"""Shared evidence presentation for live and saved investigations."""

import streamlit as st


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
