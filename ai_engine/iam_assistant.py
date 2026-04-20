"""
IAM Assistant — LLM-powered free-text instruction parser and action dispatcher.

Parses free-text admin instructions, extracts parameters, identifies what is
missing, and dispatches to the correct bot trigger once all params are resolved.
"""
import json
import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from ai_engine._openai_client import build_openai_client
from shared.config import config
from shared.models import AssistantAction, AssistantParseResult

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_jinja = Environment(loader=FileSystemLoader(_PROMPTS_DIR), autoescape=False)

# Required parameters per action — used to detect missing fields
_REQUIRED_PARAMS: dict[AssistantAction, list[str]] = {
    AssistantAction.ONBOARD_USER:      ["display_name", "user_principal_name", "department", "job_title"],
    AssistantAction.OFFBOARD_USER:     ["user_principal_name_or_id", "reason"],
    AssistantAction.ISOLATE_USER:      ["user_principal_name_or_id", "alert_reason", "severity"],
    AssistantAction.ADD_TO_GROUP:      ["user_principal_name_or_id", "group_name"],
    AssistantAction.REMOVE_FROM_GROUP: ["user_principal_name_or_id", "group_name"],
    AssistantAction.ASSIGN_LICENSE:    ["user_principal_name_or_id", "license_name"],
    AssistantAction.REMOVE_LICENSE:    ["user_principal_name_or_id", "license_name"],
}

# Human-readable labels for prompting missing params
_PARAM_LABELS: dict[str, str] = {
    "display_name":             "User's full display name (e.g. Jane Doe)",
    "user_principal_name":      "User Principal Name (e.g. jane@company.com)",
    "user_principal_name_or_id": "User's UPN (e.g. jane@company.com) or object ID",
    "department":               "Department (e.g. Engineering)",
    "job_title":                "Job title (e.g. Software Engineer)",
    "reason":                   "Reason for this action",
    "alert_reason":             "Reason for the security alert",
    "severity":                 "Alert severity (high / medium / low)",
    "alert_id":                 "Alert ID or incident number",
    "group_name":               "Group display name",
    "license_name":             "License name (e.g. Microsoft 365 E3)",
    "usage_location":           "Usage location country code (e.g. US, IN, GB)",
}


class IAMAssistant:
    """
    Parses a free-text admin instruction into a structured action + parameters.

    Usage:
        assistant = IAMAssistant()
        result = await assistant.parse("Onboard Jane Doe as a Software Engineer in Engineering")
        # result.action = AssistantAction.ONBOARD_USER
        # result.extracted = {"display_name": "Jane Doe", "department": "Engineering", ...}
        # result.missing_required = ["user_principal_name", "job_title"]  (if not mentioned)
    """

    def __init__(self):
        self._client   = build_openai_client()
        self._model    = config.openai_model
        self._template = _jinja.get_template("assistant_prompt.jinja2")

    async def parse(self, instruction: str) -> AssistantParseResult:
        """
        Parse a free-text IAM instruction.

        Args:
            instruction: Natural language admin instruction.

        Returns:
            AssistantParseResult with action, extracted params, and missing fields.
        """
        prompt = self._template.render(instruction=instruction)
        logger.info("AI assistant parsing: %.100s...", instruction)

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            raw    = response.choices[0].message.content
            data   = json.loads(raw)
        except Exception as exc:
            logger.error("AI assistant LLM call failed: %s", exc)
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

        extracted = data.get("extracted", {})

        # Normalise user identifier — LLM may return either key
        _normalise_user_identifier(extracted)

        # Compute missing required fields
        required  = _REQUIRED_PARAMS.get(action, [])
        missing   = _compute_missing(required, extracted)

        result = AssistantParseResult(
            action=action,
            confidence=float(data.get("confidence", 0.0)),
            extracted=extracted,
            missing_required=missing,
            reasoning=data.get("reasoning", ""),
        )

        logger.info(
            "Parsed: action=%s confidence=%.2f missing=%s",
            action.value, result.confidence, missing,
        )
        return result

    async def dispatch(
        self,
        result: AssistantParseResult,
        resolved: dict[str, Any],
    ) -> dict:
        """
        Execute the parsed action with all resolved parameters.

        Args:
            result:   The parse result from parse().
            resolved: Additional params provided by the admin for missing fields.
                      These are merged with result.extracted before dispatch.

        Returns:
            The final IAMState dict from the bot.
        """
        params = {**result.extracted, **resolved}
        action = result.action

        logger.info("Dispatching action: %s params=%s", action.value, list(params.keys()))

        if action == AssistantAction.ONBOARD_USER:
            return await _dispatch_onboard(params)
        elif action == AssistantAction.OFFBOARD_USER:
            return await _dispatch_offboard(params)
        elif action == AssistantAction.ISOLATE_USER:
            return await _dispatch_isolate(params)
        elif action == AssistantAction.ADD_TO_GROUP:
            return await _dispatch_add_group(params)
        elif action == AssistantAction.REMOVE_FROM_GROUP:
            return await _dispatch_remove_group(params)
        elif action == AssistantAction.ASSIGN_LICENSE:
            return await _dispatch_assign_license(params)
        elif action == AssistantAction.REMOVE_LICENSE:
            return await _dispatch_remove_license(params)
        else:
            return {"status": "failed", "error": f"Unknown action: {action.value}"}


def get_param_label(param: str) -> str:
    """Return a human-readable label for a missing parameter field."""
    return _PARAM_LABELS.get(param, param.replace("_", " ").title())


# ── Internal normalisation ────────────────────────────────────────────────────

def _normalise_user_identifier(extracted: dict) -> None:
    """
    The LLM may return 'user_principal_name', 'user_id', or 'user_principal_name_or_id'.
    Normalise to a consistent key so dispatchers can rely on it.
    """
    if "user_principal_name" in extracted and "user_principal_name_or_id" not in extracted:
        extracted["user_principal_name_or_id"] = extracted["user_principal_name"]
    if "user_id" in extracted and "user_principal_name_or_id" not in extracted:
        extracted["user_principal_name_or_id"] = extracted["user_id"]


def _compute_missing(required: list[str], extracted: dict) -> list[str]:
    """Return required fields that are absent or empty in extracted."""
    missing = []
    for field in required:
        val = extracted.get(field)
        if not val or (isinstance(val, str) and not val.strip()):
            missing.append(field)
    return missing


# ── Action dispatchers ────────────────────────────────────────────────────────

async def _dispatch_onboard(params: dict) -> dict:
    from user_bot.triggers.admin_portal import handle_admin_request
    upn      = params.get("user_principal_name") or params.get("user_principal_name_or_id", "")
    nickname = params.get("mail_nickname") or upn.split("@")[0] if "@" in upn else upn

    return await handle_admin_request({
        "action":       "onboard_user",
        "requested_by": "ai_assistant",
        "payload": {
            "display_name":          params.get("display_name", ""),
            "mail_nickname":         nickname,
            "user_principal_name":   upn,
            "department":            params.get("department", ""),
            "job_title":             params.get("job_title", ""),
            "usage_location":        params.get("usage_location", "US"),
            "manager_id":            params.get("manager_id"),
            "group_ids":             params.get("group_ids", []),
            "license_sku_id":        params.get("license_sku_id"),
            "app_role_assignments":  [],
        },
    })


async def _dispatch_offboard(params: dict) -> dict:
    from user_bot.triggers.admin_portal import handle_admin_request
    identifier = params.get("user_principal_name_or_id", "")
    is_upn     = "@" in identifier

    return await handle_admin_request({
        "action":       "offboard_user",
        "requested_by": "ai_assistant",
        "payload": {
            "user_id":             "" if is_upn else identifier,
            "user_principal_name": identifier if is_upn else "",
            "reason":              params.get("reason", "Requested via AI assistant"),
            "revoke_sessions":     True,
            "remove_licenses":     True,
            "remove_group_memberships": True,
        },
    })


async def _dispatch_isolate(params: dict) -> dict:
    from user_bot.triggers.admin_portal import handle_admin_request
    identifier = params.get("user_principal_name_or_id", "")
    is_upn     = "@" in identifier

    return await handle_admin_request({
        "action":       "isolate_user",
        "requested_by": "ai_assistant",
        "payload": {
            "user_id":             "" if is_upn else identifier,
            "user_principal_name": identifier if is_upn else "",
            "alert_id":            params.get("alert_id", "AI-ASSISTANT-ALERT"),
            "alert_reason":        params.get("alert_reason", ""),
            "severity":            params.get("severity", "medium"),
            "auto_isolate":        params.get("severity", "medium") == "high",
        },
    })


async def _dispatch_add_group(params: dict) -> dict:
    """Resolve group name to ID then add user to group directly via Graph API."""
    from shared.graph_client import GraphClient
    client     = GraphClient()
    identifier = params.get("user_principal_name_or_id", "")
    group_name = params.get("group_name", "")

    # Resolve user to object ID
    user   = await client.get_user_by_upn(identifier)
    user_id = user["id"]

    # Resolve group name to ID
    group  = await client.get_group_by_name(group_name)
    if not group:
        return {"status": "failed", "error": f"Group not found: '{group_name}'"}

    await client.add_user_to_group(group["id"], user_id)
    return {
        "status":     "completed",
        "user_id":    user_id,
        "group_id":   group["id"],
        "group_name": group_name,
        "action":     "added_to_group",
    }


async def _dispatch_remove_group(params: dict) -> dict:
    """Resolve group name then remove user from group (or all groups)."""
    from shared.graph_client import GraphClient
    client     = GraphClient()
    identifier = params.get("user_principal_name_or_id", "")
    group_name = params.get("group_name", "")

    user    = await client.get_user_by_upn(identifier)
    user_id = user["id"]

    if group_name == "__ALL__":
        removed = await client.remove_user_from_all_groups(user_id)
        return {"status": "completed", "user_id": user_id, "groups_removed": len(removed)}

    group = await client.get_group_by_name(group_name)
    if not group:
        return {"status": "failed", "error": f"Group not found: '{group_name}'"}

    await client.remove_user_from_group(group["id"], user_id)
    return {
        "status":     "completed",
        "user_id":    user_id,
        "group_id":   group["id"],
        "group_name": group_name,
        "action":     "removed_from_group",
    }


async def _dispatch_assign_license(params: dict) -> dict:
    """Resolve license name to SKU ID then assign to user."""
    from shared.graph_client import GraphClient
    client       = GraphClient()
    identifier   = params.get("user_principal_name_or_id", "")
    license_name = params.get("license_name", "")

    user    = await client.get_user_by_upn(identifier)
    user_id = user["id"]

    license = await client.get_license_sku_by_name(license_name)
    if not license:
        return {"status": "failed", "error": f"License not found: '{license_name}'"}

    await client.assign_license(user_id, license["skuId"])
    return {
        "status":       "completed",
        "user_id":      user_id,
        "license_name": license_name,
        "sku_id":       license["skuId"],
        "action":       "license_assigned",
    }


async def _dispatch_remove_license(params: dict) -> dict:
    """Resolve license name to SKU ID then remove from user."""
    from shared.graph_client import GraphClient
    client       = GraphClient()
    identifier   = params.get("user_principal_name_or_id", "")
    license_name = params.get("license_name", "")

    user    = await client.get_user_by_upn(identifier)
    user_id = user["id"]

    if license_name == "__ALL__":
        await client.remove_all_licenses(user_id)
        return {"status": "completed", "user_id": user_id, "action": "all_licenses_removed"}

    license = await client.get_license_sku_by_name(license_name)
    if not license:
        return {"status": "failed", "error": f"License not found: '{license_name}'"}

    await client.remove_license(user_id, license["skuId"])
    return {
        "status":       "completed",
        "user_id":      user_id,
        "license_name": license_name,
        "sku_id":       license["skuId"],
        "action":       "license_removed",
    }
