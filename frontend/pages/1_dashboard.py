import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(page_title="Dashboard — Agentic IAM", page_icon="📊", layout="wide")
st.title("📊 Dashboard")


# ── Load audit log ────────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def load_audit_entries() -> list[dict]:
    """Load all entries from local audit.jsonl file."""
    log_path = Path(__file__).resolve().parent.parent.parent / "audit.jsonl"
    if not log_path.exists():
        return []
    entries = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    # Sort newest first
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return entries


entries = load_audit_entries()

# ── Summary cards ─────────────────────────────────────────────────────────────
st.subheader("Overview")

total        = len(entries)
completed    = sum(1 for e in entries if e.get("status") == "completed")
failed       = sum(1 for e in entries if e.get("status") == "failed")
escalated    = sum(1 for e in entries if e.get("status") == "escalated")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Actions",       total)
col2.metric("Completed",           completed, delta=None)
col3.metric("Failed",              failed,    delta=None)
col4.metric("Pending Approvals",   escalated, delta=None)

st.divider()

# ── Flow breakdown ────────────────────────────────────────────────────────────
st.subheader("Actions by flow")

flow_counts = {}
for e in entries:
    flow = e.get("flow_name", "unknown")
    flow_counts[flow] = flow_counts.get(flow, 0) + 1

if flow_counts:
    cols = st.columns(len(flow_counts))
    for col, (flow, count) in zip(cols, flow_counts.items()):
        col.metric(flow.replace("_", " ").title(), count)
else:
    st.info("No audit entries yet. Run some flows to see activity here.")

st.divider()

# ── Recent activity ───────────────────────────────────────────────────────────
st.subheader("Recent activity")

if not entries:
    st.info("No activity recorded yet.")
else:
    recent = entries[:20]
    for e in recent:
        status = e.get("status", "")
        flow   = e.get("flow_name", "").replace("_", " ").title()
        pid    = e.get("principal_id", "")[:20]
        ts     = e.get("timestamp", "")[:19].replace("T", " ")
        by     = e.get("requested_by", "")

        icon = {"completed": "✅", "failed": "❌", "escalated": "⏳"}.get(status, "•")

        with st.container():
            c1, c2, c3, c4 = st.columns([1, 3, 3, 3])
            c1.write(icon)
            c2.write(f"**{flow}**")
            c3.write(f"`{pid}`")
            c4.write(f"{ts} — {by}")

    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()