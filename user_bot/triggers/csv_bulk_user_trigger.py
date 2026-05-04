"""
CSV Bulk User Onboarding Trigger.

Handles:
1. CSV parsing and field extraction
2. Pre-flight validation (required fields, UPN format, duplicates, Entra ID lookup)
3. Name resolution (group names -> IDs, license names -> SKU IDs, manager UPN -> ID)
4. Dispatch to bulk_user_onboarding flow
"""
import csv
import io
import logging
from pathlib import Path

from shared.models import BulkUserPreflightIssue, BulkUserRunSummary

logger = logging.getLogger(__name__)

_REQUIRED_COLS = {
    "display_name", "user_principal_name",
    "department", "job_title", "usage_location",
}

_OPTIONAL_COLS = {
    "mail_nickname", "manager_upn", "group_names", "license_name",
}


def _safe(value) -> str:
    """
    Safely convert any CSV cell value to a stripped string.
    Handles None, empty string, and whitespace-only values.
    This is the single source of truth for all cell access — avoids
    AttributeError: 'NoneType' object has no attribute 'strip'.
    """
    if value is None:
        return ""
    return str(value).strip()


def parse_csv(content: str) -> list[dict]:
    """
    Parse CSV content into a list of cleaned row dicts.
    All cell values are passed through _safe() so downstream code
    never sees None values.
    """
    reader = csv.DictReader(io.StringIO(content))
    rows   = []
    for row in reader:
        # _safe() on both key and value — headers should never be None
        # but values frequently are for optional/empty columns
        cleaned = {_safe(k): _safe(v) for k, v in row.items()}
        rows.append(cleaned)
    return rows


async def preflight_validate(
    rows: list[dict],
) -> tuple[list[BulkUserPreflightIssue], list[str]]:
    """
    Run all pre-flight checks on the parsed CSV rows.

    Checks:
    1. Required columns present in CSV header
    2. Required fields non-empty per row
    3. UPN format valid (has @)
    4. Usage location is resolvable to a 2-letter code
    5. Duplicate UPNs within the CSV
    6. UPNs that already exist in Entra ID (conflict prediction)
    7. Group names exist in Entra ID (warning, not error)
    8. License name is resolvable (warning)

    Returns:
        (issues, missing_columns)
    """
    from shared.graph_client import GraphClient
    from ai_engine.entity_resolver import normalise_usage_location
    client = GraphClient()
    issues: list[BulkUserPreflightIssue] = []

    if not rows:
        return [], []

    # ── Check 1: Required columns in header ──────────────────────────────────
    headers         = set(rows[0].keys())
    missing_columns = sorted(_REQUIRED_COLS - headers)
    if missing_columns:
        return [], missing_columns

    # ── Check 2-5: Per-row validation ─────────────────────────────────────────
    seen_upns: set[str] = set()

    for i, row in enumerate(rows, start=1):
        upn = _safe(row.get("user_principal_name"))

        # Required fields must be non-empty
        for col in _REQUIRED_COLS:
            if not _safe(row.get(col)):
                issues.append(BulkUserPreflightIssue(
                    row=i, upn=upn or f"row-{i}", severity="error",
                    message=f"Required field '{col}' is missing or empty.",
                ))

        # UPN format
        if upn and "@" not in upn:
            issues.append(BulkUserPreflightIssue(
                row=i, upn=upn, severity="error",
                message=f"Invalid UPN format: '{upn}' — must contain '@'.",
            ))

        # Usage location
        loc = _safe(row.get("usage_location"))
        if loc:
            normalised = normalise_usage_location(loc)
            if len(normalised) != 2:
                issues.append(BulkUserPreflightIssue(
                    row=i, upn=upn or f"row-{i}", severity="error",
                    message=f"Cannot resolve usage_location '{loc}' to a 2-letter country code.",
                ))

        # Duplicate UPN within CSV
        if upn:
            if upn.lower() in seen_upns:
                issues.append(BulkUserPreflightIssue(
                    row=i, upn=upn, severity="error",
                    message=f"Duplicate UPN '{upn}' — already appears in a previous row.",
                ))
            seen_upns.add(upn.lower())

    # ── Check 6: UPNs already in Entra ID ────────────────────────────────────
    for i, row in enumerate(rows, start=1):
        upn = _safe(row.get("user_principal_name"))
        if not upn or "@" not in upn:
            continue
        try:
            await client.get_user_by_upn(upn)
            # If no exception → user already exists → conflict
            issues.append(BulkUserPreflightIssue(
                row=i, upn=upn, severity="error",
                message=f"User '{upn}' already exists in Entra ID — will conflict on creation.",
            ))
        except Exception:
            pass   # 404 = does not exist = good

    # ── Check 7: Group names exist ────────────────────────────────────────────
    group_name_cache: dict[str, bool] = {}
    for i, row in enumerate(rows, start=1):
        upn         = _safe(row.get("user_principal_name")) or f"row-{i}"
        group_names = _safe(row.get("group_names"))
        if not group_names:
            continue
        for name in group_names.split("|"):
            name = _safe(name)
            if not name:
                continue
            if name not in group_name_cache:
                try:
                    g = await client.get_group_by_name(name)
                    group_name_cache[name] = g is not None
                except Exception:
                    group_name_cache[name] = False
            if not group_name_cache[name]:
                issues.append(BulkUserPreflightIssue(
                    row=i, upn=upn, severity="warning",
                    message=f"Group '{name}' not found in Entra ID — will be skipped.",
                ))

    # ── Check 8: License name resolvable ─────────────────────────────────────
    license_cache: dict[str, bool] = {}
    for i, row in enumerate(rows, start=1):
        upn          = _safe(row.get("user_principal_name")) or f"row-{i}"
        license_name = _safe(row.get("license_name"))
        if not license_name:
            continue
        if license_name not in license_cache:
            try:
                lic = await client.get_license_sku_by_name(license_name)
                license_cache[license_name] = lic is not None
            except Exception:
                license_cache[license_name] = False
        if not license_cache[license_name]:
            issues.append(BulkUserPreflightIssue(
                row=i, upn=upn, severity="warning",
                message=f"License '{license_name}' not found — will be skipped.",
            ))

    return issues, missing_columns


async def resolve_and_prepare(rows: list[dict]) -> list[dict]:
    """
    Resolve all names to IDs once before the run (not per-row).
    Returns enriched row dicts ready for bulk_user_onboarding.

    Resolves:
    - group_names   -> group_ids + group_names_resolved (id->name map)
    - license_name  -> license_sku_id + license_name_resolved
    - manager_upn   -> manager_id
    - usage_location -> normalised 2-letter code
    - mail_nickname  -> derived from UPN prefix if blank
    """
    from shared.graph_client import GraphClient
    from ai_engine.entity_resolver import normalise_usage_location
    client = GraphClient()

    # Collect all unique group names across all rows
    unique_group_names: set[str] = set()
    for row in rows:
        for name in _safe(row.get("group_names")).split("|"):
            name = _safe(name)
            if name:
                unique_group_names.add(name)

    # Resolve groups once
    group_name_to_id: dict[str, str] = {}
    for name in unique_group_names:
        try:
            g = await client.get_group_by_name(name)
            if g:
                group_name_to_id[name] = g["id"]
                logger.info("Group resolved: '%s' -> %s", name, g["id"])
        except Exception as exc:
            logger.warning("Could not resolve group '%s': %s", name, exc)

    # Collect all unique license names
    unique_licenses: set[str] = set()
    for row in rows:
        ln = _safe(row.get("license_name"))
        if ln:
            unique_licenses.add(ln)

    # Resolve licenses once
    license_name_to_sku: dict[str, str] = {}
    for name in unique_licenses:
        try:
            lic = await client.get_license_sku_by_name(name)
            if lic:
                license_name_to_sku[name] = lic["skuId"]
                logger.info("License resolved: '%s' -> %s", name, lic["skuId"])
        except Exception as exc:
            logger.warning("Could not resolve license '%s': %s", name, exc)

    # Collect all unique manager UPNs
    unique_managers: set[str] = set()
    for row in rows:
        m = _safe(row.get("manager_upn"))
        if m:
            unique_managers.add(m)

    # Resolve managers once
    manager_upn_to_id: dict[str, str] = {}
    for upn in unique_managers:
        try:
            u = await client.get_user_by_upn(upn)
            manager_upn_to_id[upn] = u["id"]
            logger.info("Manager resolved: '%s' -> %s", upn, u["id"])
        except Exception as exc:
            logger.warning("Could not resolve manager '%s': %s", upn, exc)

    # Build enriched row dicts
    prepared: list[dict] = []
    for row in rows:
        upn      = _safe(row.get("user_principal_name"))
        nickname = _safe(row.get("mail_nickname")) or (
            upn.split("@")[0] if "@" in upn else upn
        )

        # Resolve groups for this row
        raw_groups = _safe(row.get("group_names"))
        group_ids:  list[str] = []
        id_to_name: dict[str, str] = {}
        for name in raw_groups.split("|"):
            name = _safe(name)
            if name and name in group_name_to_id:
                gid = group_name_to_id[name]
                group_ids.append(gid)
                id_to_name[gid] = name

        # Resolve license for this row
        license_name = _safe(row.get("license_name"))
        sku_id       = license_name_to_sku.get(license_name) if license_name else None

        # Resolve manager for this row
        manager_upn = _safe(row.get("manager_upn"))
        manager_id  = manager_upn_to_id.get(manager_upn) if manager_upn else None

        # Normalise usage_location
        usage_loc_raw  = _safe(row.get("usage_location")) or "US"
        usage_location = normalise_usage_location(usage_loc_raw)

        prepared.append({
            "display_name":          _safe(row.get("display_name")),
            "mail_nickname":         nickname,
            "user_principal_name":   upn,
            "department":            _safe(row.get("department")),
            "job_title":             _safe(row.get("job_title")),
            "usage_location":        usage_location,
            "manager_id":            manager_id,
            "manager_upn":           manager_upn,
            "group_ids":             group_ids,
            "group_names_resolved":  id_to_name,
            "license_sku_id":        sku_id,
            "license_name_resolved": license_name or "",
        })

    return prepared

