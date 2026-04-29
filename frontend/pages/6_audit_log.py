import csv
import io
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(
    page_title="Audit Log — Agentic IAM", page_icon="📋", layout="wide"
)
st.title("📋 Audit Log")
st.caption("All actions across both bots and the AI assistant — every operation is recorded here.")


# ── Load entries ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=15)
def load_all_entries() -> list[dict]:
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
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
    return entries


def _get_action_label(entry: dict) -> str:
    """
    Return a human-readable action label.
    For AI assistant entries, uses the assistant_action from details.
    For bot entries, uses the flow_name.
    """
    details = entry.get("details", {})
    assistant_action = details.get("assistant_action")
    if assistant_action:
        return f"🤖 {assistant_action.replace('_', ' ').title()}"
    flow = entry.get("flow_name", "")
    return flow.replace("_", " ").title()


def _get_source(entry: dict) -> str:
    requested_by = entry.get("requested_by", "")
    if requested_by == "ai_assistant":
        return "AI Assistant"
    elif requested_by == "streamlit_admin":
        return "Admin UI"
    elif requested_by in ("hr_system", "csv_bulk", "sentinel_alert"):
        return requested_by.replace("_", " ").title()
    return requested_by or "unknown"


all_entries = load_all_entries()

if not all_entries:
    st.info("No audit log entries yet. Run some flows to see activity here.")
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

# ── Summary cards ─────────────────────────────────────────────────────────────
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total entries",    len(all_entries))
col2.metric("Completed",        sum(1 for e in all_entries if e.get("status") == "completed"))
col3.metric("Failed",           sum(1 for e in all_entries if e.get("status") == "failed"))
col4.metric("Escalated",        sum(1 for e in all_entries if e.get("status") == "escalated"))
col5.metric("AI Assistant",     sum(1 for e in all_entries if e.get("requested_by") == "ai_assistant"))

st.divider()

# ── Filters ───────────────────────────────────────────────────────────────────
st.subheader("Filters")
fc1, fc2, fc3, fc4 = st.columns(4)

# Source filter
all_sources   = sorted(set(_get_source(e) for e in all_entries))
sel_sources   = fc1.multiselect("Source", options=all_sources, default=all_sources)

# Status filter
all_statuses  = sorted(set(e.get("status", "") for e in all_entries))
sel_statuses  = fc2.multiselect("Status", options=all_statuses, default=all_statuses)

# Date range filter
date_options  = ["All time", "Last hour", "Last 24h", "Last 7 days"]
sel_date      = fc3.selectbox("Time range", options=date_options)

# Search
search        = fc4.text_input("Search principal ID or action", placeholder="Filter by user/app")

# Apply filters
now = datetime.utcnow()
date_cutoffs = {
    "Last hour":   now - timedelta(hours=1),
    "Last 24h":    now - timedelta(days=1),
    "Last 7 days": now - timedelta(days=7),
}

filtered = []
for e in all_entries:
    # Source
    if _get_source(e) not in sel_sources:
        continue
    # Status
    if e.get("status") not in sel_statuses:
        continue
    # Date range
    if sel_date != "All time":
        cutoff = date_cutoffs[sel_date]
        try:
            ts = datetime.fromisoformat(e.get("timestamp", "").replace("Z", ""))
            if ts < cutoff:
                continue
        except Exception:
            pass
    # Search
    if search:
        search_lower = search.lower()
        principal    = (e.get("principal_id") or "").lower()
        action_lbl   = _get_action_label(e).lower()
        details_str  = json.dumps(e.get("details", {})).lower()
        if (search_lower not in principal and
                search_lower not in action_lbl and
                search_lower not in details_str):
            continue
    filtered.append(e)

st.caption(f"Showing **{len(filtered)}** of **{len(all_entries)}** entries.")

col_ref, _ = st.columns([1, 5])
if col_ref.button("🔄 Refresh"):
    st.cache_data.clear()
    st.rerun()

st.divider()

# ── Results table ─────────────────────────────────────────────────────────────
if not filtered:
    st.info("No entries match the current filters.")
    st.stop()

table_rows = []
for e in filtered:
    status_icon = {"completed": "✅", "failed": "❌", "escalated": "⏳"}.get(e.get("status", ""), "•")
    table_rows.append({
        "Timestamp":    e.get("timestamp", "")[:19].replace("T", " "),
        "Action":       _get_action_label(e),
        "Status":       f"{status_icon} {e.get('status', '')}",
        "Source":       _get_source(e),
        "Bot":          e.get("bot_type", ""),
        "Principal ID": (e.get("principal_id") or "")[:36],
    })

st.dataframe(table_rows, use_container_width=True, hide_index=True)

# ── Entry detail ──────────────────────────────────────────────────────────────
st.divider()
st.subheader("Entry details")
st.caption("Select an entry index (0 = most recent) to see full details.")

idx = st.number_input(
    "Entry index",
    min_value=0,
    max_value=max(len(filtered) - 1, 0),
    value=0,
    step=1,
)
entry = filtered[int(idx)]

status_icon = {"completed": "✅", "failed": "❌", "escalated": "⏳"}.get(entry.get("status", ""), "•")
st.markdown(
    f"**{_get_action_label(entry)}** — "
    f"{status_icon} {entry.get('status', '')} — "
    f"Source: {_get_source(entry)} — "
    f"{entry.get('timestamp', '')[:19].replace('T', ' ')} UTC"
)

col_a, col_b = st.columns(2)
with col_a:
    st.markdown("**Metadata**")
    st.json({
        "id":           entry.get("id"),
        "flow_name":    entry.get("flow_name"),
        "status":       entry.get("status"),
        "bot_type":     entry.get("bot_type"),
        "principal_id": entry.get("principal_id"),
        "requested_by": entry.get("requested_by"),
        "timestamp":    entry.get("timestamp"),
        "error":        entry.get("error"),
    })
with col_b:
    st.markdown("**Details**")
    st.json(entry.get("details", {}))
    st.markdown("**Graph API operations**")
    ops = entry.get("graph_operations", [])
    if ops:
        for op in ops:
            st.code(op, language=None)
    else:
        st.caption("No Graph API operations recorded (direct dispatcher action).")

# ── Export ────────────────────────────────────────────────────────────────────
st.divider()
output = io.StringIO()
writer = csv.DictWriter(output, fieldnames=[
    "timestamp", "action", "status", "source", "bot_type",
    "principal_id", "requested_by", "error",
])
writer.writeheader()
for e in filtered:
    writer.writerow({
        "timestamp":    e.get("timestamp", ""),
        "action":       _get_action_label(e),
        "status":       e.get("status", ""),
        "source":       _get_source(e),
        "bot_type":     e.get("bot_type", ""),
        "principal_id": e.get("principal_id", ""),
        "requested_by": e.get("requested_by", ""),
        "error":        e.get("error", ""),
    })

st.download_button(
    label="⬇️ Export filtered log as CSV",
    data=output.getvalue(),
    file_name="audit_log_export.csv",
    mime="text/csv",
)

