import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(
    page_title="Settings — Agentic IAM", page_icon="⚙️", layout="wide"
)
st.title("⚙️ Settings")


from shared.config import config

# ── Environment info ──────────────────────────────────────────────────────────
st.subheader("Environment")

col1, col2, col3 = st.columns(3)
col1.metric("Environment",   config.environment.upper())
col2.metric("Bot type",      config.bot_type)
col3.metric("OpenAI model",  config.openai_model)

st.divider()

# ── SKIP_APPROVAL status ──────────────────────────────────────────────────────
st.subheader("Approval gate")

if config.skip_approval:
    st.error(
        "⚠️ SKIP_APPROVAL is **ON** — the approval gate is bypassed. "
        "All flows auto-execute without human review. "
        "Set `SKIP_APPROVAL=false` in your `.env` file to re-enable approvals.",
        icon="⚠️",
    )
else:
    st.success(
        "✅ Approval gate is active. Destructive flows (offboarding, app decommission) "
        "require human approval before executing.",
        icon="✅",
    )

st.divider()

# ── Connection status ─────────────────────────────────────────────────────────
st.subheader("Connection status")
st.caption("Click to test each connection.")

if st.button("🔌 Test Graph API connection"):
    from shared.graph_client import GraphClient
    with st.spinner("Testing Graph API..."):
        try:
            client = GraphClient()
            # Lightweight call — just fetch current tenant info
            result = asyncio.run(client._get("organization", params={"$select": "displayName,id"}))
            org    = result.get("value", [{}])[0]
            st.success(
                f"✅ Graph API connected — Tenant: **{org.get('displayName', 'unknown')}**"
            )
        except Exception as exc:
            st.error(f"❌ Graph API connection failed: {exc}")

if st.button("🤖 Test OpenAI connection"):
    with st.spinner("Testing OpenAI..."):
        try:
            from ai_engine._openai_client import build_openai_client
            client = build_openai_client()
            response = asyncio.run(
                client.chat.completions.create(
                    model=config.openai_model,
                    messages=[{"role": "user", "content": "Reply with: ok"}],
                    max_tokens=5,
                )
            )
            reply = response.choices[0].message.content.strip()
            st.success(f"✅ OpenAI connected — model: **{config.openai_model}** — reply: `{reply}`")
        except Exception as exc:
            st.error(f"❌ OpenAI connection failed: {exc}")

if st.button("🗄 Test Cosmos DB connection"):
    if not config.cosmos_endpoint:
        st.warning(
            "⚠ COSMOS_DB_ENDPOINT is not set — "
            "audit logs are writing to local `audit.jsonl` file."
        )
    else:
        with st.spinner("Testing Cosmos DB..."):
            try:
                from azure.cosmos import CosmosClient
                c = CosmosClient(config.cosmos_endpoint, credential=config.cosmos_key)
                db = c.get_database_client(config.cosmos_database)
                db.read()
                st.success(
                    f"✅ Cosmos DB connected — database: **{config.cosmos_database}**"
                )
            except Exception as exc:
                st.error(f"❌ Cosmos DB connection failed: {exc}")

st.divider()

# ── .env summary ──────────────────────────────────────────────────────────────
st.subheader(".env summary")
st.caption("Shows which variables are set (values are hidden).")

env_vars = {
    "AZURE_TENANT_ID":     bool(config.tenant_id),
    "AZURE_CLIENT_ID":     bool(config.client_id),
    "AZURE_CLIENT_SECRET": bool(config.client_secret),
    "OPENAI_ENDPOINT":     bool(config.openai_endpoint),
    "OPENAI_API_KEY":      bool(config.openai_api_key),
    "COSMOS_DB_ENDPOINT":  bool(config.cosmos_endpoint),
    "SERVICE_BUS_CONN":    bool(config.servicebus_conn),
    "SKIP_APPROVAL":       config.skip_approval,
}

for var, is_set in env_vars.items():
    icon = "✅" if is_set else "⬜"
    st.write(f"{icon} `{var}`")