"""Presentation-only styles; no dynamic HTML or user content is interpolated."""

import streamlit as st


def apply_theme():
    st.markdown(
        """
    <style>
    html, body, [class*="css"], .stApp {font-family:Inter,'Segoe UI',sans-serif;}
    .stApp {background:#0b1018;}
    [data-testid="stSidebar"] {background:#111925; border-right:1px solid #233044;}
    .block-container {max-width:1500px;padding-top:4.5rem;}
    .brand {color:#79e0c3;letter-spacing:.13em;font-size:12px;font-weight:700;margin-bottom:14px;}
    .hero {font-size:36px;font-weight:700;letter-spacing:-1px;line-height:1.15;margin:0 0 12px;}
    .subtitle {color:#9eafc4;font-size:16px;line-height:1.6;max-width:650px;}
    .pill {display:inline-block;border:1px solid #304736;color:#a7e0bb;background:#15251e;border-radius:20px;padding:5px 12px;font-size:12px;margin:8px 0 16px;}
    [data-testid="stChatMessage"] {background:#121c29;border:1px solid #233044;border-radius:14px;}
    [data-testid="stMetric"] {background:#121c29;padding:14px;border:1px solid #233044;border-radius:12px;}
    .stButton>button {border-radius:9px;}
    hr {border-color:#233044;}
    .stAppDeployButton {display:none;}
    </style>
    """,
        unsafe_allow_html=True,
    )
