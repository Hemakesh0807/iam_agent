"""
IAM Assistant — LLM-powered free-text instruction parser and action dispatcher.
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

# Required parameters per action.
# Groups and licenses are intentionally NOT required — they are optional for onboarding.
# usage_location is required — Entra ID blocks license assignment without it.
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
    "license_name":              "License name (e.g. Microsoft 365 E3)",
    "usage_location":            "Usage location country code (e.g. US, IN, GB)",
}


class IAMAssistant:
    """
    Parses a free-text admin instruction into a structured action + parameters,
    then dispatches to the correct bot trigger.
    """

    def __init__(self):
        self._client   = build_openai_client()
        self._model    = config.openai_model
        self._template = _jinja.get_template("assistant_prompt.jinja2")

    async def parse(self, instruction: str) -> AssistantParseResult:
        """
        Parse a free-text IAM instruction.
        Returns AssistantParseResult with action, extracted params, and missing fields.
        The returned extracted dict is a clean copy — no null/None values included.
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
            raw  = response.choices[0].message.content
            data = json.loads(raw)
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

        # Work on a copy so the original LLM response is not mutated
        extracted = copy.deepcopy(data.get("extracted", {}))

        # Normalise user identifier keys
        _normalise_user_identifier(extracted)

        # Clean null/None/empty values — must happen before compute_missing
        _clean_nulls(extracted)

        # Compute what is still missing
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

    async def dispatch(
        self,
        result: AssistantParseResult,
        resolved: dict[str, Any],
    ) -> dict:
        """
        Execute the parsed action with all resolved parameters.
        resolved contains the values the admin provided for missing fields.
        These are merged with result.extracted — resolved values take precedence.
        """
        # Always work on a fresh merge — never mutate result.extracted
        params = {**result.extracted, **resolved}
        action = result.action

        logger.info("Dispatching action: %s params=%s", action.value, list(params.keys()))

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
            return {"status": "failed", "error": f"Unknown action: {action.value}"}
        return await fn(params)


def get_param_label(param: str) -> str:
    return _PARAM_LABELS.get(param, param.replace("_", " ").title())


# ── Internal helpers ──────────────────────────────────────────────────────────

def _normalise_user_identifier(extracted: dict) -> None:
    """
    The LLM may return user_principal_name, user_id, or user_principal_name_or_id.
    Ensure user_principal_name_or_id is always populated if any user key is present.
    """
    if "user_principal_name" in extracted and "user_principal_name_or_id" not in extracted:
        extracted["user_principal_name_or_id"] = extracted["user_principal_name"]
    if "user_id" in extracted and "user_principal_name_or_id" not in extracted:
        extracted["user_principal_name_or_id"] = extracted["user_id"]


def _clean_nulls(extracted: dict) -> None:
    """
    Remove any keys whose value is null, None, the string 'None', or empty string.
    Modifies the dict in-place.
    """
    null_keys = [
        k for k, v in extracted.items()
        if v is None or v == "None" or v == "" or v == "null"
    ]
    for k in null_keys:
        del extracted[k]


def _compute_missing(required: list[str], extracted: dict) -> list[str]:
    """
    Return the list of required fields not present in extracted (after null cleaning).
    """
    return [
        field for field in required
        if not extracted.get(field)
        or (isinstance(extracted[field], str) and not extracted[field].strip())
    ]


async def _resolve_user_id(identifier: str) -> tuple[str, str]:
    """
    Given a UPN or object ID, return (user_id, upn).
    Both values are always populated — required by offboarding and isolation flows.
    """
    from shared.graph_client import GraphClient
    client = GraphClient()
    if "@" in identifier:
        user = await client.get_user_by_upn(identifier)
        return user["id"], identifier
    else:
        user = await client.get_user(identifier)
        return identifier, user.get("userPrincipalName", identifier)


# ── Action dispatchers ────────────────────────────────────────────────────────

async def _dispatch_onboard(params: dict) -> dict:
    """
    Onboard a user. Groups and licenses are optional.
    Resolves group_name -> group_id and license_name -> sku_id if provided.
    """
    from user_bot.triggers.admin_portal import handle_admin_request
    from shared.graph_client import GraphClient
    client = GraphClient()

    upn      = params.get("user_principal_name") or params.get("user_principal_name_or_id", "")
    nickname = params.get("mail_nickname") or (upn.split("@")[0] if "@" in upn else upn)

    # Resolve optional group name to ID
    group_ids  = list(params.get("group_ids", []))
    group_name = params.get("group_name")
    if group_name and not group_ids:
        group = await client.get_group_by_name(group_name)
        if group:
            group_ids = [group["id"]]
        else:
            logger.warning("Group '%s' not found — proceeding without group assignment.", group_name)

    # Resolve optional license name to SKU ID
    sku_id       = params.get("license_sku_id")
    license_name = params.get("license_name")
    if license_name and not sku_id:
        lic = await client.get_license_sku_by_name(license_name)
        if lic:
            sku_id = lic["skuId"]
        else:
            logger.warning("License '%s' not found — proceeding without license.", license_name)

    return await handle_admin_request({
        "action":       "onboard_user",
        "requested_by": "ai_assistant",
        "payload": {
            "display_name":         params.get("display_name", ""),
            "mail_nickname":        nickname,
            "user_principal_name":  upn,
            "department":           params.get("department", ""),
            "job_title":            params.get("job_title", ""),
            "usage_location":       params.get("usage_location", "US"),
            "manager_id":           params.get("manager_id"),
            "group_ids":            group_ids,       # empty list is fine — groups are optional
            "license_sku_id":       sku_id,          # None is fine — license is optional
            "app_role_assignments": [],
        },
    })


async def _dispatch_offboard(params: dict) -> dict:
    """
    Offboard a user. Resolves UPN to object ID so both fields are populated
    (UserOffboardingRequest requires both user_id and user_principal_name).
    """
    from user_bot.triggers.admin_portal import handle_admin_request
    identifier  = params.get("user_principal_name_or_id", "")
    user_id, upn = await _resolve_user_id(identifier)

    return await handle_admin_request({
        "action":       "offboard_user",
        "requested_by": "ai_assistant",
        "payload": {
            "user_id":                  user_id,
            "user_principal_name":      upn,
            "reason":                   params.get("reason", "Requested via AI assistant"),
            "revoke_sessions":          True,
            "remove_licenses":          True,
            "remove_group_memberships": True,
        },
    })


async def _dispatch_isolate(params: dict) -> dict:
    """
    Isolate a user. Resolves UPN to object ID.
    Auto-generates alert_id if not provided (policy engine requires it).
    """
    from user_bot.triggers.admin_portal import handle_admin_request
    identifier   = params.get("user_principal_name_or_id", "")
    user_id, upn = await _resolve_user_id(identifier)
    alert_id     = params.get("alert_id") or f"AI-{str(uuid.uuid4())[:8].upper()}"
    severity     = params.get("severity", "medium")

    return await handle_admin_request({
        "action":       "isolate_user",
        "requested_by": "ai_assistant",
        "payload": {
            "user_id":             user_id,
            "user_principal_name": upn,
            "alert_id":            alert_id,
            "alert_reason":        params.get("alert_reason", ""),
            "severity":            severity,
            "auto_isolate":        severity == "high",
        },
    })


async def _dispatch_add_group(params: dict) -> dict:
    """Add user to a group — resolves both user and group by name."""
    from shared.graph_client import GraphClient
    client     = GraphClient()
    identifier = params.get("user_principal_name_or_id", "")
    group_name = params.get("group_name", "")
    user_id, upn = await _resolve_user_id(identifier)

    group = await client.get_group_by_name(group_name)
    if not group:
        return {"status": "failed", "error": f"Group not found: '{group_name}'"}

    await client.add_user_to_group(group["id"], user_id)
    return {"status": "completed", "user_id": user_id, "upn": upn,
            "group_id": group["id"], "group_name": group_name, "action": "added_to_group"}


async def _dispatch_remove_group(params: dict) -> dict:
    """Remove user from a group or all groups."""
    from shared.graph_client import GraphClient
    client     = GraphClient()
    identifier = params.get("user_principal_name_or_id", "")
    group_name = params.get("group_name", "")
    user_id, upn = await _resolve_user_id(identifier)

    if group_name == "__ALL__":
        removed = await client.remove_user_from_all_groups(user_id)
        return {"status": "completed", "user_id": user_id, "upn": upn, "groups_removed": len(removed)}

    group = await client.get_group_by_name(group_name)
    if not group:
        return {"status": "failed", "error": f"Group not found: '{group_name}'"}

    await client.remove_user_from_group(group["id"], user_id)
    return {"status": "completed", "user_id": user_id, "upn": upn,
            "group_id": group["id"], "group_name": group_name, "action": "removed_from_group"}


async def _dispatch_assign_license(params: dict) -> dict:
    """Assign a license — checks usage_location first."""
    from shared.graph_client import GraphClient
    client       = GraphClient()
    identifier   = params.get("user_principal_name_or_id", "")
    license_name = params.get("license_name", "")
    user_id, upn = await _resolve_user_id(identifier)

    usage_loc = await client.get_usage_location(user_id)
    if not usage_loc:
        return {
            "status": "failed",
            "error": (
                f"User '{upn}' has no usage location set. "
                "Set it via User Management → Manage Licenses tab before assigning a license."
            ),
        }

    lic = await client.get_license_sku_by_name(license_name)
    if not lic:
        return {"status": "failed", "error": f"License not found: '{license_name}'"}

    await client.assign_license(user_id, lic["skuId"])
    return {"status": "completed", "user_id": user_id, "upn": upn,
            "license_name": license_name, "sku_id": lic["skuId"], "action": "license_assigned"}


async def _dispatch_remove_license(params: dict) -> dict:
    """Remove a license from a user."""
    from shared.graph_client import GraphClient
    client       = GraphClient()
    identifier   = params.get("user_principal_name_or_id", "")
    license_name = params.get("license_name", "")
    user_id, upn = await _resolve_user_id(identifier)

    if license_name == "__ALL__":
        await client.remove_all_licenses(user_id)
        return {"status": "completed", "user_id": user_id, "upn": upn, "action": "all_licenses_removed"}

    lic = await client.get_license_sku_by_name(license_name)
    if not lic:
        return {"status": "failed", "error": f"License not found: '{license_name}'"}

    await client.remove_license(user_id, lic["skuId"])
    return {"status": "completed", "user_id": user_id, "upn": upn,
            "license_name": license_name, "sku_id": lic["skuId"], "action": "license_removed"}
