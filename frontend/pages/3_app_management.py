import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

st.set_page_config(
    page_title="App Management — Agentic IAM", page_icon="🖥️", layout="wide"
)
st.title("🖥️ App Management")


# ── Validation helpers ────────────────────────────────────────────────────────

def _is_valid_https_url(url: str) -> bool:
    """Check that a URL starts with https:// and has a valid domain."""
    pattern = r"^https://[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+$"
    return bool(re.match(pattern, url))


def _is_valid_entity_id(entity_id: str) -> bool:
    """
    SAML Entity IDs are URIs — they must start with https:// or urn:
    and be non-empty.
    """
    return entity_id.startswith("https://") or entity_id.startswith("urn:")


def validate_saml_form(
    display_name, app_type, template_id,
    entity_id, reply_url, sign_on_url, owner_id,
) -> list[str]:
    """Run all validation checks. Returns list of error messages."""
    errors = []

    if not display_name.strip():
        errors.append("Display name is required.")

    if app_type == "gallery" and not template_id.strip():
        errors.append("Template ID is required for gallery apps.")

    if not entity_id.strip():
        errors.append("Entity ID is required.")
    elif not _is_valid_entity_id(entity_id.strip()):
        errors.append(
            "Entity ID must be a valid URI starting with 'https://' or 'urn:'."
        )

    if not reply_url.strip():
        errors.append("Reply URL (ACS URL) is required.")
    elif not _is_valid_https_url(reply_url.strip()):
        errors.append(
            "Reply URL must be a valid HTTPS URL (must start with 'https://')."
        )

    if sign_on_url.strip() and not _is_valid_https_url(sign_on_url.strip()):
        errors.append(
            "Sign-on URL must be a valid HTTPS URL if provided."
        )

    if not owner_id.strip():
        errors.append("Owner object ID is required.")

    return errors


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab1, tab2 = st.tabs(["➕ Register SAML App", "➖ Decommission App"])


# ════════════════════════════════════════════════════════════════════════════
# TAB 1 — Register SAML App (Flow D)
# ════════════════════════════════════════════════════════════════════════════
with tab1:
    st.subheader("Register a new SAML SSO application")

    with st.form("saml_app_form"):
        display_name = st.text_input(
            "Display name *", placeholder="Salesforce CRM"
        )

        app_type = st.radio(
            "App type *",
            options=["non_gallery", "gallery"],
            format_func=lambda x: "Non-gallery (custom app)" if x == "non_gallery" else "Gallery (Microsoft app catalog)",
            horizontal=True,
        )

        # Show template ID field only for gallery apps
        template_id = ""
        if app_type == "gallery":
            template_id = st.text_input(
                "Template ID *",
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
                help="Find this in Azure Portal → Entra ID → Enterprise apps → search the app → Properties → Application template ID",
            )
        else:
            st.info(
                "Non-gallery app — no template ID needed. "
                "Microsoft's standard non-gallery SAML template will be used automatically.",
                icon="ℹ️",
            )

        st.markdown("**SAML Configuration**")
        entity_id = st.text_input(
            "Entity ID *",
            placeholder="https://saml.salesforce.com",
            help="The SAML Identifier URI for your application. Must start with https:// or urn:",
        )
        reply_url = st.text_input(
            "Reply URL — ACS URL *",
            placeholder="https://saml.salesforce.com/sso/saml",
            help="The Assertion Consumer Service URL where Entra sends the SAML response. Must be HTTPS.",
        )
        sign_on_url = st.text_input(
            "Sign-on URL (optional)",
            placeholder="https://salesforce.com/login",
            help="For SP-initiated SSO. Leave blank for IdP-initiated SSO.",
        )

        st.markdown("**Ownership & Access**")
        owner_id = st.text_input(
            "Owner object ID *",
            placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            help="Entra ID object ID of the user responsible for this application.",
        )
        group_ids_raw = st.text_input(
            "Group object IDs (optional, pipe-separated)",
            placeholder="grp-id-001|grp-id-002",
            help="Groups whose members can access this app via SSO.",
        )

        submitted = st.form_submit_button("🚀 Register App", type="primary")

    if submitted:
        errors = validate_saml_form(
            display_name, app_type, template_id,
            entity_id, reply_url, sign_on_url, owner_id,
        )

        if errors:
            for err in errors:
                st.error(err)
        else:
            group_ids = (
                [g.strip() for g in group_ids_raw.split("|") if g.strip()]
                if group_ids_raw else []
            )

            from app_bot.triggers.app_request import handle_app_request
            with st.spinner("Registering SAML application..."):
                try:
                    state = asyncio.run(handle_app_request({
                        "display_name":       display_name.strip(),
                        "app_type":           app_type,
                        "template_id":        template_id.strip() or None,
                        "entity_id":          entity_id.strip(),
                        "reply_url":          reply_url.strip(),
                        "sign_on_url":        sign_on_url.strip() or None,
                        "owner_id":           owner_id.strip(),
                        "assigned_group_ids": group_ids,
                        "requested_by":       "streamlit_admin",
                    }))

                    status = state.get("status")
                    if status == "completed":
                        r = state["result"]
                        st.success("✅ Application registered successfully!")
                        st.json({
                            "app_id":               r.get("app_id"),
                            "object_id":            r.get("object_id"),
                            "service_principal_id": r.get("service_principal_id"),
                            "cert_thumbprint":      r.get("cert_thumbprint"),
                            "cert_expiry":          r.get("cert_expiry"),
                        })
                        st.info(
                            "Next step: share the Federation Metadata URL with the "
                            "application team so they can complete SP-side configuration.",
                            icon="ℹ️",
                        )
                    elif status == "escalated":
                        st.warning("⏳ App registration escalated for approval.")
                    else:
                        st.error(f"Flow ended with status: {status}")
                        if state.get("error"):
                            st.exception(state["error"])
                except Exception as exc:
                    st.error(f"Unexpected error: {exc}")


# ════════════════════════════════════════════════════════════════════════════
# TAB 2 — Decommission App (Flow E)
# ════════════════════════════════════════════════════════════════════════════
with tab2:
    st.subheader("Decommission an application")
    st.warning(
        "This permanently deletes the app registration and service principal. "
        "All user SSO access to this app will be removed.",
        icon="⚠️",
    )

    with st.form("decom_form"):
        decom_app_id  = st.text_input("App ID (client ID) *", placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
        decom_obj_id  = st.text_input("Object ID *",           placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
        decom_sp_id   = st.text_input("Service Principal ID *", placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")
        decom_reason  = st.text_area("Reason *",               placeholder="App retired — replaced by new platform")
        decom_revoke  = st.checkbox("Revoke all user assignments before deletion", value=True)
        decom_submit  = st.form_submit_button("🗑 Decommission App", type="primary")

    if decom_submit:
        errors = []
        if not decom_app_id: errors.append("App ID is required.")
        if not decom_obj_id: errors.append("Object ID is required.")
        if not decom_sp_id:  errors.append("Service Principal ID is required.")
        if not decom_reason or len(decom_reason.strip()) < 5:
            errors.append("Reason must be at least 5 characters.")

        if errors:
            for err in errors:
                st.error(err)
        else:
            from app_bot.triggers.decom_request import handle_decom_request
            with st.spinner("Decommissioning application..."):
                try:
                    state = asyncio.run(handle_decom_request({
                        "app_id":                 decom_app_id.strip(),
                        "object_id":              decom_obj_id.strip(),
                        "service_principal_id":   decom_sp_id.strip(),
                        "reason":                 decom_reason.strip(),
                        "revoke_user_assignments": decom_revoke,
                        "requested_by":           "streamlit_admin",
                    }))

                    status = state.get("status")
                    if status == "completed":
                        st.success("✅ Application decommissioned successfully.")
                        st.json(state.get("result", {}))
                    elif status == "escalated":
                        st.warning("⏳ Decommission escalated for admin approval.")
                    else:
                        st.error(f"Flow ended with status: {status}")
                        if state.get("error"):
                            st.exception(state["error"])
                except Exception as exc:
                    st.error(f"Unexpected error: {exc}")