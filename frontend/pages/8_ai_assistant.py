"""
Page 8 — AI Assistant
Natural language IAM management. Type any instruction and the AI will
parse it, identify missing parameters, prompt for them, and execute.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st
from ai_engine.iam_assistant import IAMAssistant, get_param_label
from shared.models import AssistantAction

st.set_page_config(
    page_title="AI Assistant — Agentic IAM", page_icon="🤖", layout="wide"
)
st.title("🤖 AI Assistant")
st.caption(
    "Type any IAM instruction in plain English. "
    "The AI will extract the parameters, ask for anything missing, and execute."
)

assistant = IAMAssistant()

# ── Example instructions ──────────────────────────────────────────────────────
with st.expander("💡 Example instructions", expanded=False):
    st.markdown("""
**User onboarding / offboarding**
- *Onboard Jane Doe as a Software Engineer in the Engineering department, her UPN is janedoe@company.com*
- *Offboard john.smith@company.com — he resigned, last day was Friday*
- *Isolate user jane@company.com, we detected impossible travel, high severity*

**Group management**
- *Add jane@company.com to the DevOps team group*
- *Remove john@company.com from the Finance group*
- *Remove jane@company.com from all groups*

**License management**
- *Assign Microsoft 365 E3 license to jane@company.com*
- *Remove the SPE_E3 license from john@company.com*
- *Remove all licenses from the terminated user john@company.com*
    """)

st.divider()

# ── Instruction input ─────────────────────────────────────────────────────────
instruction = st.text_area(
    "Your instruction",
    placeholder="e.g. Onboard Jane Doe as a Software Engineer in Engineering, her UPN is janedoe@company.com",
    height=100,
    key="ai_instruction",
)

parse_clicked = st.button("🔍 Parse instruction", type="primary", disabled=not instruction.strip())

# ── Reset state on new instruction ───────────────────────────────────────────
if "last_instruction" not in st.session_state:
    st.session_state["last_instruction"] = ""

if instruction.strip() != st.session_state["last_instruction"]:
    # Instruction changed — clear previous parse result
    for key in ["parse_result", "missing_inputs", "confirmed"]:
        st.session_state.pop(key, None)
    st.session_state["last_instruction"] = instruction.strip()

# ── Parse ─────────────────────────────────────────────────────────────────────
if parse_clicked and instruction.strip():
    with st.spinner("Analysing instruction..."):
        result = asyncio.run(assistant.parse(instruction.strip()))
        st.session_state["parse_result"] = result
        st.session_state.pop("missing_inputs", None)
        st.session_state.pop("confirmed", None)

# ── Show parse result ─────────────────────────────────────────────────────────
if "parse_result" in st.session_state:
    result = st.session_state["parse_result"]

    st.divider()
    st.subheader("Parsed instruction")

    # Unknown action
    if result.action == AssistantAction.UNKNOWN:
        st.error(
            "❓ Could not identify a supported IAM action from your instruction. "
            "Please rephrase or check the examples above."
        )
        st.caption(f"Reasoning: {result.reasoning}")
        st.stop()

    # Low confidence warning
    if result.confidence < 0.65:
        st.warning(
            f"⚠ Low confidence ({result.confidence:.0%}) — please verify the "
            "parsed parameters below before confirming.",
            icon="⚠️",
        )

    # Action + confidence
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Action",     result.action.value.replace("_", " ").title())
    col_b.metric("Confidence", f"{result.confidence:.0%}")
    col_c.caption(f"*{result.reasoning}*")

    # Extracted parameters
    if result.extracted:
        st.markdown("**Extracted parameters**")
        for k, v in result.extracted.items():
            st.write(f"- **{k.replace('_', ' ').title()}**: `{v}`")

    # ── Missing parameters form ───────────────────────────────────────────────
    if result.missing_required:
        st.warning(
            f"The following required parameters are missing: "
            f"**{', '.join(result.missing_required)}**",
            icon="⚠️",
        )
        st.markdown("**Please provide the missing information:**")

        with st.form("missing_params_form"):
            missing_values: dict[str, str] = {}
            for param in result.missing_required:
                label = get_param_label(param)

                # Special handling for specific fields
                if param == "severity":
                    missing_values[param] = st.selectbox(
                        label, options=["high", "medium", "low"]
                    )
                elif param == "usage_location":
                    loc_options = {
                        "India (IN)": "IN", "United States (US)": "US",
                        "United Kingdom (GB)": "GB", "Australia (AU)": "AU",
                        "Canada (CA)": "CA",
                    }
                    sel = st.selectbox(label, options=list(loc_options.keys()))
                    missing_values[param] = loc_options[sel]
                elif "reason" in param:
                    missing_values[param] = st.text_area(label, placeholder="Enter reason...")
                else:
                    missing_values[param] = st.text_input(label, placeholder=f"Enter {param}...")

            fill_submitted = st.form_submit_button("✅ Continue with these values")

        if fill_submitted:
            # Validate all missing fields are now filled
            still_missing = [p for p, v in missing_values.items() if not v.strip()]
            if still_missing:
                st.error(f"Still missing: {', '.join(still_missing)}")
            else:
                st.session_state["missing_inputs"] = missing_values

    # If no missing params or all filled — show confirmation
    all_missing_filled = (
        not result.missing_required
        or "missing_inputs" in st.session_state
    )

    if all_missing_filled and "confirmed" not in st.session_state:
        extra = st.session_state.get("missing_inputs", {})
        all_params = {**result.extracted, **extra}

        st.divider()
        st.subheader("Confirm action")
        st.info(
            f"The following action will be executed: "
            f"**{result.action.value.replace('_', ' ').upper()}**",
            icon="🔐",
        )

        st.markdown("**Final parameters:**")
        for k, v in all_params.items():
            st.write(f"- **{k.replace('_', ' ').title()}**: `{v}`")

        col_conf, col_cancel = st.columns([1, 5])
        confirm = col_conf.button("🚀 Execute", type="primary")
        cancel  = col_cancel.button("✖ Cancel")

        if cancel:
            for key in ["parse_result", "missing_inputs", "confirmed"]:
                st.session_state.pop(key, None)
            st.rerun()

        if confirm:
            st.session_state["confirmed"] = True
            st.session_state["confirmed_params"] = all_params

    # ── Execute ───────────────────────────────────────────────────────────────
    if st.session_state.get("confirmed"):
        all_params = st.session_state.get("confirmed_params", {})

        st.divider()
        st.subheader("Executing...")

        with st.spinner(f"Running {result.action.value}..."):
            try:
                outcome = asyncio.run(assistant.dispatch(result, all_params))
                status  = outcome.get("status", "")

                if status == "completed":
                    st.success("✅ Action completed successfully!")
                    st.json(outcome)
                elif status == "escalated":
                    st.warning(
                        "⏳ Action escalated for admin approval. "
                        "Check the Approvals page.",
                        icon="⏳",
                    )
                    st.json(outcome.get("result", {}))
                elif status == "failed":
                    st.error(f"❌ Action failed: {outcome.get('error', 'Unknown error')}")
                else:
                    st.warning(f"Unexpected status: {status}")
                    st.json(outcome)

            except Exception as exc:
                st.error(f"❌ Unexpected error during execution: {exc}")

        # Clear state so admin can run another instruction
        st.divider()
        if st.button("🔄 Run another instruction"):
            for key in ["parse_result", "missing_inputs", "confirmed", "confirmed_params", "last_instruction"]:
                st.session_state.pop(key, None)
            st.rerun()
            