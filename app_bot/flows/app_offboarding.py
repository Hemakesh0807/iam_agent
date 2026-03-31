import logging

from shared.audit.audit_logger import AuditLogger
from shared.exceptions import DependencyCheckError, GraphAPIError, ResourceNotFoundError
from shared.graph_client import GraphClient
from shared.models import AppOffboardingRequest, FlowName, FlowStatus

logger = logging.getLogger(__name__)

# Maximum number of active user assignments allowed before blocking offboarding.
# If an app has more active users than this, the flow raises DependencyCheckError
# and requires manual review before proceeding.
_MAX_ACTIVE_ASSIGNMENTS_THRESHOLD = 0


async def run_app_offboarding(request: AppOffboardingRequest) -> dict:
    """
    Flow E — Application Offboarding.

    Steps:
        1. Verify the application exists in Entra ID
        2. Dependency check — ensure no active user assignments remain
        3. Revoke all user app role assignments
        4. Delete the service principal (enterprise app)
        5. Delete the application registration
        6. Write audit log entry

    The dependency check (Step 2) is the critical safety gate unique to app offboarding.
    If active users are still assigned, the flow halts and raises DependencyCheckError
    rather than silently breaking those users' access.

    Args:
        request: Validated AppOffboardingRequest from the orchestrator.

    Returns:
        dict with decommission summary.

    Raises:
        DependencyCheckError: if active user assignments exist and block offboarding.
        GraphAPIError: if any Graph API call fails after retries.
        ResourceNotFoundError: if the application does not exist.
    """
    client = GraphClient()
    audit = AuditLogger()
    graph_ops = []

    logger.info(
        "Starting app offboarding for app_id=%s object_id=%s",
        request.app_id, request.object_id,
    )

    try:
        # ── Step 1: Verify application exists ─────────────────────────────────
        app = await client.get_application(request.object_id)
        graph_ops.append(f"GET /applications/{request.object_id}")
        display_name = app.get("displayName", request.app_id)
        logger.info("Application found: %s (appId=%s)", display_name, request.app_id)

        # ── Step 2: Dependency check ──────────────────────────────────────────
        assignments = await client.get_app_role_assignments(request.service_principal_id)
        graph_ops.append(
            f"GET /servicePrincipals/{request.service_principal_id}/appRoleAssignedTo"
        )
        active_count = len(assignments)

        if active_count > _MAX_ACTIVE_ASSIGNMENTS_THRESHOLD:
            assigned_users = [a.get("principalDisplayName", a.get("principalId")) for a in assignments]
            logger.warning(
                "App offboarding blocked: %d active user(s) still assigned to %s: %s",
                active_count, display_name, assigned_users,
            )
            raise DependencyCheckError(
                f"Cannot offboard '{display_name}' — {active_count} user(s) still assigned: "
                f"{assigned_users}. Revoke all user assignments first or set "
                f"revoke_user_assignments=True in the request."
            )

        logger.info("Dependency check passed — no active user assignments.")

        # ── Step 3: Revoke all user assignments ───────────────────────────────
        revoked_count = 0
        if request.revoke_user_assignments and assignments:
            revoked_count = await client.revoke_all_app_assignments(
                request.service_principal_id
            )
            graph_ops.append(
                f"DELETE /servicePrincipals/{request.service_principal_id}"
                f"/appRoleAssignedTo/* ({revoked_count} revoked)"
            )
            logger.info(
                "Revoked %d user assignment(s) from %s.", revoked_count, display_name
            )

        # ── Step 4: Delete service principal ──────────────────────────────────
        await client.delete_service_principal(request.service_principal_id)
        graph_ops.append(f"DELETE /servicePrincipals/{request.service_principal_id}")
        logger.info("Service principal deleted: %s", request.service_principal_id)

        # ── Step 5: Delete application registration ───────────────────────────
        await client.delete_application(request.object_id)
        graph_ops.append(f"DELETE /applications/{request.object_id}")
        logger.info(
            "Application registration deleted: %s (objectId=%s)",
            display_name, request.object_id,
        )

        # ── Step 6: Audit log ─────────────────────────────────────────────────
        await audit.log(
            flow_name=FlowName.APP_OFFBOARDING,
            status=FlowStatus.COMPLETED,
            principal_id=request.object_id,
            requested_by=request.requested_by,
            details={
                "display_name": display_name,
                "app_id": request.app_id,
                "object_id": request.object_id,
                "service_principal_id": request.service_principal_id,
                "reason": request.reason,
                "assignments_revoked": revoked_count,
            },
            graph_operations=graph_ops,
        )

        result = {
            "display_name": display_name,
            "app_id": request.app_id,
            "object_id": request.object_id,
            "service_principal_deleted": True,
            "application_deleted": True,
            "assignments_revoked": revoked_count,
            "status": "completed",
        }
        logger.info(
            "App offboarding completed for: %s (appId=%s)", display_name, request.app_id
        )
        return result

    except DependencyCheckError:
        await audit.log_failure(
            flow_name=FlowName.APP_OFFBOARDING,
            principal_id=request.object_id,
            requested_by=request.requested_by,
            error=DependencyCheckError("Active user assignments block offboarding."),
            details={
                "app_id": request.app_id,
                "reason": "dependency_check_failed",
            },
        )
        raise

    except ResourceNotFoundError:
        logger.error("Application not found: %s", request.object_id)
        await audit.log_failure(
            flow_name=FlowName.APP_OFFBOARDING,
            principal_id=request.object_id,
            requested_by=request.requested_by,
            error=ResourceNotFoundError("Application", request.object_id),
            details={"app_id": request.app_id},
        )
        raise

    except GraphAPIError as exc:
        logger.error(
            "App offboarding failed for app_id=%s: %s", request.app_id, exc
        )
        await audit.log_failure(
            flow_name=FlowName.APP_OFFBOARDING,
            principal_id=request.object_id,
            requested_by=request.requested_by,
            error=exc,
            details={
                "app_id": request.app_id,
                "graph_ops_completed": graph_ops,
            },
        )
        raise