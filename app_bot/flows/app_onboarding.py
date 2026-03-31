import logging

from shared.audit.audit_logger import AuditLogger
from shared.exceptions import GraphAPIError
from shared.graph_client import GraphClient
from shared.models import AppOnboardingRequest, FlowName, FlowStatus

logger = logging.getLogger(__name__)


async def run_app_onboarding(request: AppOnboardingRequest) -> dict:
    """
    Flow D — Application Onboarding.

    Steps:
        1. Register the application in Entra ID
        2. Create a service principal (enterprise app) for the registration
        3. Add the owner to the application
        4. Add approved API permission scopes
        5. Write audit log entry

    Args:
        request: Validated AppOnboardingRequest from the orchestrator.

    Returns:
        dict with registered app details (appId, objectId, servicePrincipalId).

    Raises:
        GraphAPIError: if any Graph API call fails after retries.
    """
    client = GraphClient()
    audit = AuditLogger()
    graph_ops = []

    logger.info("Starting app onboarding for: %s", request.display_name)

    try:
        # ── Step 1: Register the application ──────────────────────────────────
        app_payload = {
            "displayName": request.display_name,
            "description": request.description or "",
            "signInAudience": "AzureADMyOrg",   # Single tenant only
            "web": {
                "redirectUris": request.redirect_uris,
            },
        }

        app = await client.create_application(app_payload)
        object_id = app["id"]
        app_id = app["appId"]
        graph_ops.append(f"POST /applications -> objectId={object_id} appId={app_id}")
        logger.info(
            "Application registered: %s (appId=%s objectId=%s)",
            request.display_name, app_id, object_id,
        )

        # ── Step 2: Create service principal ──────────────────────────────────
        sp = await client.create_service_principal(app_id)
        sp_id = sp["id"]
        graph_ops.append(f"POST /servicePrincipals -> {sp_id}")
        logger.info("Service principal created: %s", sp_id)

        # ── Step 3: Add owner ─────────────────────────────────────────────────
        await client.add_application_owner(object_id, request.owner_id)
        graph_ops.append(f"POST /applications/{object_id}/owners/$ref")
        logger.info("Owner %s added to application.", request.owner_id)

        # ── Step 4: Add API permission scopes ─────────────────────────────────
        # Scopes are stored on the app registration as required resource access.
        # Admin consent is a separate manual step in the portal.
        if request.requested_scopes:
            scope_payload = _build_scope_payload(request.requested_scopes)
            await client._patch(f"applications/{object_id}", scope_payload)
            graph_ops.append(
                f"PATCH /applications/{object_id} (requiredResourceAccess)"
            )
            logger.info(
                "Added %d scope(s) to application.", len(request.requested_scopes)
            )

        # ── Step 5: Audit log ─────────────────────────────────────────────────
        await audit.log(
            flow_name=FlowName.APP_ONBOARDING,
            status=FlowStatus.COMPLETED,
            principal_id=object_id,
            requested_by=request.requested_by,
            details={
                "display_name": request.display_name,
                "app_id": app_id,
                "object_id": object_id,
                "service_principal_id": sp_id,
                "owner_id": request.owner_id,
                "scopes_requested": request.requested_scopes,
                "redirect_uris": request.redirect_uris,
            },
            graph_operations=graph_ops,
        )

        result = {
            "display_name": request.display_name,
            "app_id": app_id,
            "object_id": object_id,
            "service_principal_id": sp_id,
            "scopes_registered": request.requested_scopes,
            "status": "completed",
        }
        logger.info("App onboarding completed for: %s (appId=%s)", request.display_name, app_id)
        return result

    except GraphAPIError as exc:
        logger.error("App onboarding failed for %s: %s", request.display_name, exc)
        await audit.log_failure(
            flow_name=FlowName.APP_ONBOARDING,
            principal_id=request.display_name,
            requested_by=request.requested_by,
            error=exc,
            details={
                "display_name": request.display_name,
                "graph_ops_completed": graph_ops,
            },
        )
        raise


def _build_scope_payload(scopes: list[str]) -> dict:
    """
    Build the requiredResourceAccess payload for Microsoft Graph delegated scopes.
    Maps scope name strings to the Graph API's permission format.

    In a full implementation this would look up scope GUIDs from the
    Microsoft Graph service principal. For now we use the well-known
    Graph API resource ID and store scopes by name for admin to consent.
    """
    GRAPH_API_RESOURCE_ID = "00000003-0000-0000-c000-000000000000"

    return {
        "requiredResourceAccess": [
            {
                "resourceAppId": GRAPH_API_RESOURCE_ID,
                "resourceAccess": [
                    {
                        "id": scope,   # In production: resolve to actual GUID
                        "type": "Scope",
                    }
                    for scope in scopes
                ],
            }
        ]
    }