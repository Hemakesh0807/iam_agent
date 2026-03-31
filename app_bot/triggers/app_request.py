import logging

from shared.models import AppOnboardingRequest
from app_bot.bot import handle_request

logger = logging.getLogger(__name__)


async def handle_app_request(request_body: dict) -> dict:
    """
    Trigger: New application registration request.

    Receives a request from an admin portal, developer self-service form,
    or internal API and routes it to Flow D (app onboarding).

    In production this is an Azure Function with an HTTP trigger
    behind Azure API Management.

    Expected request_body shape:
    {
        "display_name": "My Internal App",
        "owner_id": "user-object-id",
        "requested_scopes": ["User.Read", "Mail.Read"],
        "redirect_uris": ["https://myapp.org/auth/callback"],
        "description": "Internal tool for the finance team",
        "requested_by": "dev@org.com"
    }
    """
    logger.info(
        "App registration request received for: %s by %s",
        request_body.get("display_name"),
        request_body.get("requested_by"),
    )

    request = AppOnboardingRequest(
        display_name=request_body["display_name"],
        owner_id=request_body["owner_id"],
        requested_scopes=request_body.get("requested_scopes", []),
        redirect_uris=request_body.get("redirect_uris", []),
        description=request_body.get("description"),
        requested_by=request_body.get("requested_by", "admin_portal"),
    )

    request_text = (
        f"Register new application '{request.display_name}' "
        f"with scopes: {', '.join(request.requested_scopes)}."
    )

    return await handle_request(request_text=request_text, request_payload=request)