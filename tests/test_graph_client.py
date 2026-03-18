import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shared.graph_client.graph_client import GraphClient
from shared.exceptions import GraphAPIError, ResourceNotFoundError


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    """GraphClient instance with a mocked token."""
    with patch("shared.graph_client.graph_client.get_access_token", return_value="fake-token"):
        yield GraphClient()


def mock_response(status_code: int, json_body: dict | None = None) -> MagicMock:
    """Helper: build a fake httpx.Response."""
    response = MagicMock()
    response.status_code = status_code
    response.content = b'{"id": "abc"}' if json_body is not None else b""
    response.json.return_value = json_body or {}
    response.text = str(json_body)
    response.headers = {}
    return response


# ── User Operations ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_user_success(client):
    fake_user = {"id": "user-123", "displayName": "Jane Doe", "userPrincipalName": "jane@org.com"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response(201, fake_user)
        result = await client.create_user({"displayName": "Jane Doe"})

    assert result["id"] == "user-123"
    assert result["displayName"] == "Jane Doe"


@pytest.mark.asyncio
async def test_create_user_raises_on_error(client):
    error_body = {"error": {"message": "Property userPrincipalName is required."}}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response(400, error_body)
        with pytest.raises(GraphAPIError) as exc_info:
            await client.create_user({"displayName": "Bad User"})

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_disable_user_success(client):
    with patch("httpx.AsyncClient.patch", new_callable=AsyncMock) as mock_patch:
        mock_patch.return_value = mock_response(204)
        await client.disable_user("user-123")   # Should not raise

    mock_patch.assert_called_once()


@pytest.mark.asyncio
async def test_get_user_not_found(client):
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response(404)
        with pytest.raises(ResourceNotFoundError):
            await client.get_user("nonexistent-id")


@pytest.mark.asyncio
async def test_revoke_sessions(client):
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response(200, {"value": True})
        await client.revoke_user_sessions("user-123")   # Should not raise


@pytest.mark.asyncio
async def test_remove_all_licenses(client):
    user_data = {"assignedLicenses": [{"skuId": "sku-111"}, {"skuId": "sku-222"}]}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:

        mock_get.return_value = mock_response(200, user_data)
        mock_post.return_value = mock_response(200, {})
        await client.remove_all_licenses("user-123")

    # Should have called assignLicense with both SKUs in removeLicenses
    call_body = mock_post.call_args.kwargs["json"]
    assert len(call_body["removeLicenses"]) == 2


# ── Group Operations ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_user_to_group(client):
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response(204)
        await client.add_user_to_group("group-456", "user-123")

    assert "@odata.id" in mock_post.call_args.kwargs["json"]


@pytest.mark.asyncio
async def test_remove_user_from_all_groups(client):
    memberships = {
        "value": [
            {"id": "grp-1", "@odata.type": "#microsoft.graph.group"},
            {"id": "grp-2", "@odata.type": "#microsoft.graph.group"},
            {"id": "role-1", "@odata.type": "#microsoft.graph.directoryRole"},  # Should be skipped
        ]
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("httpx.AsyncClient.delete", new_callable=AsyncMock) as mock_delete:

        mock_get.return_value = mock_response(200, memberships)
        mock_delete.return_value = mock_response(204)

        removed = await client.remove_user_from_all_groups("user-123")

    assert removed == ["grp-1", "grp-2"]
    assert mock_delete.call_count == 2  # Directory role not removed


# ── Application Operations ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_application(client):
    fake_app = {"id": "obj-abc", "appId": "client-xyz", "displayName": "My App"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response(201, fake_app)
        result = await client.create_application({"displayName": "My App"})

    assert result["appId"] == "client-xyz"


@pytest.mark.asyncio
async def test_revoke_all_app_assignments(client):
    assignments = {
        "value": [
            {"id": "assign-1", "principalId": "user-1"},
            {"id": "assign-2", "principalId": "user-2"},
        ]
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get, \
         patch("httpx.AsyncClient.delete", new_callable=AsyncMock) as mock_delete:

        mock_get.return_value = mock_response(200, assignments)
        mock_delete.return_value = mock_response(204)

        count = await client.revoke_all_app_assignments("sp-123")

    assert count == 2
    assert mock_delete.call_count == 2


# ── Rate limiting ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limit_raises_graph_api_error(client):
    rate_limited = mock_response(429)
    rate_limited.headers = {"Retry-After": "5"}

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = rate_limited
        with pytest.raises(GraphAPIError) as exc_info:
            await client.get_user("user-123")

    assert exc_info.value.status_code == 429