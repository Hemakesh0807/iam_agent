import logging
import time
from threading import Lock

import msal

from shared.config import config
from shared.exceptions import AuthenticationError

logger = logging.getLogger(__name__)

# Microsoft Graph API scope
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]


class TokenCache:
    """
    Thread-safe in-memory token cache.
    Refreshes automatically 5 minutes before expiry.
    """

    def __init__(self):
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock = Lock()
        self._refresh_buffer_seconds = 300  # Refresh 5 min before expiry

    def get(self) -> str | None:
        with self._lock:
            if self._token and time.time() < (self._expires_at - self._refresh_buffer_seconds):
                return self._token
            return None

    def set(self, token: str, expires_in: int) -> None:
        with self._lock:
            self._token = token
            self._expires_at = time.time() + expires_in


_cache = TokenCache()


def get_access_token() -> str:
    """
    Acquire a Microsoft Graph API access token.

    On Azure (production): uses managed identity — no credentials needed.
    Locally (dev): falls back to service principal client credentials.

    Returns the bearer token string.
    Raises AuthenticationError on failure.
    """
    cached = _cache.get()
    if cached:
        logger.debug("Using cached Graph API token.")
        return cached

    token, expires_in = _acquire_token()
    _cache.set(token, expires_in)
    logger.info("Acquired new Graph API token. Expires in %ds.", expires_in)
    return token


def _acquire_token() -> tuple[str, int]:
    """Internal: acquire token via managed identity or service principal."""

    # ── Managed identity (Azure production) ───────────────────────────────────
    if not config.client_id or not config.client_secret:
        return _acquire_via_managed_identity()

    # ── Service principal (local dev / CI) ────────────────────────────────────
    return _acquire_via_service_principal()


def _acquire_via_managed_identity() -> tuple[str, int]:
    """
    Acquire token using the Azure managed identity assigned to the Function App.
    No credentials — Azure handles this automatically.
    """
    try:
        app = msal.ManagedIdentityApplication(
            managed_identity=msal.SystemAssignedManagedIdentity(),
        )
        result = app.acquire_token_for_client(resource="https://graph.microsoft.com")

        if "access_token" not in result:
            raise AuthenticationError(
                f"Managed identity token acquisition failed: {result.get('error_description', 'unknown error')}"
            )

        return result["access_token"], result.get("expires_in", 3600)

    except AuthenticationError:
        raise
    except Exception as exc:
        raise AuthenticationError(f"Managed identity authentication failed: {exc}") from exc


def _acquire_via_service_principal() -> tuple[str, int]:
    """
    Acquire token using a service principal (client credentials flow).
    Used only for local development and CI pipelines.
    """
    try:
        authority = f"https://login.microsoftonline.com/{config.tenant_id}"
        app = msal.ConfidentialClientApplication(
            client_id=config.client_id,
            client_credential=config.client_secret,
            authority=authority,
        )

        result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)

        if "access_token" not in result:
            raise AuthenticationError(
                f"Service principal token acquisition failed: {result.get('error_description', 'unknown error')}"
            )

        return result["access_token"], result.get("expires_in", 3600)

    except AuthenticationError:
        raise
    except Exception as exc:
        raise AuthenticationError(f"Service principal authentication failed: {exc}") from exc