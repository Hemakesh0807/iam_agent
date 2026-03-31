import logging

from ai_engine.orchestrator import IAMOrchestrator
from shared.models import FlowName
from app_bot.flows import run_app_onboarding, run_app_offboarding

logger = logging.getLogger(__name__)

_BOT_REGISTRY = {
    FlowName.APP_ONBOARDING:  run_app_onboarding,
    FlowName.APP_OFFBOARDING: run_app_offboarding,
}

_orchestrator = IAMOrchestrator(bot_registry=_BOT_REGISTRY)


async def handle_request(request_text: str, request_payload: object) -> dict:
    """
    Main entry point for Bot 2 (app bot).

    Called by all triggers (app_request, decom_request).
    Passes the request through the full AI engine pipeline and returns
    the final state.

    Args:
        request_text    : Free-text description of the request.
                          Used by the intent classifier.
        request_payload : Typed Pydantic request model.
                          One of: AppOnboardingRequest, AppOffboardingRequest.

    Returns:
        Final IAMState dict with status, result, or error.
    """
    logger.info("App bot received request: %.80s...", request_text)
    state = await _orchestrator.run(
        request_text=request_text,
        request_payload=request_payload,
    )
    logger.info(
        "App bot completed. status=%s intent=%s",
        state.get("status"), state.get("intent"),
    )
    return state