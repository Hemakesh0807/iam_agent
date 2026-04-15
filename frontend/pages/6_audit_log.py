import csv
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(
    page_title="Audit Log — Agentic IAM", page_icon="📋", layout="wide"
)
st.title("📋 Audit Log")


# ── Load entries ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
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


all_entries = load_all_entries()

if not all_entries:
    st.info("No audit log entries yet. Run some flows to see activity here.")
    st.stop()

# ── Filters ───────────────────────────────────────────────────────────────────
st.subheader("Filters")
col1, col2, col3 = st.columns(3)

all_flows    = sorted(set(e.get("flow_name", "") for e in all_entries))
all_statuses = sorted(set(e.get("status", "") for e in all_entries))

selected_flows    = col1.multiselect("Flow",   options=all_flows,    default=all_flows)
selected_statuses = col2.multiselect("Status", options=all_statuses, default=all_statuses)
search_principal  = col3.text_input("Search principal ID", placeholder="Filter by user/app ID")

# Apply filters
filtered = [
    e for e in all_entries
    if e.get("flow_name") in selected_flows
    and e.get("status") in selected_statuses
    and (not search_principal or search_principal.lower() in e.get("principal_id", "").lower())
]

st.caption(f"Showing {len(filtered)} of {len(all_entries)} entries.")

col_ref, col_exp = st.columns([1, 5])
if col_ref.button("🔄 Refresh"):
    st.cache_data.clear()
    st.rerun()

st.divider()

# ── Table ─────────────────────────────────────────────────────────────────────
table_rows = []
for e in filtered:
    table_rows.append({
        "Timestamp":    e.get("timestamp", "")[:19].replace("T", " "),
        "Flow":         e.get("flow_name", "").replace("_", " ").title(),
        "Status":       e.get("status", ""),
        "Bot":          e.get("bot_type", ""),
        "Principal ID": e.get("principal_id", "")[:36],
        "Requested by": e.get("requested_by", ""),
    })

st.dataframe(table_rows, use_container_width=True, hide_index=True)

# ── Row detail expander ───────────────────────────────────────────────────────
st.subheader("Entry details")
st.caption("Select an entry index to see full details including Graph API operations.")

if filtered:
    idx = st.number_input(
        "Entry index (0 = most recent)",
        min_value=0,
        max_value=len(filtered) - 1,
        value=0,
        step=1,
    )
    entry = filtered[int(idx)]

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
            st.caption("No operations recorded.")

# ── Export ────────────────────────────────────────────────────────────────────
st.divider()
if filtered:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=[
        "timestamp", "flow_name", "status", "bot_type",
        "principal_id", "requested_by", "error",
    ])
    writer.writeheader()
    for e in filtered:
        writer.writerow({
            "timestamp":    e.get("timestamp", ""),
            "flow_name":    e.get("flow_name", ""),
            "status":       e.get("status", ""),
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