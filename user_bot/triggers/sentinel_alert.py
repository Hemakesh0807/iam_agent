import logging

from shared.models import RiskIsolationRequest, RiskSeverity
from user_bot.bot import handle_request

logger = logging.getLogger(__name__)


async def handle_sentinel_alert(alert_body: dict) -> dict:
    """
    Trigger: Microsoft Sentinel security alert.

    Receives a Sentinel alert payload and routes it to
    the risk isolation flow (Flow C) via the user bot.

    In production this is an Azure Function triggered by a
    Sentinel Logic App playbook via HTTP.
    Locally you can call this directly with a test payload.

    Expected alert_body shape:
    {
        "alert_id": "alert-uuid",
        "user_id": "entra-object-id",
        "user_principal_name": "jane@org.com",
        "severity": "high",
        "alert_reason": "Impossible travel detected",
        "sentinel_incident_id": "INC-4821"    (optional)
    }
    """
    logger.warning(
        "Sentinel alert received: alert_id=%s user=%s severity=%s",
        alert_body.get("alert_id"),
        alert_body.get("user_principal_name"),
        alert_body.get("severity"),
    )

    # Validate severity — default to HIGH if unrecognised (fail safe)
    severity_raw = alert_body.get("severity", "high").lower()
    try:
        severity = RiskSeverity(severity_raw)
    except ValueError:
        logger.warning(
            "Unrecognised severity '%s' in Sentinel alert. Defaulting to HIGH.", severity_raw
        )
        severity = RiskSeverity.HIGH

    request = RiskIsolationRequest(
        user_id=alert_body["user_id"],
        user_principal_name=alert_body["user_principal_name"],
        alert_id=alert_body["alert_id"],
        severity=severity,
        alert_reason=alert_body.get("alert_reason", "Security alert from Microsoft Sentinel"),
        sentinel_incident_id=alert_body.get("sentinel_incident_id"),
        auto_isolate=(severity == RiskSeverity.HIGH),
    )

    request_text = (
        f"Security alert: isolate user {request.user_principal_name}. "
        f"Severity: {severity.value}. Reason: {request.alert_reason}"
    )

    return await handle_request(request_text=request_text, request_payload=request)