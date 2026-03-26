import json
import logging

from shared.models import UserOffboardingRequest, UserOnboardingRequest
from user_bot.bot import handle_request

logger = logging.getLogger(__name__)


async def handle_hr_event(event_body: dict) -> dict:
    """
    Trigger: HR system webhook.

    Handles two HR event types:
    - hire       -> UserOnboardingRequest  -> Flow A
    - terminate  -> UserOffboardingRequest -> Flow B

    In production this is an Azure Function with an HTTP trigger.
    Locally you can call this directly with a test payload.

    Expected event_body shape for hire:
    {
        "event_type": "hire",
        "display_name": "Jane Doe",
        "mail_nickname": "janedoe",
        "user_principal_name": "janedoe@org.com",
        "department": "Engineering",
        "job_title": "Software Engineer",
        "manager_id": "mgr-object-id",
        "group_ids": ["grp-001", "grp-002"],
        "license_sku_id": "sku-id-here"
    }

    Expected event_body shape for terminate:
    {
        "event_type": "terminate",
        "user_id": "user-object-id",
        "user_principal_name": "janedoe@org.com",
        "reason": "Employee resigned"
    }
    """
    event_type = event_body.get("event_type", "").lower()
    logger.info("HR webhook received event_type: %s", event_type)

    if event_type == "hire":
        request = UserOnboardingRequest(
            display_name=event_body["display_name"],
            mail_nickname=event_body["mail_nickname"],
            user_principal_name=event_body["user_principal_name"],
            department=event_body["department"],
            job_title=event_body["job_title"],
            manager_id=event_body.get("manager_id"),
            group_ids=event_body.get("group_ids", []),
            license_sku_id=event_body.get("license_sku_id"),
            app_role_assignments=event_body.get("app_role_assignments", []),
            requested_by="hr_system",
            source="hr_system",
        )
        request_text = (
            f"Onboard new hire {request.display_name} "
            f"in department {request.department} as {request.job_title}."
        )

    elif event_type == "terminate":
        request = UserOffboardingRequest(
            user_id=event_body["user_id"],
            user_principal_name=event_body["user_principal_name"],
            requested_by="hr_system",
            reason=event_body.get("reason", "Employment terminated via HR system"),
            revoke_sessions=True,
            remove_licenses=True,
            remove_group_memberships=True,
            backup_manager_id=event_body.get("backup_manager_id"),
        )
        request_text = (
            f"Offboard user {request.user_principal_name}. "
            f"Reason: {request.reason}"
        )

    else:
        logger.warning("HR webhook received unknown event_type: %s", event_type)
        return {"error": f"Unknown HR event type: '{event_type}'", "status": "rejected"}

    return await handle_request(request_text=request_text, request_payload=request)