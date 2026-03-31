import logging

from shared.models import AppOffboardingRequest
from app_bot.bot import handle_request

logger = logging.getLogger(__name__)


async def handle_decom_request(request_body: dict) -> dict:
    """
    Trigger: Application decommission request.

    Receives a decommission request from an admin and routes it
    to Flow E (app offboarding).

    In production this is an Azure Function with an HTTP trigger.

    Expected request_body shape:
    {
        "app_id": "client-id-of-app",
        "object_id": "object-id-of-app-registration",
        "service_principal_id": "object-id-of-service-principal",
        "requested_by": "admin@org.com",
        "reason": "App retired — replaced by new platform",
        "revoke_user_assignments": true
    }
    """
    logger.info(
        "App decommission request received for app_id=%s by %s",
        request_body.get("app_id"),
        request_body.get("requested_by"),
    )

    request = AppOffboardingRequest(
        app_id=request_body["app_id"],
        object_id=request_body["object_id"],
        service_principal_id=request_body["service_principal_id"],
        requested_by=request_body.get("requested_by", "admin_portal"),
        reason=request_body.get("reason", "Application decommissioned"),
        revoke_user_assignments=request_body.get("revoke_user_assignments", True),
    )

    request_text = (
        f"Decommission application with app_id '{request.app_id}'. "
        f"Reason: {request.reason}"
    )

    return await handle_request(request_text=request_text, request_payload=request)