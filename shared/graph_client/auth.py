import logging
import time
from threading import Lock

import msal

from shared.config import config
from shared.exceptions import AuthenticationError

logger = logging.getLogger(__name__)

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
        self._refresh_buffer_seconds = 300

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

    Local dev : AZURE_CLIENT_ID + AZURE_CLIENT_SECRET present -> service principal.
    Azure prod: neither set -> managed identity (no credentials needed).

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
    """
    Route to the correct auth method based on environment.

    - AZURE_CLIENT_ID set  -> local/CI -> service principal credentials flow.
    - AZURE_CLIENT_ID unset -> Azure  -> managed identity (never attempted locally).
    """
    if config.client_id and config.client_secret:
        logger.debug("Auth mode: service principal (local/CI).")
        return _acquire_via_service_principal()

    logger.debug("Auth mode: managed identity (Azure).")
    return _acquire_via_managed_identity()


def _acquire_via_managed_identity() -> tuple[str, int]:
    """
    Acquire token using the Azure managed identity assigned to the Function App.
    Only called when running on Azure -- will fail locally.
    """
    try:
        app = msal.ManagedIdentityApplication(
            managed_identity=msal.SystemAssignedManagedIdentity(),
        )
        result = app.acquire_token_for_client(resource="https://graph.microsoft.com")

        if "access_token" not in result:
            raise AuthenticationError(
                f"Managed identity token acquisition failed: "
                f"{result.get('error_description', 'unknown error')}"
            )

        return result["access_token"], result.get("expires_in", 3600)

    except AuthenticationError:
        raise
    except Exception as exc:
        raise AuthenticationError(
            f"Managed identity authentication failed: {exc}"
        ) from exc


def _acquire_via_service_principal() -> tuple[str, int]:
    """
    Acquire token using client credentials (service principal).
    Used for local development and CI pipelines.
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
                f"Service principal token acquisition failed: "
                f"{result.get('error_description', 'unknown error')}"
            )

        return result["access_token"], result.get("expires_in", 3600)

    except AuthenticationError:
        raise
    except Exception as exc:
        raise AuthenticationError(
            f"Service principal authentication failed: {exc}"
        ) from exc