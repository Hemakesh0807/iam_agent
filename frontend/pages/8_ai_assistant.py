"""
Page 8 — AI Assistant
Natural language IAM management. Type any instruction, the AI extracts
parameters, prompts for anything missing, confirms, then executes.
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
    "The AI extracts parameters, asks for anything missing, confirms, then executes."
)

assistant = IAMAssistant()

# ── Country codes for usage_location prompt ───────────────────────────────────
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

# ── Examples ──────────────────────────────────────────────────────────────────
with st.expander("💡 Example instructions", expanded=False):
    st.markdown("""
**Onboarding**
- *Onboard Jane Doe as a Software Engineer in Engineering, UPN: jane@company.com, based in India*
- *Create account for John Smith, department: Finance, job title: Analyst, UPN: john@company.com, location: US*

**Offboarding / Isolation**
- *Offboard john@company.com — he resigned last Friday*
- *Isolate jane@company.com, impossible travel detected, high severity*

**Group & license management**
- *Add jane@company.com to the DevOps team group*
- *Remove john@company.com from the Finance group*
- *Assign Microsoft 365 E3 license to jane@company.com*
- *Remove all licenses from john@company.com*
    """)

st.divider()

# ═══════════════════════════════════════════════════════════════════════════════
# State machine:
#   stage = "input"      → show instruction textbox
#   stage = "parsed"     → show extracted params
#   stage = "filling"    → show missing params form
#   stage = "confirm"    → show confirmation before execute
#   stage = "done"       → show result
# ═══════════════════════════════════════════════════════════════════════════════

if "stage" not in st.session_state:
    st.session_state["stage"] = "input"


def _reset():
    for k in ["stage", "parse_result", "missing_values", "final_params", "exec_result"]:
        st.session_state.pop(k, None)
    st.rerun()


# ── STAGE: input ──────────────────────────────────────────────────────────────
if st.session_state["stage"] == "input":

    instruction = st.text_area(
        "Your instruction",
        placeholder="e.g. Onboard Jane Doe as a Software Engineer in Engineering, UPN: jane@company.com, based in India",
        height=110,
        key="ai_instruction_input",
    )

    if st.button("🔍 Parse instruction", type="primary", disabled=not (instruction or "").strip()):
        with st.spinner("Analysing instruction..."):
            result = asyncio.run(assistant.parse(instruction.strip()))
            st.session_state["parse_result"] = result
            st.session_state["stage"] = "parsed"
            st.rerun()


# ── STAGE: parsed ─────────────────────────────────────────────────────────────
elif st.session_state["stage"] == "parsed":
    result = st.session_state["parse_result"]

    # Show original instruction for context
    st.info(f"**Instruction:** {st.session_state.get('ai_instruction_input', '')}", icon="📝")

    # Unknown action
    if result.action == AssistantAction.UNKNOWN:
        st.error(
            "❓ Could not identify a supported IAM action. "
            "Please rephrase or check the examples above."
        )
        st.caption(f"Reasoning: {result.reasoning}")
        if st.button("🔄 Try again"):
            _reset()
        st.stop()

    # Low confidence
    if result.confidence < 0.65:
        st.warning(
            f"⚠ Low confidence ({result.confidence:.0%}) — please review the extracted "
            "parameters carefully before proceeding.",
            icon="⚠️",
        )

    # Action summary
    col_a, col_b = st.columns([1, 3])
    col_a.metric("Action",     result.action.value.replace("_", " ").title())
    col_b.metric("Confidence", f"{result.confidence:.0%}")
    st.caption(f"*{result.reasoning}*")

    # Extracted parameters
    if result.extracted:
        st.markdown("**Extracted parameters**")
        for k, v in result.extracted.items():
            st.write(f"- **{k.replace('_', ' ').title()}**: `{v}`")

    # Missing parameters
    if result.missing_required:
        st.warning(
            f"Missing required parameters: **{', '.join(p.replace('_', ' ') for p in result.missing_required)}**",
            icon="⚠️",
        )
        col1, col2 = st.columns([1, 5])
        if col1.button("➡ Provide missing info", type="primary"):
            st.session_state["stage"] = "filling"
            st.rerun()
        if col2.button("✖ Cancel"):
            _reset()
    else:
        # All params present — go straight to confirm
        st.success("✅ All required parameters extracted. Ready to execute.")
        col1, col2 = st.columns([1, 5])
        if col1.button("➡ Review & confirm", type="primary"):
            st.session_state["final_params"] = dict(result.extracted)
            st.session_state["stage"] = "confirm"
            st.rerun()
        if col2.button("✖ Cancel"):
            _reset()


# ── STAGE: filling ────────────────────────────────────────────────────────────
elif st.session_state["stage"] == "filling":
    result = st.session_state["parse_result"]

    st.info(f"**Instruction:** {st.session_state.get('ai_instruction_input', '')}", icon="📝")
    st.subheader("Provide missing parameters")
    st.caption(
        f"Action: **{result.action.value.replace('_', ' ').title()}** — "
        f"please fill in the fields below."
    )

    # Show already extracted params as read-only reference
    if result.extracted:
        with st.expander("Already extracted", expanded=False):
            for k, v in result.extracted.items():
                st.write(f"- **{k.replace('_', ' ').title()}**: `{v}`")

    # Dynamic form for missing fields
    missing_values: dict = {}
    with st.form("missing_params_form"):
        for param in result.missing_required:
            label = get_param_label(param)

            if param == "usage_location":
                sel = st.selectbox(
                    f"{label} *",
                    options=list(COUNTRY_CODES.keys()),
                    help="Required by Entra ID before a license can be assigned to the user.",
                )
                missing_values[param] = COUNTRY_CODES[sel]

            elif param == "severity":
                missing_values[param] = st.selectbox(
                    f"{label} *", options=["high", "medium", "low"]
                )

            elif "reason" in param or "alert_reason" in param:
                missing_values[param] = st.text_area(
                    f"{label} *", placeholder="Enter reason..."
                )

            else:
                missing_values[param] = st.text_input(
                    f"{label} *",
                    placeholder=f"Enter {param.replace('_', ' ')}...",
                )

        filled = st.form_submit_button("✅ Continue", type="primary")

    if filled:
        # Validate all required fields are now filled.
        # Selectbox fields (usage_location, severity) always have a value
        # since Streamlit pre-selects the first option — no special handling needed.
        still_missing = [
            p for p in result.missing_required
            if not missing_values.get(p)
            or (isinstance(missing_values.get(p), str) and not missing_values[p].strip())
        ]
        if still_missing:
            for p in still_missing:
                st.error(f"'{get_param_label(p)}' is still required.")
        else:
            # Merge extracted + filled
            final = {**result.extracted, **missing_values}
            st.session_state["missing_values"] = missing_values
            st.session_state["final_params"]   = final
            st.session_state["stage"]          = "confirm"
            st.rerun()

    if st.button("← Back"):
        st.session_state["stage"] = "parsed"
        st.rerun()


# ── STAGE: confirm ────────────────────────────────────────────────────────────
elif st.session_state["stage"] == "confirm":
    result      = st.session_state["parse_result"]
    final       = st.session_state["final_params"]

    st.info(f"**Instruction:** {st.session_state.get('ai_instruction_input', '')}", icon="📝")
    st.subheader("Confirm action")

    st.markdown(
        f"The following **{result.action.value.replace('_', ' ').upper()}** "
        f"action will be executed:"
    )

    # Show all final params cleanly
    for k, v in final.items():
        if v and v not in (None, "None", ""):
            st.write(f"- **{k.replace('_', ' ').title()}**: `{v}`")

    st.divider()
    col1, col2, col3 = st.columns([1, 1, 5])

    if col1.button("🚀 Execute", type="primary"):
        st.session_state["stage"] = "done"
        st.rerun()

    if col2.button("← Back"):
        st.session_state["stage"] = "filling" if result.missing_required else "parsed"
        st.rerun()

    if col3.button("✖ Cancel"):
        _reset()


# ── STAGE: done ───────────────────────────────────────────────────────────────
elif st.session_state["stage"] == "done":
    result = st.session_state["parse_result"]
    final  = st.session_state["final_params"]

    st.info(f"**Instruction:** {st.session_state.get('ai_instruction_input', '')}", icon="📝")
    st.subheader("Executing...")

    # Only run once — cache result in session_state
    if "exec_result" not in st.session_state:
        with st.spinner(f"Running {result.action.value.replace('_', ' ')}..."):
            try:
                outcome = asyncio.run(assistant.dispatch(result, final))
                st.session_state["exec_result"] = {"ok": True, "data": outcome}
            except Exception as exc:
                st.session_state["exec_result"] = {"ok": False, "error": str(exc)}

    exec_res = st.session_state["exec_result"]

    if not exec_res["ok"]:
        st.error(f"❌ Unexpected error: {exec_res['error']}")
    else:
        outcome = exec_res["data"]
        status  = outcome.get("status", "")

        if status == "completed":
            st.success("✅ Action completed successfully!")
            st.json(outcome)
        elif status == "escalated":
            st.warning("⏳ Action escalated for admin approval. Check the Approvals page.", icon="⏳")
            st.json(outcome.get("result", {}))
        elif status == "failed":
            st.error(f"❌ Action failed: {outcome.get('error', 'Unknown error')}")
            if outcome.get("result"):
                st.json(outcome.get("result"))
        else:
            st.warning(f"Unexpected status: {status}")
            st.json(outcome)

    st.divider()
    if st.button("🔄 Run another instruction", type="primary"):
        _reset()
