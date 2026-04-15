import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(
    page_title="Approvals — Agentic IAM", page_icon="✅", layout="wide"
)
st.title("✅ Pending Approvals")
st.caption("Review and approve or reject escalated IAM requests.")


# ── Load escalated entries ────────────────────────────────────────────────────

@st.cache_data(ttl=15)
def load_escalated() -> list[dict]:
    log_path = Path(__file__).resolve().parent.parent.parent / "audit.jsonl"
    if not log_path.exists():
        return []
    entries = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    e = json.loads(line)
                    if e.get("status") == "escalated":
                        entries.append(e)
                except json.JSONDecodeError:
                    continue
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return entries


escalated = load_escalated()

if not escalated:
    st.success("No pending approvals — everything is up to date.")
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

st.info(f"{len(escalated)} request(s) pending approval.", icon="⏳")

if st.button("🔄 Refresh"):
    st.cache_data.clear()
    st.rerun()

st.divider()

# ── Approval cards ────────────────────────────────────────────────────────────
for entry in escalated:
    flow       = entry.get("flow_name", "unknown").replace("_", " ").title()
    principal  = entry.get("principal_id", "unknown")
    requested  = entry.get("requested_by", "unknown")
    timestamp  = entry.get("timestamp", "")[:19].replace("T", " ")
    details    = entry.get("details", {})
    reason     = details.get("reason", entry.get("error", "No reason recorded"))
    entry_id   = entry.get("id", principal)

    with st.container(border=True):
        c1, c2 = st.columns([3, 1])

        with c1:
            st.markdown(f"**{flow}**")
            st.caption(f"Principal: `{principal}`")
            st.caption(f"Requested by: {requested} — {timestamp}")
            st.caption(f"Reason: {reason}")

        with c2:
            approve_key = f"approve_{entry_id}"
            reject_key  = f"reject_{entry_id}"

            if st.button("✅ Approve", key=approve_key, type="primary"):
                # Re-trigger the flow by calling the bot directly
                # The approval gate is bypassed since admin has reviewed
                st.info("Approval noted. Re-triggering the flow...")
                st.warning(
                    "Full approval callback will be wired in Phase 4 "
                    "(notifier + callback endpoint). For now, use SKIP_APPROVAL=true "
                    "and re-submit the request manually.",
                    icon="ℹ️",
                )

            if st.button("❌ Reject", key=reject_key):
                st.warning(f"Request rejected. Logging decision for {principal}.")
                # In production: update Cosmos DB entry status to 'rejected'