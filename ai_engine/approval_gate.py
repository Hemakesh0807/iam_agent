import logging

from shared.exceptions import ApprovalRequiredError
from shared.models import FlowName, RiskSeverity

logger = logging.getLogger(__name__)

# Flows that always require human approval before execution
_ALWAYS_REQUIRE_APPROVAL = {
    FlowName.USER_OFFBOARDING,   # Disabling a user is irreversible in the short term
    FlowName.APP_OFFBOARDING,    # Deleting an app registration can break integrations
}

# Flows that auto-execute without approval
_ALWAYS_AUTO_EXECUTE = {
    FlowName.USER_ONBOARDING,    # Low risk — creates access, doesn't remove it
    FlowName.APP_ONBOARDING,     # Policy engine already enforces scope limits
}


class ApprovalGate:
    """
    Decides whether a flow should auto-execute or require human approval.

    Rules:
    - USER_OFFBOARDING  : always requires approval (destructive action)
    - APP_OFFBOARDING   : always requires approval (destructive action)
    - RISK_ISOLATION    : auto-executes if severity is HIGH, escalates otherwise
    - USER_ONBOARDING   : auto-executes (policy engine already validated)
    - APP_ONBOARDING    : auto-executes (policy engine already validated)

    Raises ApprovalRequiredError when human sign-off is needed.
    The orchestrator catches this and routes to the escalation path.

    Usage:
        gate = ApprovalGate()
        gate.evaluate(FlowName.USER_OFFBOARDING, context={})
        # raises ApprovalRequiredError for offboarding flows
    """

    def evaluate(self, flow_name: FlowName, context: dict) -> None:
        """
        Evaluate whether the flow can auto-execute.

        Args:
            flow_name : The flow being evaluated.
            context   : Additional context — e.g. {"severity": RiskSeverity.HIGH}

        Raises:
            ApprovalRequiredError if human approval is needed.
        """

        # Flows that always auto-execute
        if flow_name in _ALWAYS_AUTO_EXECUTE:
            logger.info("Approval gate: auto-execute approved for flow '%s'.", flow_name.value)
            return

        # Flows that always require approval
        if flow_name in _ALWAYS_REQUIRE_APPROVAL:
            raise ApprovalRequiredError(
                flow_name=flow_name.value,
                reason=(
                    f"Flow '{flow_name.value}' is a destructive operation and always "
                    "requires explicit human approval before execution."
                ),
            )

        # Risk isolation — decision depends on severity
        if flow_name == FlowName.RISK_ISOLATION:
            self._evaluate_risk_isolation(context)
            return

        # Fallback — unknown flow, require approval to be safe
        raise ApprovalRequiredError(
            flow_name=flow_name.value,
            reason=f"No approval rule defined for flow '{flow_name.value}'. Escalating by default.",
        )

    def _evaluate_risk_isolation(self, context: dict) -> None:
        """
        Risk isolation approval logic:
        - HIGH severity   -> auto-execute immediately (time-critical)
        - MEDIUM severity -> escalate to admin
        - LOW severity    -> escalate to admin (log only in most cases)
        """
        severity = context.get("severity")

        if severity == RiskSeverity.HIGH:
            logger.info(
                "Approval gate: HIGH severity risk isolation — auto-executing immediately."
            )
            return  # Auto-execute — no approval needed

        if severity == RiskSeverity.MEDIUM:
            raise ApprovalRequiredError(
                flow_name=FlowName.RISK_ISOLATION.value,
                reason=(
                    "MEDIUM severity alert requires admin review before isolating the user. "
                    "The threat is suspicious but not yet confirmed."
                ),
            )

        if severity == RiskSeverity.LOW:
            raise ApprovalRequiredError(
                flow_name=FlowName.RISK_ISOLATION.value,
                reason=(
                    "LOW severity alert does not warrant automatic isolation. "
                    "Escalating to admin for review — action may not be necessary."
                ),
            )

        # Unknown severity — escalate to be safe
        raise ApprovalRequiredError(
            flow_name=FlowName.RISK_ISOLATION.value,
            reason=f"Unknown risk severity '{severity}'. Escalating to admin by default.",
        )