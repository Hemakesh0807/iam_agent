"""
IAM Assistant — context-aware, memory-enabled IAM instruction parser and dispatcher.
"""
import copy
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from ai_engine._openai_client import build_openai_client
from shared.config import config
from shared.models import AssistantAction, AssistantParseResult

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_jinja = Environment(loader=FileSystemLoader(_PROMPTS_DIR), autoescape=False)

_REQUIRED_PARAMS: dict[AssistantAction, list[str]] = {
    AssistantAction.ONBOARD_USER:      ["display_name", "user_principal_name", "department", "job_title", "usage_location"],
    AssistantAction.OFFBOARD_USER:     ["user_principal_name_or_id", "reason"],
    AssistantAction.ISOLATE_USER:      ["user_principal_name_or_id", "alert_reason", "severity"],
    AssistantAction.ADD_TO_GROUP:      ["user_principal_name_or_id", "group_name"],
    AssistantAction.REMOVE_FROM_GROUP: ["user_principal_name_or_id", "group_name"],
    AssistantAction.ASSIGN_LICENSE:    ["user_principal_name_or_id", "license_name"],
    AssistantAction.REMOVE_LICENSE:    ["user_principal_name_or_id", "license_name"],
}

_PARAM_LABELS: dict[str, str] = {
    "display_name":              "User's full display name (e.g. Jane Doe)",
    "user_principal_name":       "User Principal Name (e.g. jane@company.com)",
    "user_principal_name_or_id": "User's UPN (e.g. jane@company.com) or object ID",
    "department":                "Department (e.g. Engineering)",
    "job_title":                 "Job title (e.g. Software Engineer)",
    "reason":                    "Reason for this action",
    "alert_reason":              "Reason for the security alert",
    "severity":                  "Alert severity (high / medium / low)",
    "alert_id":                  "Alert ID or incident number",
    "group_name":                "Group display name",
    "license_name":              "License name (e.g. Microsoft Power Automate Free)",
    "usage_location":            "Country code for usage location (e.g. US, IN, GB)",
}


class IAMAssistant:
    """
    Context-aware, memory-enabled IAM instruction parser and dispatcher.

    Uses conversation history to resolve pronouns and entity references.
    Validates resolved entities against Entra ID before dispatching.
    Logs every action to the audit log regardless of flow type.
    """

    def __init__(self):
        self._client   = build_openai_client()
        self._model    = config.openai_model
        self._template = _jinja.get_template("assistant_prompt.jinja2")

    async def parse(
        self,
        instruction: str,
        memory=None,
    ) -> AssistantParseResult:
        """
        Parse a free-text IAM instruction with optional conversation context.

        Args:
            instruction: The admin's natural language instruction.
            memory: ConversationMemory instance — if provided, injects context
                    into the prompt so the LLM can resolve references.

        Returns:
            AssistantParseResult with action, extracted params, and missing fields.
        """
        context = memory.build_context_string() if memory and memory.count > 0 else ""
        prompt  = self._template.render(instruction=instruction, context=context)

        logger.info("Parsing (context_turns=%d): %.100s...", memory.count if memory else 0, instruction)

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            raw  = response.choices[0].message.content
            data = json.loads(raw)
        except Exception as exc:
            logger.error("LLM call failed: %s", exc)
            return AssistantParseResult(
                action=AssistantAction.UNKNOWN,
                confidence=0.0,
                missing_required=[],
                reasoning=f"LLM call failed: {exc}",
            )

        action_str = data.get("action", "unknown")
        try:
            action = AssistantAction(action_str)
        except ValueError:
            action = AssistantAction.UNKNOWN

        extracted = copy.deepcopy(data.get("extracted", {}))
        _normalise_user_identifier(extracted)
        _clean_nulls(extracted)

        required = _REQUIRED_PARAMS.get(action, [])
        missing  = _compute_missing(required, extracted)

        result = AssistantParseResult(
            action=action,
            confidence=float(data.get("confidence", 0.0)),
            extracted=extracted,
            missing_required=missing,
            reasoning=data.get("reasoning", ""),
        )

        logger.info(
            "Parsed: action=%s confidence=%.2f extracted=%s missing=%s",
            action.value, result.confidence, list(extracted.keys()), missing,
        )
        return result

    async def resolve_entities(
        self,
        action: str,
        extracted: dict[str, Any],
        memory=None,
    ) -> tuple[dict[str, Any], list[str]]:
        """
        Resolve entity references (pronouns, partial names) using memory + Entra ID.

        Returns:
            (resolved_params, warnings) where warnings are clarification messages
            shown to the admin before execution.
        """
        if memory is None:
            return extracted, []

        from ai_engine.entity_resolver import EntityResolver
        resolver = EntityResolver(memory)
        return await resolver.resolve(action, extracted)

    async def dispatch(
        self,
        result: AssistantParseResult,
        resolved: dict[str, Any],
        memory=None,
    ) -> dict:
        """
        Execute the parsed action with all resolved parameters.
        Writes to audit log for every action regardless of type.

        Args:
            result:   The parse result from parse().
            resolved: Admin-provided values for missing fields.
            memory:   ConversationMemory — used to record the turn after execution.

        Returns:
            Outcome dict with status, result data, and resolved_entities.
        """
        params = {**result.extracted, **resolved}
        action = result.action

        dispatchers = {
            AssistantAction.ONBOARD_USER:      _dispatch_onboard,
            AssistantAction.OFFBOARD_USER:     _dispatch_offboard,
            AssistantAction.ISOLATE_USER:      _dispatch_isolate,
            AssistantAction.ADD_TO_GROUP:      _dispatch_add_group,
            AssistantAction.REMOVE_FROM_GROUP: _dispatch_remove_group,
            AssistantAction.ASSIGN_LICENSE:    _dispatch_assign_license,
            AssistantAction.REMOVE_LICENSE:    _dispatch_remove_license,
        }

        fn = dispatchers.get(action)
        if not fn:
            return {"status": "failed", "error": f"Unknown action: {action.value}", "resolved_entities": {}}

        logger.info("Dispatching: %s params=%s", action.value, list(params.keys()))
        outcome = await fn(params)

        # Record turn in memory regardless of outcome
        if memory is not None:
            resolved_entities = outcome.get("resolved_entities", {})
            memory.add_turn(
                instruction=resolved.get("_instruction", ""),
                action=action.value,
                extracted=result.extracted,
                resolved_entities=resolved_entities,
                outcome={k: v for k, v in outcome.items() if k != "resolved_entities"},
                status=outcome.get("status", "unknown"),
            )

        return outcome


def get_param_label(param: str) -> str:
    return _PARAM_LABELS.get(param, param.replace("_", " ").title())


# ── Internal helpers ──────────────────────────────────────────────────────────

def _normalise_user_identifier(extracted: dict) -> None:
    if "user_principal_name" in extracted and "user_principal_name_or_id" not in extracted:
        extracted["user_principal_name_or_id"] = extracted["user_principal_name"]
    if "user_id" in extracted and "user_principal_name_or_id" not in extracted:
        extracted["user_principal_name_or_id"] = extracted["user_id"]


def _clean_nulls(extracted: dict) -> None:
    null_keys = [k for k, v in extracted.items()
                 if v is None or v == "None" or v == "" or v == "null"]
    for k in null_keys:
        del extracted[k]


def _compute_missing(required: list[str], extracted: dict) -> list[str]:
    return [
        f for f in required
        if not extracted.get(f) or (isinstance(extracted[f], str) and not extracted[f].strip())
    ]


async def _resolve_user_id(identifier: str) -> tuple[str, str]:
    """Given UPN or object ID, return (user_id, upn). Always populates both."""
    from shared.graph_client import GraphClient
    client = GraphClient()
    if "@" in identifier:
        user = await client.get_user_by_upn(identifier)
        return user["id"], user.get("userPrincipalName", identifier)
    else:
        user = await client.get_user(identifier)
        return identifier, user.get("userPrincipalName", identifier)


async def _write_assistant_audit(
    action: str,
    principal_id: str,
    status: str,
    details: dict,
    error: str | None = None,
) -> None:
    """
    Write an audit log entry for any AI assistant action.
    This ensures group assignments, license changes etc. are all audited
    even when they bypass the bot flows.
    """
    try:
        from shared.audit.audit_logger import AuditLogger
        from shared.models import FlowName, FlowStatus

        # Map assistant actions to the closest FlowName for audit categorisation
        _action_to_flow = {
            "onboard_user":      FlowName.USER_ONBOARDING,
            "offboard_user":     FlowName.USER_OFFBOARDING,
            "isolate_user":      FlowName.RISK_ISOLATION,
            "add_to_group":      FlowName.USER_ONBOARDING,
            "remove_from_group": FlowName.USER_OFFBOARDING,
            "assign_license":    FlowName.USER_ONBOARDING,
            "remove_license":    FlowName.USER_OFFBOARDING,
            "app_onboarding":    FlowName.APP_ONBOARDING,
            "app_offboarding":   FlowName.APP_OFFBOARDING,
        }

        _status_map = {
            "completed": FlowStatus.COMPLETED,
            "failed":    FlowStatus.FAILED,
            "escalated": FlowStatus.ESCALATED,
        }

        flow_name   = _action_to_flow.get(action, FlowName.USER_ONBOARDING)
        flow_status = _status_map.get(status, FlowStatus.FAILED)

        audit = AuditLogger()
        await audit.log(
            flow_name=flow_name,
            status=flow_status,
            principal_id=principal_id or "unknown",
            requested_by="ai_assistant",
            details={"assistant_action": action, **details},
            error=error,
        )
    except Exception as exc:
        logger.error("Failed to write assistant audit log: %s", exc)


# ── Action dispatchers ────────────────────────────────────────────────────────

async def _dispatch_onboard(params: dict) -> dict:
    from user_bot.triggers.admin_portal import handle_admin_request
    from shared.graph_client import GraphClient
    client = GraphClient()

    upn      = params.get("user_principal_name") or params.get("user_principal_name_or_id", "")
    nickname = params.get("mail_nickname") or (upn.split("@")[0] if "@" in upn else upn)

    group_ids    = list(params.get("group_ids", []))
    group_name   = params.get("group_name")
    if group_name and not group_ids:
        group = await client.get_group_by_name(group_name)
        if group:
            group_ids = [group["id"]]
        else:
            logger.warning("Group '%s' not found — proceeding without.", group_name)

    sku_id       = params.get("license_sku_id")
    license_name = params.get("license_name")
    if license_name and not sku_id:
        lic = await client.get_license_sku_by_name(license_name)
        if lic:
            sku_id = lic["skuId"]

    state = await handle_admin_request({
        "action": "onboard_user", "requested_by": "ai_assistant",
        "payload": {
            "display_name":         params.get("display_name", ""),
            "mail_nickname":        nickname,
            "user_principal_name":  upn,
            "department":           params.get("department", ""),
            "job_title":            params.get("job_title", ""),
            "usage_location":       params.get("usage_location", "US"),
            "manager_id":           params.get("manager_id"),
            "group_ids":            group_ids,
            "license_sku_id":       sku_id,
            "app_role_assignments": [],
        },
    })

    result_data = state.get("result") or {}
    user_id = result_data.get("user_id") or upn
    resolved_entities = {"user_id": user_id, "user_principal_name": upn,
                         "display_name": params.get("display_name", "")}

    await _write_assistant_audit("onboard_user", user_id, state.get("status", "failed"),
                                 {"upn": upn, "department": params.get("department")},
                                 state.get("error"))
    return {**state, "resolved_entities": resolved_entities}


async def _dispatch_offboard(params: dict) -> dict:
    from user_bot.triggers.admin_portal import handle_admin_request
    identifier   = params.get("user_principal_name_or_id", "")
    user_id, upn = await _resolve_user_id(identifier)
    resolved_entities = {"user_id": user_id, "user_principal_name": upn}

    state = await handle_admin_request({
        "action": "offboard_user", "requested_by": "ai_assistant",
        "payload": {
            "user_id": user_id, "user_principal_name": upn,
            "reason": params.get("reason", "Requested via AI assistant"),
            "revoke_sessions": True, "remove_licenses": True, "remove_group_memberships": True,
        },
    })
    await _write_assistant_audit("offboard_user", user_id, state.get("status", "failed"),
                                 {"upn": upn}, state.get("error"))
    return {**state, "resolved_entities": resolved_entities}


async def _dispatch_isolate(params: dict) -> dict:
    from user_bot.triggers.admin_portal import handle_admin_request
    identifier   = params.get("user_principal_name_or_id", "")
    user_id, upn = await _resolve_user_id(identifier)
    alert_id     = params.get("alert_id") or f"AI-{str(uuid.uuid4())[:8].upper()}"
    severity     = params.get("severity", "medium")
    resolved_entities = {"user_id": user_id, "user_principal_name": upn}

    state = await handle_admin_request({
        "action": "isolate_user", "requested_by": "ai_assistant",
        "payload": {
            "user_id": user_id, "user_principal_name": upn,
            "alert_id": alert_id, "alert_reason": params.get("alert_reason", ""),
            "severity": severity, "auto_isolate": severity == "high",
        },
    })
    await _write_assistant_audit("isolate_user", user_id, state.get("status", "failed"),
                                 {"upn": upn, "severity": severity}, state.get("error"))
    return {**state, "resolved_entities": resolved_entities}


async def _dispatch_add_group(params: dict) -> dict:
    from shared.graph_client import GraphClient
    client     = GraphClient()
    identifier = params.get("user_principal_name_or_id", "")
    group_name = params.get("group_name", "")
    user_id, upn = await _resolve_user_id(identifier)
    resolved_entities = {"user_id": user_id, "user_principal_name": upn}

    group = await client.get_group_by_name(group_name)
    if not group:
        await _write_assistant_audit("add_to_group", user_id, "failed",
                                     {"upn": upn, "group_name": group_name},
                                     f"Group not found: '{group_name}'")
        return {"status": "failed", "error": f"Group not found: '{group_name}'",
                "resolved_entities": resolved_entities}

    await client.add_user_to_group(group["id"], user_id)
    await _write_assistant_audit("add_to_group", user_id, "completed",
                                 {"upn": upn, "group_name": group_name, "group_id": group["id"]})
    return {"status": "completed", "user_id": user_id, "upn": upn,
            "group_id": group["id"], "group_name": group_name, "action": "added_to_group",
            "resolved_entities": resolved_entities}


async def _dispatch_remove_group(params: dict) -> dict:
    from shared.graph_client import GraphClient
    client     = GraphClient()
    identifier = params.get("user_principal_name_or_id", "")
    group_name = params.get("group_name", "")
    user_id, upn = await _resolve_user_id(identifier)
    resolved_entities = {"user_id": user_id, "user_principal_name": upn}

    if group_name == "__ALL__":
        removed = await client.remove_user_from_all_groups(user_id)
        await _write_assistant_audit("remove_from_group", user_id, "completed",
                                     {"upn": upn, "groups_removed": len(removed), "all": True})
        return {"status": "completed", "user_id": user_id, "upn": upn,
                "groups_removed": len(removed), "resolved_entities": resolved_entities}

    group = await client.get_group_by_name(group_name)
    if not group:
        await _write_assistant_audit("remove_from_group", user_id, "failed",
                                     {"upn": upn, "group_name": group_name},
                                     f"Group not found: '{group_name}'")
        return {"status": "failed", "error": f"Group not found: '{group_name}'",
                "resolved_entities": resolved_entities}

    await client.remove_user_from_group(group["id"], user_id)
    await _write_assistant_audit("remove_from_group", user_id, "completed",
                                 {"upn": upn, "group_name": group_name, "group_id": group["id"]})
    return {"status": "completed", "user_id": user_id, "upn": upn,
            "group_id": group["id"], "group_name": group_name, "action": "removed_from_group",
            "resolved_entities": resolved_entities}


async def _dispatch_assign_license(params: dict) -> dict:
    from shared.graph_client import GraphClient
    client       = GraphClient()
    identifier   = params.get("user_principal_name_or_id", "")
    license_name = params.get("license_name", "")
    user_id, upn = await _resolve_user_id(identifier)
    resolved_entities = {"user_id": user_id, "user_principal_name": upn}

    usage_loc = await client.get_usage_location(user_id)
    if not usage_loc:
        err = (f"User '{upn}' has no usage location set. "
               "Set it via User Management → Manage Licenses tab.")
        await _write_assistant_audit("assign_license", user_id, "failed",
                                     {"upn": upn, "license_name": license_name}, err)
        return {"status": "failed", "error": err, "resolved_entities": resolved_entities}

    lic = await client.get_license_sku_by_name(license_name)
    if not lic:
        err = f"License not found: '{license_name}'"
        await _write_assistant_audit("assign_license", user_id, "failed",
                                     {"upn": upn, "license_name": license_name}, err)
        return {"status": "failed", "error": err, "resolved_entities": resolved_entities}

    await client.assign_license(user_id, lic["skuId"])
    await _write_assistant_audit("assign_license", user_id, "completed",
                                 {"upn": upn, "license_name": license_name, "sku_id": lic["skuId"]})
    return {"status": "completed", "user_id": user_id, "upn": upn,
            "license_name": license_name, "sku_id": lic["skuId"], "action": "license_assigned",
            "resolved_entities": resolved_entities}


async def _dispatch_remove_license(params: dict) -> dict:
    from shared.graph_client import GraphClient
    client       = GraphClient()
    identifier   = params.get("user_principal_name_or_id", "")
    license_name = params.get("license_name", "")
    user_id, upn = await _resolve_user_id(identifier)
    resolved_entities = {"user_id": user_id, "user_principal_name": upn}

    if license_name == "__ALL__":
        await client.remove_all_licenses(user_id)
        await _write_assistant_audit("remove_license", user_id, "completed",
                                     {"upn": upn, "all": True})
        return {"status": "completed", "user_id": user_id, "upn": upn,
                "action": "all_licenses_removed", "resolved_entities": resolved_entities}

    lic = await client.get_license_sku_by_name(license_name)
    if not lic:
        err = f"License not found: '{license_name}'"
        await _write_assistant_audit("remove_license", user_id, "failed",
                                     {"upn": upn, "license_name": license_name}, err)
        return {"status": "failed", "error": err, "resolved_entities": resolved_entities}

    await client.remove_license(user_id, lic["skuId"])
    await _write_assistant_audit("remove_license", user_id, "completed",
                                 {"upn": upn, "license_name": license_name, "sku_id": lic["skuId"]})
    return {"status": "completed", "user_id": user_id, "upn": upn,
            "license_name": license_name, "sku_id": lic["skuId"], "action": "license_removed",
            "resolved_entities": resolved_entities}

