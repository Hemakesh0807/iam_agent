"""
Agentic IAM — Streamlit Frontend
Entry point. Run with: streamlit run frontend/app.py
from the project root (agentic-iam/).
"""

import sys
from pathlib import Path

# Ensure project root is on the Python path so all bot modules are importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

st.set_page_config(
    page_title="Agentic IAM",
    page_icon="🔐",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🔐 Agentic IAM")
    st.caption("AI-driven Identity & Access Management")
    st.divider()

    from shared.config import config
    env_color = {"dev": "🟡", "staging": "🟠", "prod": "🔴"}.get(
        config.environment, "⚪"
    )
    st.caption(f"{env_color} Environment: **{config.environment.upper()}**")

    if config.skip_approval:
        st.warning("⚠ SKIP_APPROVAL is ON", icon="⚠️")

    st.divider()
    st.caption("Navigate using the pages above ↑")

# ── Home page ─────────────────────────────────────────────────────────────────
st.title("🔐 Agentic IAM")
st.markdown(
    "Welcome to the Agentic IAM dashboard. "
    "Use the sidebar to navigate between pages."
)

col1, col2, col3, col4 = st.columns(4)
col1.page_link("pages/1_dashboard.py",        label="📊 Dashboard",       icon="📊")
col2.page_link("pages/2_user_management.py",  label="👤 User Management", icon="👤")
col3.page_link("pages/3_app_management.py",   label="🖥 App Management",  icon="🖥️")
col4.page_link("pages/4_bulk_csv.py",         label="📂 Bulk CSV",        icon="📂")

col5, col6, col7, _ = st.columns(4)
col5.page_link("pages/5_approvals.py",        label="✅ Approvals",       icon="✅")
col6.page_link("pages/6_audit_log.py",        label="📋 Audit Log",       icon="📋")
col7.page_link("pages/7_settings.py",         label="⚙️ Settings",        icon="⚙️")