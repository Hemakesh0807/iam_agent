import logging

from shared.audit.audit_logger import AuditLogger
from shared.exceptions import GraphAPIError, ResourceNotFoundError
from shared.graph_client import GraphClient
from shared.models import FlowName, FlowStatus, UserOffboardingRequest

logger = logging.getLogger(__name__)


async def run_user_offboarding(request: UserOffboardingRequest) -> dict:
    """
    Flow B — User Offboarding.

    Steps:
        1. Verify user exists in Entra ID
        2. Disable the user account
        3. Revoke all active sessions and refresh tokens
        4. Remove from all group memberships
        5. Remove all license assignments
        6. Write audit log entry

    Note: User is soft-deleted (disabled), not hard-deleted.
    Hard deletion is a separate manual step done by an admin after a retention period.

    Args:
        request: Validated UserOffboardingRequest from the orchestrator.

    Returns:
        dict with summary of offboarding actions taken.

    Raises:
        GraphAPIError: if any Graph API call fails after retries.
        ResourceNotFoundError: if the user does not exist in Entra ID.
    """
    client = GraphClient()
    audit = AuditLogger()
    graph_ops = []

    logger.info("Starting user offboarding for: %s", request.user_principal_name)

    try:
        # ── Step 1: Verify user exists ────────────────────────────────────────
        user = await client.get_user(request.user_id)
        graph_ops.append(f"GET /users/{request.user_id}")

        if not user.get("accountEnabled"):
            logger.warning(
                "User %s is already disabled. Proceeding to ensure full cleanup.",
                request.user_principal_name,
            )

        # ── Step 2: Disable account ───────────────────────────────────────────
        await client.disable_user(request.user_id)
        graph_ops.append(f"PATCH /users/{request.user_id} (disable)")
        logger.info("Account disabled: %s", request.user_principal_name)

        # ── Step 3: Revoke all sessions ───────────────────────────────────────
        if request.revoke_sessions:
            await client.revoke_user_sessions(request.user_id)
            graph_ops.append(f"POST /users/{request.user_id}/revokeSignInSessions")
            logger.info("Sessions revoked for: %s", request.user_principal_name)

        # ── Step 4: Remove group memberships ──────────────────────────────────
        removed_groups = []
        if request.remove_group_memberships:
            removed_groups = await client.remove_user_from_all_groups(request.user_id)
            graph_ops.append(
                f"DELETE /groups/*/members/{request.user_id} ({len(removed_groups)} groups)"
            )
            logger.info(
                "Removed from %d group(s): %s",
                len(removed_groups), request.user_principal_name,
            )

        # ── Step 5: Remove licenses ───────────────────────────────────────────
        if request.remove_licenses:
            await client.remove_all_licenses(request.user_id)
            graph_ops.append(f"POST /users/{request.user_id}/assignLicense (remove all)")
            logger.info("Licenses removed for: %s", request.user_principal_name)

        # ── Step 6: Audit log ─────────────────────────────────────────────────
        await audit.log(
            flow_name=FlowName.USER_OFFBOARDING,
            status=FlowStatus.COMPLETED,
            principal_id=request.user_id,
            requested_by=request.requested_by,
            details={
                "upn": request.user_principal_name,
                "reason": request.reason,
                "sessions_revoked": request.revoke_sessions,
                "groups_removed": len(removed_groups),
                "licenses_removed": request.remove_licenses,
            },
            graph_operations=graph_ops,
        )

        result = {
            "user_id": request.user_id,
            "user_principal_name": request.user_principal_name,
            "account_disabled": True,
            "sessions_revoked": request.revoke_sessions,
            "groups_removed": len(removed_groups),
            "licenses_removed": request.remove_licenses,
            "status": "completed",
        }
        logger.info("User offboarding completed for: %s", request.user_principal_name)
        return result

    except ResourceNotFoundError:
        logger.error("User not found in Entra ID: %s", request.user_id)
        await audit.log_failure(
            flow_name=FlowName.USER_OFFBOARDING,
            principal_id=request.user_id,
            requested_by=request.requested_by,
            error=ResourceNotFoundError("User", request.user_id),
            details={"upn": request.user_principal_name},
        )
        raise

    except GraphAPIError as exc:
        logger.error("User offboarding failed for %s: %s", request.user_principal_name, exc)
        await audit.log_failure(
            flow_name=FlowName.USER_OFFBOARDING,
            principal_id=request.user_id,
            requested_by=request.requested_by,
            error=exc,
            details={
                "upn": request.user_principal_name,
                "graph_ops_completed": graph_ops,
            },
        )
        raise