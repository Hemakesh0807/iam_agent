import logging

from shared.exceptions import PolicyViolationError
from shared.models import (
    AppOffboardingRequest,
    AppOnboardingRequest,
    FlowName,
    RiskIsolationRequest,
    RiskSeverity,
    UserOffboardingRequest,
    UserOnboardingRequest,
)

logger = logging.getLogger(__name__)

# Scopes that are never allowed on any application registration
_FORBIDDEN_SCOPES = {
    "Directory.ReadWrite.All",
    "RoleManagement.ReadWrite.Directory",
    "User.ReadWrite.All",
}

# Departments that require extra approval before onboarding
_HIGH_PRIVILEGE_DEPARTMENTS = {"security", "it", "infrastructure", "devops"}

# Risk levels that trigger auto-isolation without human approval
_AUTO_ISOLATE_SEVERITIES = {RiskSeverity.HIGH}


class PolicyEngine:
    """
    Enforces IAM access policies before any Graph API action is executed.

    Rules are checked per flow type. A PolicyViolationError is raised
    if the request violates any rule — the orchestrator catches this
    and routes to the approval gate or rejects the request entirely.

    Usage:
        policy = PolicyEngine()
        policy.check(FlowName.USER_ONBOARDING, request)
    """

    def check(self, flow_name: FlowName, request: object) -> None:
        """
        Run all policy checks for the given flow.
        Raises PolicyViolationError if any check fails.
        """
        checker = {
            FlowName.USER_ONBOARDING:  self._check_user_onboarding,
            FlowName.USER_OFFBOARDING: self._check_user_offboarding,
            FlowName.RISK_ISOLATION:   self._check_risk_isolation,
            FlowName.APP_ONBOARDING:   self._check_app_onboarding,
            FlowName.APP_OFFBOARDING:  self._check_app_offboarding,
        }.get(flow_name)

        if not checker:
            raise PolicyViolationError(f"No policy defined for flow: {flow_name}")

        logger.info("Running policy checks for flow: %s", flow_name.value)
        checker(request)
        logger.info("Policy checks passed for flow: %s", flow_name.value)

    # ── User Onboarding ───────────────────────────────────────────────────────

    def _check_user_onboarding(self, request: UserOnboardingRequest) -> None:

        # UPN must match the org domain
        if "@" not in request.user_principal_name:
            raise PolicyViolationError(
                f"Invalid userPrincipalName format: '{request.user_principal_name}'. "
                "Must include an @ domain."
            )

        # High-privilege departments require explicit manager approval
        dept = request.department.lower()
        if dept in _HIGH_PRIVILEGE_DEPARTMENTS and not request.manager_id:
            raise PolicyViolationError(
                f"Onboarding into department '{request.department}' requires a manager_id "
                "for approval. This is a high-privilege department."
            )

        # Must have at least one group assignment
        if not request.group_ids:
            raise PolicyViolationError(
                "User onboarding request must include at least one group_id. "
                "A user with no group memberships cannot access any resources."
            )

        logger.debug("User onboarding policy passed for: %s", request.user_principal_name)

    # ── User Offboarding ──────────────────────────────────────────────────────

    def _check_user_offboarding(self, request: UserOffboardingRequest) -> None:

        # Reason must be provided
        if not request.reason or len(request.reason.strip()) < 5:
            raise PolicyViolationError(
                "User offboarding requires a reason of at least 5 characters. "
                "This is required for audit compliance."
            )

        # Cannot offboard without disabling — that would leave sessions active
        if not request.revoke_sessions:
            raise PolicyViolationError(
                "revoke_sessions cannot be False for offboarding. "
                "Active sessions must always be revoked when disabling a user."
            )

        logger.debug("User offboarding policy passed for: %s", request.user_principal_name)

    # ── Risk Isolation ────────────────────────────────────────────────────────

    def _check_risk_isolation(self, request: RiskIsolationRequest) -> None:

        # An alert ID is mandatory for traceability
        if not request.alert_id:
            raise PolicyViolationError(
                "Risk isolation requires an alert_id for audit traceability."
            )

        # High severity alerts must always auto-isolate — no exceptions
        if request.severity == RiskSeverity.HIGH and not request.auto_isolate:
            raise PolicyViolationError(
                "HIGH severity alerts must always auto-isolate. "
                "Manual escalation is not permitted for confirmed high-risk events."
            )

        logger.debug(
            "Risk isolation policy passed for: %s (severity=%s)",
            request.user_principal_name, request.severity.value,
        )

    # ── App Onboarding ────────────────────────────────────────────────────────

    def _check_app_onboarding(self, request: AppOnboardingRequest) -> None:

        # Check for forbidden scopes (least privilege enforcement)
        requested = set(request.requested_scopes)
        forbidden = requested & _FORBIDDEN_SCOPES
        if forbidden:
            raise PolicyViolationError(
                f"Application requested forbidden scopes: {sorted(forbidden)}. "
                "These scopes grant excessive directory-level permissions and are not allowed."
            )

        # App must have an owner
        if not request.owner_id:
            raise PolicyViolationError(
                "App onboarding requires an owner_id. "
                "Every registered application must have an accountable owner."
            )

        # At least one scope must be requested
        if not request.requested_scopes:
            raise PolicyViolationError(
                "App onboarding request must include at least one requested scope."
            )

        logger.debug("App onboarding policy passed for: %s", request.display_name)

    # ── App Offboarding ───────────────────────────────────────────────────────

    def _check_app_offboarding(self, request: AppOffboardingRequest) -> None:

        # Reason must be provided
        if not request.reason or len(request.reason.strip()) < 5:
            raise PolicyViolationError(
                "App offboarding requires a reason of at least 5 characters."
            )

        # Both app object ID and service principal ID must be present
        if not request.object_id or not request.service_principal_id:
            raise PolicyViolationError(
                "App offboarding requires both object_id and service_principal_id. "
                "Both must be provided to fully decommission the application."
            )

        logger.debug("App offboarding policy passed for app_id: %s", request.app_id)