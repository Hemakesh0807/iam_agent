import logging

from shared.audit.audit_logger import AuditLogger
from shared.exceptions import GraphAPIError, ResourceNotFoundError
from shared.graph_client import GraphClient
from shared.models import FlowName, FlowStatus, UserOnboardingRequest

logger = logging.getLogger(__name__)


async def run_user_onboarding(request: UserOnboardingRequest) -> dict:
    """
    Flow A — User Onboarding.

    Steps:
        1. Create user account in Entra ID
        2. Add user to all specified groups
        3. Assign license (if provided)
        4. Assign app roles (if provided)
        5. Write audit log entry

    Args:
        request: Validated UserOnboardingRequest from the orchestrator.

    Returns:
        dict with created user details and summary of actions taken.

    Raises:
        GraphAPIError: if any Graph API call fails after retries.
    """
    client = GraphClient()
    audit = AuditLogger()
    graph_ops = []

    logger.info("Starting user onboarding for: %s", request.user_principal_name)

    try:
        # ── Step 1: Create user ───────────────────────────────────────────────
        user_payload = {
            "accountEnabled": True,
            "displayName": request.display_name,
            "usageLocation": request.usage_location,
            "mailNickname": request.mail_nickname,
            "userPrincipalName": request.user_principal_name,
            "department": request.department,
            "jobTitle": request.job_title,
            "passwordProfile": {
                "forceChangePasswordNextSignIn": True,
                "password": _generate_temp_password(),
            },
        }
        if request.manager_id:
            user_payload["manager@odata.bind"] = (
                f"https://graph.microsoft.com/v1.0/users/{request.manager_id}"
            )

        user = await client.create_user(user_payload)
        user_id = user["id"]
        graph_ops.append(f"POST /users -> {user_id}")
        logger.info("User created: %s (%s)", request.user_principal_name, user_id)

        # ── Step 2: Add to groups ─────────────────────────────────────────────
        for group_id in request.group_ids:
            await client.add_user_to_group(group_id, user_id)
            graph_ops.append(f"POST /groups/{group_id}/members/$ref")
        logger.info("Added user to %d group(s).", len(request.group_ids))

        # ── Step 3: Assign license ────────────────────────────────────────────
        if request.license_sku_id:
            await client.assign_license(user_id, request.license_sku_id)
            graph_ops.append(f"POST /users/{user_id}/assignLicense")
            logger.info("License assigned: %s", request.license_sku_id)

        # ── Step 4: Assign app roles ──────────────────────────────────────────
        for assignment in request.app_role_assignments:
            await client.assign_app_role(
                service_principal_id=assignment["service_principal_id"],
                user_id=user_id,
                app_role_id=assignment["app_role_id"],
            )
            graph_ops.append(
                f"POST /servicePrincipals/{assignment['service_principal_id']}/appRoleAssignments"
            )
        if request.app_role_assignments:
            logger.info("Assigned %d app role(s).", len(request.app_role_assignments))

        # ── Step 5: Audit log ─────────────────────────────────────────────────
        await audit.log(
            flow_name=FlowName.USER_ONBOARDING,
            status=FlowStatus.COMPLETED,
            principal_id=user_id,
            requested_by=request.requested_by,
            details={
                "display_name": request.display_name,
                "upn": request.user_principal_name,
                "department": request.department,
                "groups_assigned": len(request.group_ids),
                "license_assigned": bool(request.license_sku_id),
                "app_roles_assigned": len(request.app_role_assignments),
                "source": request.source,
            },
            graph_operations=graph_ops,
        )

        result = {
            "user_id": user_id,
            "user_principal_name": request.user_principal_name,
            "display_name": request.display_name,
            "groups_assigned": len(request.group_ids),
            "license_assigned": bool(request.license_sku_id),
            "app_roles_assigned": len(request.app_role_assignments),
            "status": "completed",
        }
        logger.info("User onboarding completed for: %s", request.user_principal_name)
        return result

    except (GraphAPIError, ResourceNotFoundError) as exc:
        logger.error("User onboarding failed for %s: %s", request.user_principal_name, exc)
        await audit.log_failure(
            flow_name=FlowName.USER_ONBOARDING,
            principal_id=request.user_principal_name,
            requested_by=request.requested_by,
            error=exc,
            details={"upn": request.user_principal_name, "graph_ops_completed": graph_ops},
        )
        raise


def _generate_temp_password() -> str:
    """
    Generate a temporary password that meets Entra ID complexity requirements.
    User is forced to change this on first sign-in.
    In production, consider integrating with a secrets manager for this.
    """
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits + "!@#$%"
    return "Tmp!" + "".join(secrets.choice(alphabet) for _ in range(12))
