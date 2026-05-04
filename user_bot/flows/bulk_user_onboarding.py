"""
Bulk User Onboarding Flow.

Handles sequential creation of multiple users from a validated list
of BulkUserOnboardingRow objects. Tracks per-user sub-operation results.
"""
import logging
import time

from shared.audit.audit_logger import AuditLogger
from shared.exceptions import GraphAPIError
from shared.graph_client import GraphClient
from shared.models import (
    BulkUserResult,
    BulkUserRowStatus,
    BulkUserRunSummary,
    BulkUserSubOp,
    FlowName,
    FlowStatus,
)

logger = logging.getLogger(__name__)


async def run_bulk_user_onboarding(
    rows: list[dict],
    dry_run: bool = False,
) -> BulkUserRunSummary:
    """
    Sequentially onboard multiple users from a list of validated row dicts.

    Each row dict contains:
        display_name, mail_nickname, user_principal_name, department,
        job_title, usage_location, manager_id (optional),
        group_ids (list), license_sku_id (optional)

    Sub-operations per user (tracked individually):
        1. create_account   — POST /users
        2. assign_groups    — POST /groups/{id}/members/$ref (one per group)
        3. assign_license   — POST /users/{id}/assignLicense
        4. assign_manager   — (optional)

    If create_account fails → row is FAILED, sub-ops 2-4 are skipped.
    If create_account succeeds but a sub-op fails → row is PARTIAL.
    All sub-ops succeed → row is COMPLETED.

    Args:
        rows:    List of validated row dicts (from csv_bulk_user_trigger).
        dry_run: If True, validates and simulates without making API calls.

    Returns:
        BulkUserRunSummary with per-row results and aggregate counts.
    """
    client  = GraphClient()
    audit   = AuditLogger()
    results: list[BulkUserResult] = []
    overall_start = time.monotonic()

    logger.info(
        "Bulk user onboarding started: %d user(s) dry_run=%s",
        len(rows), dry_run,
    )

    for i, row in enumerate(rows, start=1):
        upn          = row.get("user_principal_name", f"row-{i}")
        display_name = row.get("display_name", upn)
        row_start    = time.monotonic()
        sub_ops: list[BulkUserSubOp] = []
        user_id: str | None = None

        logger.info("Processing row %d/%d: %s", i, len(rows), upn)

        if dry_run:
            # Dry run — simulate success for all rows
            results.append(BulkUserResult(
                row=i,
                display_name=display_name,
                upn=upn,
                status=BulkUserRowStatus.COMPLETED,
                user_id="dry-run-no-id",
                duration_seconds=0.0,
                sub_ops=[
                    BulkUserSubOp(name="create_account",  success=True, detail="(dry run)"),
                    BulkUserSubOp(name="assign_groups",   success=True, detail=f"{len(row.get('group_ids', []))} group(s) (dry run)"),
                    BulkUserSubOp(name="assign_license",  success=True, detail="(dry run)" if row.get("license_sku_id") else "no license"),
                ],
            ))
            continue

        # ── Step 1: Create user account ───────────────────────────────────────
        try:
            user_payload = _build_user_payload(row)
            user         = await client.create_user(user_payload)
            user_id      = user["id"]
            sub_ops.append(BulkUserSubOp(
                name="create_account", success=True,
                detail=f"objectId={user_id}",
            ))
            logger.info("Row %d: account created user_id=%s", i, user_id)

        except (GraphAPIError, Exception) as exc:
            duration = round(time.monotonic() - row_start, 2)
            sub_ops.append(BulkUserSubOp(
                name="create_account", success=False, detail=str(exc)
            ))
            result = BulkUserResult(
                row=i, display_name=display_name, upn=upn,
                status=BulkUserRowStatus.FAILED,
                duration_seconds=duration,
                sub_ops=sub_ops,
                error=str(exc),
            )
            results.append(result)
            await _audit_row(audit, result)
            logger.error("Row %d: account creation FAILED: %s", i, exc)
            continue   # Move to next row

        # ── Step 2: Assign groups ─────────────────────────────────────────────
        group_ids     = row.get("group_ids", [])
        group_names   = row.get("group_names_resolved", {})   # id -> name map
        groups_failed = 0
        for group_id in group_ids:
            group_name = group_names.get(group_id, group_id)
            try:
                await client.add_user_to_group(group_id, user_id)
                sub_ops.append(BulkUserSubOp(
                    name="assign_group", success=True, detail=group_name
                ))
            except Exception as exc:
                sub_ops.append(BulkUserSubOp(
                    name="assign_group", success=False,
                    detail=f"{group_name}: {exc}",
                ))
                groups_failed += 1
                logger.warning("Row %d: group '%s' assignment failed: %s", i, group_name, exc)

        if group_ids:
            logger.info(
                "Row %d: groups %d/%d assigned.",
                i, len(group_ids) - groups_failed, len(group_ids),
            )

        # ── Step 3: Assign license ────────────────────────────────────────────
        sku_id       = row.get("license_sku_id")
        license_name = row.get("license_name_resolved", sku_id or "")
        if sku_id:
            try:
                await client.assign_license(user_id, sku_id)
                sub_ops.append(BulkUserSubOp(
                    name="assign_license", success=True, detail=license_name
                ))
                logger.info("Row %d: license '%s' assigned.", i, license_name)
            except Exception as exc:
                sub_ops.append(BulkUserSubOp(
                    name="assign_license", success=False,
                    detail=f"{license_name}: {exc}",
                ))
                logger.warning("Row %d: license assignment failed: %s", i, exc)
        else:
            sub_ops.append(BulkUserSubOp(
                name="assign_license", success=True, detail="no license requested"
            ))

        # ── Step 4: Assign manager ────────────────────────────────────────────
        manager_id = row.get("manager_id")
        if manager_id:
            try:
                await client.set_manager(user_id, manager_id)
                sub_ops.append(BulkUserSubOp(
                    name="assign_manager", success=True,
                    detail=row.get("manager_upn", manager_id),
                ))
            except Exception as exc:
                sub_ops.append(BulkUserSubOp(
                    name="assign_manager", success=False, detail=str(exc)
                ))
                logger.warning("Row %d: manager assignment failed: %s", i, exc)

        # ── Determine final row status ────────────────────────────────────────
        failed_ops = [s for s in sub_ops if not s.success]
        if failed_ops:
            row_status = BulkUserRowStatus.PARTIAL
        else:
            row_status = BulkUserRowStatus.COMPLETED

        duration = round(time.monotonic() - row_start, 2)
        result   = BulkUserResult(
            row=i,
            display_name=display_name,
            upn=upn,
            status=row_status,
            user_id=user_id,
            duration_seconds=duration,
            sub_ops=sub_ops,
        )
        results.append(result)
        await _audit_row(audit, result)
        logger.info("Row %d: status=%s duration=%.2fs", i, row_status.value, duration)

    # ── Build summary ─────────────────────────────────────────────────────────
    total_duration = round(time.monotonic() - overall_start, 2)
    summary = BulkUserRunSummary(
        total=len(results),
        completed=sum(1 for r in results if r.status == BulkUserRowStatus.COMPLETED),
        partial=sum(1 for r in results if r.status == BulkUserRowStatus.PARTIAL),
        failed=sum(1 for r in results if r.status == BulkUserRowStatus.FAILED),
        skipped=sum(1 for r in results if r.status == BulkUserRowStatus.SKIPPED),
        total_duration=total_duration,
        results=results,
        dry_run=dry_run,
    )

    logger.info(
        "Bulk user onboarding complete: total=%d completed=%d partial=%d "
        "failed=%d skipped=%d duration=%.2fs dry_run=%s",
        summary.total, summary.completed, summary.partial,
        summary.failed, summary.skipped, summary.total_duration, dry_run,
    )
    return summary


def _build_user_payload(row: dict) -> dict:
    """Build the Graph API POST /users payload from a validated row dict."""
    from user_bot.flows.onboarding import _generate_temp_password
    payload = {
        "accountEnabled":       True,
        "displayName":          row["display_name"],
        "mailNickname":         row["mail_nickname"],
        "userPrincipalName":    row["user_principal_name"],
        "department":           row["department"],
        "jobTitle":             row["job_title"],
        "usageLocation":        row["usage_location"],
        "passwordProfile": {
            "forceChangePasswordNextSignIn": True,
            "password": _generate_temp_password(),
        },
    }
    return payload


async def _audit_row(audit: AuditLogger, result: BulkUserResult) -> None:
    """Write an audit log entry for a single bulk user row."""
    status_map = {
        BulkUserRowStatus.COMPLETED: FlowStatus.COMPLETED,
        BulkUserRowStatus.PARTIAL:   FlowStatus.COMPLETED,   # User was created
        BulkUserRowStatus.FAILED:    FlowStatus.FAILED,
        BulkUserRowStatus.SKIPPED:   FlowStatus.FAILED,
    }
    try:
        await audit.log(
            flow_name=FlowName.USER_ONBOARDING,
            status=status_map.get(result.status, FlowStatus.FAILED),
            principal_id=result.user_id or result.upn,
            requested_by="csv_bulk_users",
            details={
                "display_name": result.display_name,
                "upn":          result.upn,
                "bulk_status":  result.status.value,
                "sub_ops":      [s.model_dump() for s in result.sub_ops],
                "summary":      result.summary_line(),
            },
            error=result.error,
        )
    except Exception as exc:
        logger.error("Failed to write audit for row %d: %s", result.row, exc)

