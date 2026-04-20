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

COUNTRY_CODES = {
    "India (IN)":          "IN",
    "United States (US)":  "US",
    "United Kingdom (GB)": "GB",
    "Australia (AU)":      "AU",
    "Canada (CA)":         "CA",
    "Germany (DE)":        "DE",
    "France (FR)":         "FR",
    "Singapore (SG)":      "SG",
    "UAE (AE)":            "AE",
    "Japan (JP)":          "JP",
}


@st.cache_data(ttl=300, show_spinner="Loading groups...")
def fetch_groups() -> list[dict]:
    return asyncio.run(client.list_groups())


@st.cache_data(ttl=300, show_spinner="Loading licenses...")
def fetch_licenses() -> list[dict]:
    return asyncio.run(client.list_licenses())


def resolve_group_ids(names: list[str], all_groups: list[dict]) -> list[str]:
    m = {g["displayName"]: g["id"] for g in all_groups}
    return [m[n] for n in names if n in m]


def resolve_sku_id(label: str, all_lics: list[dict]) -> str | None:
    for l in all_lics:
        if _lic_label(l) == label:
            return l["skuId"]
    return None


def _lic_label(l: dict) -> str:
    c = l.get("consumedUnits", 0)
    a = l.get("prepaidUnits", {}).get("enabled", 0)
    return f"{l['skuPartNumber']} ({c} of {a} used)"


tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "➕ Onboard User", "➖ Offboard User", "🚨 Isolate User",
    "👥 Manage Groups", "🪪 Manage Licenses",
])


# ════════════════ TAB 1 — Onboard ════════════════════════════════════════════
with tab1:
    st.subheader("Onboard a new user")
    with st.form("onboard_form"):
        c1, c2 = st.columns(2)
        display_name  = c1.text_input("Display name *",  placeholder="Jane Doe")
        mail_nickname = c2.text_input("Mail nickname *",  placeholder="janedoe")
        upn = st.text_input("User Principal Name *", placeholder="janedoe@yourdomain.com")
        c3, c4 = st.columns(2)
        department = c3.text_input("Department *", placeholder="Engineering")
        job_title  = c4.text_input("Job title *",  placeholder="Software Engineer")
        c5, c6 = st.columns(2)
        usage_location = c5.selectbox(
            "Usage location *", options=list(COUNTRY_CODES.keys()), index=0,
            help="Required by Entra ID before a license can be assigned.",
        )
        manager_id = c6.text_input("Manager object ID (optional)")

        st.markdown("**Groups** (optional — assignable later from Manage Groups tab)")
        try:
            all_groups = fetch_groups()
            selected_group_names = st.multiselect(
                "Select groups", options=[g["displayName"] for g in all_groups]
            )
        except Exception as e:
            st.error(f"Could not load groups: {e}")
            selected_group_names, all_groups = [], []

        st.markdown("**License** (optional — assignable later from Manage Licenses tab)")
        try:
            all_licenses = fetch_licenses()
            lic_labels   = ["(No license)"] + [_lic_label(l) for l in all_licenses]
            sel_lic      = st.selectbox("Select license", options=lic_labels)
        except Exception as e:
            st.error(f"Could not load licenses: {e}")
            sel_lic, all_licenses = "(No license)", []

        submitted = st.form_submit_button("🚀 Onboard User", type="primary")

    if submitted:
        errors = []
        if not display_name:          errors.append("Display name is required.")
        if not mail_nickname:         errors.append("Mail nickname is required.")
        if not upn or "@" not in upn: errors.append("Valid UPN is required.")
        if not department:            errors.append("Department is required.")
        if not job_title:             errors.append("Job title is required.")
        for e in errors: st.error(e)
        if not errors:
            group_ids = resolve_group_ids(selected_group_names, all_groups)
            sku_id    = resolve_sku_id(sel_lic, all_licenses) if sel_lic != "(No license)" else None
            loc_code  = COUNTRY_CODES[usage_location]
            from user_bot.triggers.admin_portal import handle_admin_request
            with st.spinner("Running onboarding flow..."):
                try:
                    state = asyncio.run(handle_admin_request({
                        "action": "onboard_user", "requested_by": "streamlit_admin",
                        "payload": {
                            "display_name": display_name, "mail_nickname": mail_nickname,
                            "user_principal_name": upn, "department": department,
                            "job_title": job_title, "usage_location": loc_code,
                            "manager_id": manager_id or None,
                            "group_ids": group_ids, "license_sku_id": sku_id,
                            "app_role_assignments": [],
                        },
                    }))
                    if state.get("status") == "completed":
                        r = state["result"]
                        st.success("✅ User onboarded successfully!")
                        st.json({"user_id": r.get("user_id"), "upn": r.get("user_principal_name"),
                                 "usage_location": loc_code, "groups_assigned": r.get("groups_assigned"),
                                 "license": bool(sku_id)})
                        st.info("Assign additional groups or licenses from the tabs above.", icon="ℹ️")
                    else:
                        st.error(f"Status: {state.get('status')}")
                        if state.get("error"): st.exception(state["error"])
                except Exception as exc:
                    st.error(f"Error: {exc}")


# ════════════════ TAB 2 — Offboard ═══════════════════════════════════════════
with tab2:
    st.subheader("Offboard an existing user")
    with st.form("offboard_form"):
        off_upn = st.text_input("UPN *", placeholder="jane@yourdomain.com")
        off_reason = st.text_area("Reason *", placeholder="Employee resigned")
        off_sub = st.form_submit_button("🗑 Offboard User", type="primary")

    if off_sub:
        errors = []
        if not off_upn:
            errors.append("UPN is required.")
        if not off_reason or len(off_reason.strip()) < 5:
            errors.append("Reason must be at least 5 characters.")
        for e in errors:
            st.error(e)

        if not errors:
            from user_bot.triggers.admin_portal import handle_admin_request
            with st.spinner("Resolving user and running offboarding..."):
                try:
                    # 🔥 Step 1: Resolve user using UPN
                    user = asyncio.run(client.get_user_by_upn(off_upn))
                    if not user:
                        st.error("❌ No user found with this UPN.")
                        st.stop()
                    user_id = user["id"]
                    # ✅ Optional: show confirmation
                    st.info(
                        f"User found: {user.get('displayName')} "
                        f"({user.get('userPrincipalName')})"
                    )
                    # 🔥 Step 2: Call backend with resolved user_id
                    state = asyncio.run(handle_admin_request({
                        "action": "offboard_user",
                        "requested_by": "streamlit_admin",
                        "payload": {
                            "user_id": user_id,
                            "user_principal_name": off_upn,
                            "reason": off_reason,
                            "revoke_sessions": True,
                            "remove_licenses": True,
                            "remove_group_memberships": True,
                        },
                    }))

                    s = state.get("status")
                    if s == "completed":
                        st.success("✅ Offboarded.")
                        st.json(state.get("result", {}))
                    elif s == "escalated":
                        st.warning("⏳ Escalated. Check Approvals page.")
                    else:
                        st.error(f"Status: {s}")
                        if state.get("error"):
                            st.exception(state.get("error"))
                except Exception as exc:
                    st.error(f"Error: {exc}")

# with tab2:
#     st.subheader("Offboard an existing user")
#     with st.form("offboard_form"):
#         off_uid    = st.text_input("User object ID *", placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
#         off_upn    = st.text_input("UPN *",            placeholder="jane@yourdomain.com")
#         off_reason = st.text_area("Reason *",          placeholder="Employee resigned")
#         off_sub    = st.form_submit_button("🗑 Offboard User", type="primary")

#     if off_sub:
#         errors = []
#         if not off_uid:    errors.append("User object ID is required.")
#         if not off_upn:    errors.append("UPN is required.")
#         if not off_reason or len(off_reason.strip()) < 5:
#             errors.append("Reason must be at least 5 characters.")
#         for e in errors: st.error(e)
#         if not errors:
#             from user_bot.triggers.admin_portal import handle_admin_request
#             with st.spinner("Running offboarding..."):
#                 try:
#                     state = asyncio.run(handle_admin_request({
#                         "action": "offboard_user", "requested_by": "streamlit_admin",
#                         "payload": {"user_id": off_uid, "user_principal_name": off_upn,
#                                     "reason": off_reason, "revoke_sessions": True,
#                                     "remove_licenses": True, "remove_group_memberships": True},
#                     }))
#                     s = state.get("status")
#                     if s == "completed":   st.success("✅ Offboarded."); st.json(state.get("result", {}))
#                     elif s == "escalated": st.warning("⏳ Escalated. Check Approvals page.")
#                     else:                  st.error(f"Status: {s}"); st.exception(state.get("error", ""))
#                 except Exception as exc:
#                     st.error(f"Error: {exc}")


# ════════════════ TAB 3 — Isolate ════════════════════════════════════════════
with tab3:
    st.subheader("Isolate a compromised user")
    st.warning(
        "Immediately disables account, revokes sessions, forces password reset.",
        icon="⚠️"
    )
    with st.form("isolate_form"):
        iso_upn = st.text_input("UPN *", placeholder="jane@yourdomain.com")
        iso_aid = st.text_input("Alert ID *", placeholder="INC-0001")
        iso_rsn = st.text_area("Alert reason *")
        iso_sev = st.selectbox("Severity *", ["high", "medium", "low"])
        iso_sub = st.form_submit_button("🚨 Isolate User", type="primary")

    if iso_sub:
        errors = []
        if not iso_upn:
            errors.append("UPN is required.")
        if not iso_aid:
            errors.append("Alert ID is required.")
        if not iso_rsn:
            errors.append("Alert reason is required.")
        for e in errors:
            st.error(e)

        if not errors:
            from user_bot.triggers.admin_portal import handle_admin_request
            with st.spinner("Resolving user and isolating..."):
                try:
                    # 🔥 Step 1: Resolve user from UPN
                    user = asyncio.run(client.get_user_by_upn(iso_upn))
                    if not user:
                        st.error("❌ No user found with this UPN.")
                        st.stop()

                    user_id = user["id"]
                    # ✅ Optional: show confirmation
                    st.info(
                        f"User found: {user.get('displayName')} "
                        f"({user.get('userPrincipalName')})"
                    )
                    # 🔥 Step 2: Call backend
                    state = asyncio.run(handle_admin_request({
                        "action": "isolate_user",
                        "requested_by": "streamlit_admin",
                        "payload": {
                            "user_id": user_id,
                            "user_principal_name": iso_upn,
                            "alert_id": iso_aid,
                            "alert_reason": iso_rsn,
                            "severity": iso_sev,
                            "auto_isolate": iso_sev == "high",
                        },
                    }))

                    s = state.get("status")
                    if s in ("completed", "isolated"):
                        st.success("✅ Isolated.")
                        st.json(state.get("result", {}))
                    elif s == "escalated":
                        st.warning("⏳ Escalated.")
                    else:
                        st.error(f"Status: {s}")
                        if state.get("error"):
                            st.exception(state.get("error"))
                except Exception as exc:
                    st.error(f"Error: {exc}")

# with tab3:
#     st.subheader("Isolate a compromised user")
#     st.warning("Immediately disables account, revokes sessions, forces password reset.", icon="⚠️")
#     with st.form("isolate_form"):
#         iso_uid  = st.text_input("User object ID *")
#         iso_upn  = st.text_input("UPN *")
#         iso_aid  = st.text_input("Alert ID *")
#         iso_rsn  = st.text_area("Alert reason *")
#         iso_sev  = st.selectbox("Severity *", ["high", "medium", "low"])
#         iso_sub  = st.form_submit_button("🚨 Isolate User", type="primary")

#     if iso_sub:
#         errors = []
#         if not iso_uid: errors.append("User object ID is required.")
#         if not iso_upn: errors.append("UPN is required.")
#         if not iso_aid: errors.append("Alert ID is required.")
#         if not iso_rsn: errors.append("Alert reason is required.")
#         for e in errors: st.error(e)
#         if not errors:
#             from user_bot.triggers.admin_portal import handle_admin_request
#             with st.spinner("Isolating..."):
#                 try:
#                     state = asyncio.run(handle_admin_request({
#                         "action": "isolate_user", "requested_by": "streamlit_admin",
#                         "payload": {"user_id": iso_uid, "user_principal_name": iso_upn,
#                                     "alert_id": iso_aid, "alert_reason": iso_rsn,
#                                     "severity": iso_sev, "auto_isolate": iso_sev == "high"},
#                     }))
#                     s = state.get("status")
#                     if s in ("completed", "isolated"): st.success("✅ Isolated."); st.json(state.get("result", {}))
#                     elif s == "escalated":             st.warning("⏳ Escalated.")
#                     else:                              st.error(f"Status: {s}")
#                 except Exception as exc:
#                     st.error(f"Error: {exc}")


# ════════════════ TAB 4 — Manage Groups ══════════════════════════════════════
with tab4:
    st.subheader("Manage group memberships")
    grp_id_input = st.text_input("User UPN ", key="grp_id", placeholder="jane@yourdomain.com")

    if grp_id_input and st.button("🔍 Look up", key="grp_lookup"):
        with st.spinner("Looking up..."):
            try:
                u = asyncio.run(client.get_user_by_upn(grp_id_input))
                st.session_state["grp_user"] = u
                mems = asyncio.run(client.get_user_memberships(u["id"]))
                st.session_state["grp_mems"] = [m for m in mems if m.get("@odata.type") == "#microsoft.graph.group"]
            except Exception as exc:
                st.error(f"User not found: {exc}"); st.session_state.pop("grp_user", None)

    if "grp_user" in st.session_state:
        u    = st.session_state["grp_user"]
        mems = st.session_state.get("grp_mems", [])
        st.success(f"**{u['displayName']}** — `{u['userPrincipalName']}`")
        st.markdown(f"**Current memberships ({len(mems)})**")
        for m in mems: st.caption(f"• {m.get('displayName', m['id'])}")
        if not mems: st.caption("No group memberships.")

        st.divider()
        ca, cr = st.columns(2)
        with ca:
            st.markdown("**Add to group**")
            try:
                all_grps   = fetch_groups()
                cur_ids    = {m["id"] for m in mems}
                avail_grps = [g for g in all_grps if g["id"] not in cur_ids]
                add_sel    = st.selectbox("Group to add", ["(select)"] + [g["displayName"] for g in avail_grps], key="grp_add")
                if st.button("➕ Add", key="grp_add_btn") and add_sel != "(select)":
                    with st.spinner(f"Adding to {add_sel}..."):
                        g = asyncio.run(client.get_group_by_name(add_sel))
                        asyncio.run(client.add_user_to_group(g["id"], u["id"]))
                        st.success(f"✅ Added to **{add_sel}**.")
                        st.cache_data.clear(); st.rerun()
            except Exception as exc: st.error(f"Error: {exc}")

        with cr:
            st.markdown("**Remove from group**")
            if mems:
                rem_sel = st.selectbox("Group to remove", ["(select)"] + [m.get("displayName","") for m in mems], key="grp_rem")
                if st.button("➖ Remove", key="grp_rem_btn") and rem_sel != "(select)":
                    with st.spinner(f"Removing from {rem_sel}..."):
                        g = asyncio.run(client.get_group_by_name(rem_sel))
                        asyncio.run(client.remove_user_from_group(g["id"], u["id"]))
                        st.success(f"✅ Removed from **{rem_sel}**.")
                        st.cache_data.clear(); st.rerun()
            else:
                st.caption("No groups to remove from.")


# ════════════════ TAB 5 — Manage Licenses ════════════════════════════════════
with tab5:
    st.subheader("Manage license assignments")
    lic_id_input = st.text_input("User UPN or object ID", key="lic_id", placeholder="jane@yourdomain.com")
 
    if lic_id_input and st.button("🔍 Look up", key="lic_lookup"):
        with st.spinner("Looking up..."):
            try:
                u = asyncio.run(client.get_user_by_upn(lic_id_input))
                st.session_state["lic_user"]         = u
                all_lics                             = fetch_licenses()
                sku_map                              = {l["skuId"]: l["skuPartNumber"] for l in all_lics}
                assigned                             = asyncio.run(client.get_user_licenses(u["id"]))
                usage_loc                            = asyncio.run(client.get_usage_location(u["id"]))
                st.session_state["lic_assigned"]     = assigned
                st.session_state["lic_sku_map"]      = sku_map
                st.session_state["lic_all"]          = all_lics
                st.session_state["lic_usage_loc"]    = usage_loc
            except Exception as exc:
                st.error(f"User not found: {exc}"); st.session_state.pop("lic_user", None)
 
    if "lic_user" in st.session_state:
        u          = st.session_state["lic_user"]
        assigned   = st.session_state.get("lic_assigned", [])
        sku_map    = st.session_state.get("lic_sku_map", {})
        all_lics   = st.session_state.get("lic_all", [])
        usage_loc  = st.session_state.get("lic_usage_loc")
 
        st.success(f"**{u['displayName']}** — `{u['userPrincipalName']}`")
 
        # ── Usage location status ─────────────────────────────────────────────
        if usage_loc:
            st.caption(f"📍 Usage location: **{usage_loc}**")
        else:
            st.warning(
                "⚠ This user has no usage location set. "
                "A usage location is required before any license can be assigned.",
                icon="⚠️",
            )
            st.markdown("**Set usage location**")
            loc_opts = {
                "India (IN)": "IN", "United States (US)": "US",
                "United Kingdom (GB)": "GB", "Australia (AU)": "AU",
                "Canada (CA)": "CA", "Germany (DE)": "DE",
                "France (FR)": "FR", "Singapore (SG)": "SG",
                "UAE (AE)": "AE", "Japan (JP)": "JP",
            }
            loc_sel = st.selectbox(
                "Select usage location",
                options=list(loc_opts.keys()),
                key="lic_loc_sel",
            )
            if st.button("💾 Set usage location", key="lic_set_loc"):
                with st.spinner("Setting usage location..."):
                    try:
                        code = loc_opts[loc_sel]
                        asyncio.run(client.set_usage_location(u["id"], code))
                        st.session_state["lic_usage_loc"] = code
                        st.success(f"✅ Usage location set to **{code}**. You can now assign licenses.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Failed to set usage location: {exc}")
 
        st.markdown(f"**Assigned licenses ({len(assigned)})**")
        for a in assigned: st.caption(f"• {sku_map.get(a['skuId'], a['skuId'])}")
        if not assigned: st.caption("No licenses assigned.")
 
        st.divider()
        ca, cr = st.columns(2)
 
        with ca:
            st.markdown("**Assign license**")
            if not usage_loc:
                st.info("Set a usage location above before assigning a license.", icon="ℹ️")
            else:
                assigned_ids = {a["skuId"] for a in assigned}
                avail_lics   = [l for l in all_lics if l["skuId"] not in assigned_ids]
                if avail_lics:
                    asgn_sel = st.selectbox("License to assign", ["(select)"] + [_lic_label(l) for l in avail_lics], key="lic_asgn")
                    if st.button("➕ Assign", key="lic_asgn_btn") and asgn_sel != "(select)":
                        sku = resolve_sku_id(asgn_sel, avail_lics)
                        if sku:
                            with st.spinner("Assigning..."):
                                asyncio.run(client.assign_license(u["id"], sku))
                                st.success("✅ License assigned.")
                                st.cache_data.clear(); st.rerun()
                else:
                    st.caption("All licenses already assigned.")
 
        with cr:
            st.markdown("**Remove license**")
            if assigned:
                rem_opts = {sku_map.get(a["skuId"], a["skuId"]): a["skuId"] for a in assigned}
                rem_sel  = st.selectbox("License to remove", ["(select)"] + list(rem_opts.keys()), key="lic_rem")
                if st.button("➖ Remove", key="lic_rem_btn") and rem_sel != "(select)":
                    with st.spinner("Removing..."):
                        asyncio.run(client.remove_license(u["id"], rem_opts[rem_sel]))
                        st.success("✅ License removed.")
                        st.cache_data.clear(); st.rerun()
            else:
                st.caption("No licenses to remove.")
 
