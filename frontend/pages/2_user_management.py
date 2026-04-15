import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st
from shared.graph_client import GraphClient

st.set_page_config(
    page_title="User Management — Agentic IAM", page_icon="👤", layout="wide"
)
st.title("👤 User Management")

client = GraphClient()


# ── Cached resolvers ──────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner="Loading groups...")
def fetch_groups() -> list[dict]:
    return asyncio.run(client.list_groups())


@st.cache_data(ttl=300, show_spinner="Loading licenses...")
def fetch_licenses() -> list[dict]:
    return asyncio.run(client.list_licenses())


def resolve_group_ids(selected_names: list[str], all_groups: list[dict]) -> list[str]:
    """Map selected group display names to their object IDs."""
    name_to_id = {g["displayName"]: g["id"] for g in all_groups}
    return [name_to_id[n] for n in selected_names if n in name_to_id]


def resolve_sku_id(selected_label: str, all_licenses: list[dict]) -> str | None:
    """Map selected license label back to its skuId."""
    for lic in all_licenses:
        label = _license_label(lic)
        if label == selected_label:
            return lic["skuId"]
    return None


def _license_label(lic: dict) -> str:
    consumed  = lic.get("consumedUnits", 0)
    available = lic.get("prepaidUnits", {}).get("enabled", 0)
    return f"{lic['skuPartNumber']} ({consumed} of {available} used)"


# ── Tab layout ────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs(["➕ Onboard User", "➖ Offboard User", "🚨 Isolate User"])


# ════════════════════════════════════════════════════════════════════════════
# TAB 1 — Onboard User (Flow A)
# ════════════════════════════════════════════════════════════════════════════
with tab1:
    st.subheader("Onboard a new user")

    with st.form("onboard_form"):
        c1, c2 = st.columns(2)
        display_name = c1.text_input("Display name *", placeholder="Jane Doe")
        mail_nickname = c2.text_input("Mail nickname *", placeholder="janedoe")

        upn = st.text_input(
            "User Principal Name *",
            placeholder="janedoe@yourdomain.com",
        )

        c3, c4 = st.columns(2)
        department = c3.text_input("Department *", placeholder="Engineering")
        job_title  = c4.text_input("Job title *",  placeholder="Software Engineer")

        manager_id = st.text_input(
            "Manager object ID (optional)",
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        )

        st.markdown("**Group assignments**")
        try:
            all_groups = fetch_groups()
            group_names = [g["displayName"] for g in all_groups]
            selected_group_names = st.multiselect(
                "Select groups to assign *",
                options=group_names,
                help="User must be assigned to at least one group.",
            )
        except Exception as e:
            st.error(f"Could not load groups: {e}")
            selected_group_names = []
            all_groups = []

        st.markdown("**License**")
        try:
            all_licenses = fetch_licenses()
            license_labels = ["(No license)"] + [_license_label(l) for l in all_licenses]
            selected_license_label = st.selectbox(
                "Select license",
                options=license_labels,
                help="Leave as '(No license)' for service accounts or contractors.",
            )
        except Exception as e:
            st.error(f"Could not load licenses: {e}")
            selected_license_label = "(No license)"
            all_licenses = []

        submitted = st.form_submit_button("🚀 Onboard User", type="primary")

    if submitted:
        # Validation
        errors = []
        if not display_name:  errors.append("Display name is required.")
        if not mail_nickname: errors.append("Mail nickname is required.")
        if not upn:           errors.append("User Principal Name is required.")
        if "@" not in upn:    errors.append("UPN must contain '@'.")
        if not department:    errors.append("Department is required.")
        if not job_title:     errors.append("Job title is required.")
        if not selected_group_names:
            errors.append("At least one group must be selected.")

        if errors:
            for err in errors:
                st.error(err)
        else:
            group_ids   = resolve_group_ids(selected_group_names, all_groups)
            sku_id      = (
                resolve_sku_id(selected_license_label, all_licenses)
                if selected_license_label != "(No license)" else None
            )

            from user_bot.triggers.admin_portal import handle_admin_request
            with st.spinner("Running onboarding flow..."):
                try:
                    state = asyncio.run(handle_admin_request({
                        "action": "onboard_user",
                        "requested_by": "streamlit_admin",
                        "payload": {
                            "display_name":        display_name,
                            "mail_nickname":       mail_nickname,
                            "user_principal_name": upn,
                            "department":          department,
                            "job_title":           job_title,
                            "manager_id":          manager_id or None,
                            "group_ids":           group_ids,
                            "license_sku_id":      sku_id,
                            "app_role_assignments": [],
                        },
                    }))

                    if state.get("status") == "completed":
                        r = state["result"]
                        st.success(f"✅ User onboarded successfully!")
                        st.json({
                            "user_id":        r.get("user_id"),
                            "upn":            r.get("user_principal_name"),
                            "groups_assigned": r.get("groups_assigned"),
                            "license":        bool(sku_id),
                        })
                    else:
                        st.error(f"Flow ended with status: {state.get('status')}")
                        if state.get("error"):
                            st.exception(state["error"])
                except Exception as exc:
                    st.error(f"Unexpected error: {exc}")


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 — Offboard User (Flow B)
# ════════════════════════════════════════════════════════════════════════════
with tab2:
    st.subheader("Offboard an existing user")

    with st.form("offboard_form"):
        off_user_id = st.text_input(
            "User object ID *",
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        )
        off_upn = st.text_input(
            "User Principal Name *",
            placeholder="jane@yourdomain.com",
        )
        off_reason = st.text_area(
            "Reason *",
            placeholder="Employee resigned — last day 30 June 2025",
        )
        off_submitted = st.form_submit_button("🗑 Offboard User", type="primary")

    if off_submitted:
        errors = []
        if not off_user_id: errors.append("User object ID is required.")
        if not off_upn:     errors.append("UPN is required.")
        if not off_reason or len(off_reason.strip()) < 5:
            errors.append("Reason must be at least 5 characters.")

        if errors:
            for err in errors:
                st.error(err)
        else:
            from user_bot.triggers.admin_portal import handle_admin_request
            with st.spinner("Running offboarding flow..."):
                try:
                    state = asyncio.run(handle_admin_request({
                        "action": "offboard_user",
                        "requested_by": "streamlit_admin",
                        "payload": {
                            "user_id":              off_user_id,
                            "user_principal_name":  off_upn,
                            "reason":               off_reason,
                            "revoke_sessions":      True,
                            "remove_licenses":      True,
                            "remove_group_memberships": True,
                        },
                    }))

                    status = state.get("status")
                    if status == "completed":
                        st.success("✅ User offboarded successfully.")
                        st.json(state.get("result", {}))
                    elif status == "escalated":
                        st.warning(
                            "⏳ Offboarding escalated for admin approval. "
                            "Check the Approvals page."
                        )
                    else:
                        st.error(f"Flow ended with status: {status}")
                        if state.get("error"):
                            st.exception(state["error"])
                except Exception as exc:
                    st.error(f"Unexpected error: {exc}")


# ════════════════════════════════════════════════════════════════════════════
# TAB 3 — Isolate User (Flow C)
# ════════════════════════════════════════════════════════════════════════════
with tab3:
    st.subheader("Isolate a compromised user")
    st.warning(
        "This immediately disables the account, revokes all sessions, "
        "and forces a password reset.",
        icon="⚠️",
    )

    with st.form("isolate_form"):
        iso_user_id = st.text_input(
            "User object ID *",
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
        )
        iso_upn = st.text_input(
            "User Principal Name *",
            placeholder="jane@yourdomain.com",
        )
        iso_alert_id = st.text_input(
            "Alert ID *",
            placeholder="alert-uuid or INC-0001",
        )
        iso_reason = st.text_area(
            "Alert reason *",
            placeholder="Impossible travel detected — login from two countries within 1 hour",
        )
        iso_severity = st.selectbox(
            "Severity *",
            options=["high", "medium", "low"],
        )
        iso_submitted = st.form_submit_button("🚨 Isolate User", type="primary")

    if iso_submitted:
        errors = []
        if not iso_user_id:  errors.append("User object ID is required.")
        if not iso_upn:      errors.append("UPN is required.")
        if not iso_alert_id: errors.append("Alert ID is required.")
        if not iso_reason:   errors.append("Alert reason is required.")

        if errors:
            for err in errors:
                st.error(err)
        else:
            from user_bot.triggers.admin_portal import handle_admin_request
            with st.spinner("Isolating user..."):
                try:
                    state = asyncio.run(handle_admin_request({
                        "action": "isolate_user",
                        "requested_by": "streamlit_admin",
                        "payload": {
                            "user_id":              iso_user_id,
                            "user_principal_name":  iso_upn,
                            "alert_id":             iso_alert_id,
                            "alert_reason":         iso_reason,
                            "severity":             iso_severity,
                            "auto_isolate":         iso_severity == "high",
                        },
                    }))

                    status = state.get("status")
                    if status in ("completed", "isolated"):
                        st.success("✅ User isolated successfully.")
                        st.json(state.get("result", {}))
                    elif status == "escalated":
                        st.warning("⏳ Escalated for admin review — severity is not HIGH.")
                    else:
                        st.error(f"Flow ended with status: {status}")
                        if state.get("error"):
                            st.exception(state["error"])
                except Exception as exc:
                    st.error(f"Unexpected error: {exc}")