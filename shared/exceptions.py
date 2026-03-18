class IAMBaseError(Exception):
    """Base class for all IAM automation errors."""


class GraphAPIError(IAMBaseError):
    """Raised when a Microsoft Graph API call fails."""

    def __init__(self, message: str, status_code: int | None = None, endpoint: str | None = None):
        self.status_code = status_code
        self.endpoint = endpoint
        super().__init__(f"Graph API error [{status_code}] on {endpoint}: {message}")


class AuthenticationError(IAMBaseError):
    """Raised when token acquisition fails."""


class PolicyViolationError(IAMBaseError):
    """Raised when a request violates an access policy."""


class DependencyCheckError(IAMBaseError):
    """Raised when an app offboarding dependency check fails (e.g. active users)."""


class ApprovalRequiredError(IAMBaseError):
    """Raised when a flow requires human approval before execution."""

    def __init__(self, flow_name: str, reason: str):
        self.flow_name = flow_name
        self.reason = reason
        super().__init__(f"Human approval required for '{flow_name}': {reason}")


class IntentClassificationError(IAMBaseError):
    """Raised when the AI engine cannot determine request intent."""


class RiskScoringError(IAMBaseError):
    """Raised when risk scoring fails for a security alert."""


class ResourceNotFoundError(IAMBaseError):
    """Raised when a user or application is not found in Entra ID."""

    def __init__(self, resource_type: str, identifier: str):
        super().__init__(f"{resource_type} not found: '{identifier}'")