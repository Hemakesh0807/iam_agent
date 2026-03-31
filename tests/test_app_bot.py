import pytest
from unittest.mock import AsyncMock, patch

from shared.models import AppOnboardingRequest, AppOffboardingRequest, FlowStatus
from shared.exceptions import GraphAPIError, ResourceNotFoundError, DependencyCheckError
from app_bot.flows.app_onboarding import run_app_onboarding
from app_bot.flows.app_offboarding import run_app_offboarding


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def onboarding_req():
    return AppOnboardingRequest(
        display_name="Finance Portal",
        owner_id="user-owner-001",
        requested_scopes=["User.Read", "Mail.Read"],
        redirect_uris=["https://financeportal.org/callback"],
        description="Internal finance team app",
        requested_by="dev@org.com",
    )

@pytest.fixture
def offboarding_req():
    return AppOffboardingRequest(
        app_id="app-abc-123",
        object_id="obj-abc-123",
        service_principal_id="sp-abc-123",
        requested_by="admin@org.com",
        reason="App retired and replaced",
        revoke_user_assignments=True,
    )

@pytest.fixture
def mock_audit():
    with patch("app_bot.flows.app_onboarding.AuditLogger") as A1, \
         patch("app_bot.flows.app_offboarding.AuditLogger") as A2:
        for A in [A1, A2]:
            instance = AsyncMock()
            A.return_value = instance
            instance.log = AsyncMock()
            instance.log_failure = AsyncMock()
        yield


# ── Flow D: App Onboarding ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_app_onboarding_success(onboarding_req, mock_audit):
    with patch("app_bot.flows.app_onboarding.GraphClient") as MockG:
        instance = AsyncMock()
        MockG.return_value = instance
        instance.create_application = AsyncMock(
            return_value={"id": "obj-new", "appId": "app-new"}
        )
        instance.create_service_principal = AsyncMock(
            return_value={"id": "sp-new"}
        )
        instance.add_application_owner = AsyncMock()
        instance._patch = AsyncMock()

        result = await run_app_onboarding(onboarding_req)

    assert result["status"] == "completed"
    assert result["app_id"] == "app-new"
    assert result["object_id"] == "obj-new"
    assert result["service_principal_id"] == "sp-new"
    assert result["scopes_registered"] == ["User.Read", "Mail.Read"]


@pytest.mark.asyncio
async def test_app_onboarding_no_scopes(mock_audit):
    req = AppOnboardingRequest(
        display_name="Simple App",
        owner_id="user-001",
        requested_scopes=["User.Read"],
        requested_by="dev@org.com",
    )
    with patch("app_bot.flows.app_onboarding.GraphClient") as MockG:
        instance = AsyncMock()
        MockG.return_value = instance
        instance.create_application = AsyncMock(
            return_value={"id": "obj-x", "appId": "app-x"}
        )
        instance.create_service_principal = AsyncMock(return_value={"id": "sp-x"})
        instance.add_application_owner = AsyncMock()
        instance._patch = AsyncMock()

        result = await run_app_onboarding(req)

    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_app_onboarding_graph_failure_logs_and_raises(onboarding_req, mock_audit):
    with patch("app_bot.flows.app_onboarding.GraphClient") as MockG:
        instance = AsyncMock()
        MockG.return_value = instance
        instance.create_application = AsyncMock(
            side_effect=GraphAPIError("Conflict", 409)
        )

        with pytest.raises(GraphAPIError):
            await run_app_onboarding(onboarding_req)


# ── Flow E: App Offboarding ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_app_offboarding_success_no_assignments(offboarding_req, mock_audit):
    with patch("app_bot.flows.app_offboarding.GraphClient") as MockG:
        instance = AsyncMock()
        MockG.return_value = instance
        instance.get_application = AsyncMock(
            return_value={"id": "obj-abc-123", "displayName": "Finance Portal"}
        )
        instance.get_app_role_assignments = AsyncMock(return_value=[])  # No active users
        instance.revoke_all_app_assignments = AsyncMock(return_value=0)
        instance.delete_service_principal = AsyncMock()
        instance.delete_application = AsyncMock()

        result = await run_app_offboarding(offboarding_req)

    assert result["status"] == "completed"
    assert result["service_principal_deleted"] is True
    assert result["application_deleted"] is True
    assert result["assignments_revoked"] == 0


@pytest.mark.asyncio
async def test_app_offboarding_blocks_on_active_assignments(offboarding_req, mock_audit):
    with patch("app_bot.flows.app_offboarding.GraphClient") as MockG:
        instance = AsyncMock()
        MockG.return_value = instance
        instance.get_application = AsyncMock(
            return_value={"id": "obj-abc-123", "displayName": "Finance Portal"}
        )
        # 2 active users still assigned
        instance.get_app_role_assignments = AsyncMock(return_value=[
            {"principalId": "u1", "principalDisplayName": "Alice"},
            {"principalId": "u2", "principalDisplayName": "Bob"},
        ])

        with pytest.raises(DependencyCheckError, match="2 user"):
            await run_app_offboarding(offboarding_req)

    # Ensure delete was never called
    instance.delete_application.assert_not_called()
    instance.delete_service_principal.assert_not_called()


@pytest.mark.asyncio
async def test_app_offboarding_app_not_found_raises(offboarding_req, mock_audit):
    with patch("app_bot.flows.app_offboarding.GraphClient") as MockG:
        instance = AsyncMock()
        MockG.return_value = instance
        instance.get_application = AsyncMock(
            side_effect=ResourceNotFoundError("Application", "obj-abc-123")
        )

        with pytest.raises(ResourceNotFoundError):
            await run_app_offboarding(offboarding_req)


@pytest.mark.asyncio
async def test_app_offboarding_graph_failure_after_dependency_check(offboarding_req, mock_audit):
    with patch("app_bot.flows.app_offboarding.GraphClient") as MockG:
        instance = AsyncMock()
        MockG.return_value = instance
        instance.get_application = AsyncMock(
            return_value={"id": "obj-abc-123", "displayName": "Finance Portal"}
        )
        instance.get_app_role_assignments = AsyncMock(return_value=[])
        instance.delete_service_principal = AsyncMock(
            side_effect=GraphAPIError("Server error", 500)
        )

        with pytest.raises(GraphAPIError):
            await run_app_offboarding(offboarding_req)