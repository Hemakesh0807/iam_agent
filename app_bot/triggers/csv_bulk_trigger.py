import asyncio
import csv
import logging
import time
from pathlib import Path

from shared.models import AppType, BulkAppOnboardingResult, SAMLAppOnboardingRequest
from app_bot.flows.saml_app_onboarding import run_saml_app_onboarding

logger = logging.getLogger(__name__)


async def handle_csv_bulk_onboarding(csv_path: str) -> list[BulkAppOnboardingResult]:
    """
    Trigger: Bulk SAML app onboarding from a CSV file.

    Reads each row from the CSV, validates it, runs the SAML onboarding
    flow for each app, and returns a full result list with per-app duration.

    Failure handling:
        - Process all apps regardless of individual failures
        - Continue even if some rows fail
        - Collect and return all results (passed + failed) at the end
        - Each failure is fully logged and audit-logged

    Expected CSV columns:
        display_name, app_type, template_id, entity_id, reply_url,
        sign_on_url, owner_id, assigned_group_ids

    Args:
        csv_path: Path to the CSV file.

    Returns:
        List of BulkAppOnboardingResult — one per CSV row.
    """
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    rows = _read_csv(path)
    if not rows:
        logger.warning("CSV file is empty or has no valid rows: %s", csv_path)
        return []

    logger.info(
        "Bulk SAML onboarding started: %d app(s) from '%s'",
        len(rows), csv_path,
    )

    results: list[BulkAppOnboardingResult] = []

    for i, row in enumerate(rows, start=1):
        display_name = row.get("display_name", f"Row {i}")
        logger.info("Processing row %d/%d: '%s'", i, len(rows), display_name)

        start_time = time.monotonic()

        try:
            request = _build_request(row, i)
            outcome = await run_saml_app_onboarding(request)
            duration = round(time.monotonic() - start_time, 2)

            results.append(BulkAppOnboardingResult(
                row=i,
                display_name=display_name,
                app_type=row.get("app_type", ""),
                status="completed",
                app_id=outcome.get("app_id"),
                object_id=outcome.get("object_id"),
                sp_id=outcome.get("service_principal_id"),
                cert_thumbprint=outcome.get("cert_thumbprint"),
                duration_seconds=duration,
            ))
            logger.info(
                "Row %d completed: '%s' in %.2fs", i, display_name, duration
            )

        except Exception as exc:
            duration = round(time.monotonic() - start_time, 2)
            logger.error(
                "Row %d failed: '%s' after %.2fs — %s",
                i, display_name, duration, exc,
            )
            results.append(BulkAppOnboardingResult(
                row=i,
                display_name=display_name,
                app_type=row.get("app_type", ""),
                status="failed",
                duration_seconds=duration,
                error=str(exc),
            ))
            # Continue to next row — do not stop bulk run on failure

    _log_summary(results)
    return results


# ── CSV reading ───────────────────────────────────────────────────────────────

def _read_csv(path: Path) -> list[dict]:
    """Read and return all rows from the CSV as a list of dicts."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = [row for row in reader]

    # Strip whitespace from all values
    cleaned = []
    for row in rows:
        cleaned.append({k.strip(): v.strip() for k, v in row.items()})

    return cleaned


# ── Request builder ───────────────────────────────────────────────────────────

def _build_request(row: dict, row_num: int) -> SAMLAppOnboardingRequest:
    """
    Build a SAMLAppOnboardingRequest from a CSV row dict.
    Raises ValueError for missing required fields.
    """
    _require_field(row, "display_name", row_num)
    _require_field(row, "app_type",     row_num)
    _require_field(row, "entity_id",    row_num)
    _require_field(row, "reply_url",    row_num)
    _require_field(row, "owner_id",     row_num)

    app_type_raw = row["app_type"].lower().strip()
    if app_type_raw not in ("gallery", "non_gallery"):
        raise ValueError(
            f"Row {row_num}: app_type must be 'gallery' or 'non_gallery', "
            f"got '{app_type_raw}'"
        )
    app_type = AppType(app_type_raw)

    if app_type == AppType.GALLERY and not row.get("template_id"):
        raise ValueError(
            f"Row {row_num}: template_id is required for gallery apps "
            f"(display_name='{row['display_name']}')"
        )

    # Parse pipe-separated group IDs — e.g. "grp-001|grp-002"
    group_ids_raw = row.get("assigned_group_ids", "").strip()
    group_ids = (
        [g.strip() for g in group_ids_raw.split("|") if g.strip()]
        if group_ids_raw else []
    )

    return SAMLAppOnboardingRequest(
        display_name=row["display_name"],
        app_type=app_type,
        template_id=row.get("template_id") or None,
        entity_id=row["entity_id"],
        reply_url=row["reply_url"],
        sign_on_url=row.get("sign_on_url") or None,
        owner_id=row["owner_id"],
        assigned_group_ids=group_ids,
        requested_by=row.get("requested_by", "csv_bulk"),
        source="csv_bulk",
    )


def _require_field(row: dict, field: str, row_num: int) -> None:
    if not row.get(field, "").strip():
        raise ValueError(
            f"Row {row_num}: required field '{field}' is missing or empty."
        )


# ── Summary logger ────────────────────────────────────────────────────────────

def _log_summary(results: list[BulkAppOnboardingResult]) -> None:
    total    = len(results)
    passed   = sum(1 for r in results if r.status == "completed")
    failed   = sum(1 for r in results if r.status == "failed")
    total_dur = round(sum(r.duration_seconds for r in results), 2)

    logger.info(
        "Bulk SAML onboarding complete: total=%d passed=%d failed=%d "
        "total_duration=%.2fs",
        total, passed, failed, total_dur,
    )
    if failed:
        for r in results:
            if r.status == "failed":
                logger.error(
                    "  FAILED row=%d name='%s' error=%s",
                    r.row, r.display_name, r.error,
                )