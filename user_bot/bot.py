import logging

from ai_engine.orchestrator import IAMOrchestrator
from shared.models import FlowName
from user_bot.flows import (
    run_user_offboarding,
    run_user_onboarding,
    run_risk_isolation,
)

logger = logging.getLogger(__name__)

# Registry maps each FlowName to its async flow function.
# The orchestrator calls the correct one after classification + policy + approval.
_BOT_REGISTRY = {
    FlowName.USER_ONBOARDING:  run_user_onboarding,
    FlowName.USER_OFFBOARDING: run_user_offboarding,
    FlowName.RISK_ISOLATION:   run_risk_isolation,
}

# Single orchestrator instance reused across all invocations
_orchestrator = IAMOrchestrator(bot_registry=_BOT_REGISTRY)


async def handle_request(request_text: str, request_payload: object) -> dict:
    """
    Main entry point for Bot 1 (user bot).

    Called by all triggers (hr_webhook, sentinel_alert, admin_portal).
    Passes the request through the full AI engine pipeline and returns
    the final state.

    Args:
        request_text    : Free-text description of the request.
                          Used by the intent classifier.
        request_payload : Typed Pydantic request model.
                          One of: UserOnboardingRequest, UserOffboardingRequest,
                          RiskIsolationRequest.

    Returns:
        Final IAMState dict with status, result, or error.
    """
    logger.info("User bot received request: %.80s...", request_text)
    state = await _orchestrator.run(
        request_text=request_text,
        request_payload=request_payload,
    )
    logger.info(
        "User bot completed. status=%s intent=%s",
        state.get("status"), state.get("intent"),
    )
    return state