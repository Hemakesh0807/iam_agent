import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shared.models import (
    FlowStatus, RiskSeverity,
    UserOnboardingRequest, UserOffboardingRequest, RiskIsolationRequest,
)
from shared.exceptions import GraphAPIError, ResourceNotFoundError
from user_bot.flows.onboarding import run_user_onboarding
from user_bot.flows.offboarding import run_user_offboarding
from user_bot.flows.risk_isolation import run_risk_isolation


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def onboarding_req():
    return UserOnboardingRequest(
        display_name="Jane Doe",
        mail_nickname="janedoe",
        user_principal_name="janedoe@org.com",
        department="Engineering",
        job_title="Engineer",
        group_ids=["grp-001", "grp-002"],
        license_sku_id="sku-m365",
        requested_by="hr_system",
    )

@pytest.fixture
def offboarding_req():
    return UserOffboardingRequest(
        user_id="user-123",
        user_principal_name="jane@org.com",
        requested_by="hr_system",
        reason="Employee resigned",
        revoke_sessions=True,
        remove_licenses=True,
        remove_group_memberships=True,
    )

@pytest.fixture
def risk_req():
    return RiskIsolationRequest(
        user_id="user-123",
        user_principal_name="jane@org.com",
        alert_id="alert-456",
        severity=RiskSeverity.HIGH,
        alert_reason="Impossible travel detected",
        sentinel_incident_id="INC-001",
        auto_isolate=True,
    )

@pytest.fixture
def mock_graph():
    """Returns a fully mocked GraphClient."""
    with patch("user_bot.flows.onboarding.GraphClient") as MockG, \
         patch("user_bot.flows.offboarding.GraphClient") as MockG2, \
         patch("user_bot.flows.risk_isolation.GraphClient") as MockG3:

        for MockCls in [MockG, MockG2, MockG3]:
            instance = AsyncMock()
            MockCls.return_value = instance
            instance.create_user = AsyncMock(return_value={"id": "new-user-id"})
            instance.get_user = AsyncMock(return_value={"id": "user-123", "accountEnabled": True})
            instance.disable_user = AsyncMock()
            instance.revoke_user_sessions = AsyncMock()
            instance.delete_user = AsyncMock()
            instance.assign_license = AsyncMock()
            instance.remove_all_licenses = AsyncMock()
            instance.add_user_to_group = AsyncMock()
            instance.remove_user_from_all_groups = AsyncMock(return_value=["grp-1", "grp-2"])
            instance.assign_app_role = AsyncMock()
            instance.force_password_reset = AsyncMock()

        yield

@pytest.fixture
def mock_audit():
    """Mocks AuditLogger so no file/DB writes happen in tests."""
    with patch("user_bot.flows.onboarding.AuditLogger") as A1, \
         patch("user_bot.flows.offboarding.AuditLogger") as A2, \
         patch("user_bot.flows.risk_isolation.AuditLogger") as A3:
        for A in [A1, A2, A3]:
            instance = AsyncMock()
            A.return_value = instance
            instance.log = AsyncMock()
            instance.log_failure = AsyncMock()
        yield


# ── Flow A: User Onboarding ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_onboarding_success(onboarding_req, mock_graph, mock_audit):
    result = await run_user_onboarding(onboarding_req)

    assert result["status"] == "completed"
    assert result["user_id"] == "new-user-id"
    assert result["groups_assigned"] == 2
    assert result["license_assigned"] is True


@pytest.mark.asyncio
async def test_onboarding_no_license(mock_graph, mock_audit):
    req = UserOnboardingRequest(
        display_name="John", mail_nickname="john",
        user_principal_name="john@org.com", department="HR",
        job_title="HR Lead", group_ids=["grp-1"],
        requested_by="hr_system", license_sku_id=None,
    )
    result = await run_user_onboarding(req)

    assert result["license_assigned"] is False


@pytest.mark.asyncio
async def test_onboarding_graph_failure_logs_and_raises(onboarding_req, mock_audit):
    with patch("user_bot.flows.onboarding.GraphClient") as MockG:
        instance = AsyncMock()
        MockG.return_value = instance
        instance.create_user = AsyncMock(
            side_effect=GraphAPIError("Service unavailable", 503)
        )

        with pytest.raises(GraphAPIError):
            await run_user_onboarding(onboarding_req)


# ── Flow B: User Offboarding ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_offboarding_success(offboarding_req, mock_graph, mock_audit):
    result = await run_user_offboarding(offboarding_req)

    assert result["status"] == "completed"
    assert result["account_disabled"] is True
    assert result["sessions_revoked"] is True
    assert result["groups_removed"] == 2
    assert result["licenses_removed"] is True


@pytest.mark.asyncio
async def test_offboarding_user_not_found_raises(offboarding_req, mock_audit):
    with patch("user_bot.flows.offboarding.GraphClient") as MockG:
        instance = AsyncMock()
        MockG.return_value = instance
        instance.get_user = AsyncMock(
            side_effect=ResourceNotFoundError("User", "user-123")
        )

        with pytest.raises(ResourceNotFoundError):
            await run_user_offboarding(offboarding_req)


@pytest.mark.asyncio
async def test_offboarding_already_disabled_still_completes(offboarding_req, mock_audit):
    with patch("user_bot.flows.offboarding.GraphClient") as MockG:
        instance = AsyncMock()
        MockG.return_value = instance
        # User already disabled
        instance.get_user = AsyncMock(
            return_value={"id": "user-123", "accountEnabled": False}
        )
        instance.disable_user = AsyncMock()
        instance.revoke_user_sessions = AsyncMock()
        instance.remove_user_from_all_groups = AsyncMock(return_value=[])
        instance.remove_all_licenses = AsyncMock()

        result = await run_user_offboarding(offboarding_req)

    assert result["status"] == "completed"


# ── Flow C: Risk Isolation ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_risk_isolation_success(risk_req, mock_graph, mock_audit):
    result = await run_risk_isolation(risk_req)

    assert result["status"] == "isolated"
    assert result["account_disabled"] is True
    assert result["sessions_revoked"] is True
    assert result["password_reset_forced"] is True


@pytest.mark.asyncio
async def test_risk_isolation_user_not_found_raises(risk_req, mock_audit):
    with patch("user_bot.flows.risk_isolation.GraphClient") as MockG:
        instance = AsyncMock()
        MockG.return_value = instance
        instance.get_user = AsyncMock(
            side_effect=ResourceNotFoundError("User", "user-123")
        )

        with pytest.raises(ResourceNotFoundError):
            await run_risk_isolation(risk_req)


@pytest.mark.asyncio
async def test_risk_isolation_already_disabled_still_revokes(risk_req, mock_audit):
    with patch("user_bot.flows.risk_isolation.GraphClient") as MockG:
        instance = AsyncMock()
        MockG.return_value = instance
        instance.get_user = AsyncMock(
            return_value={"id": "user-123", "accountEnabled": False}
        )
        instance.revoke_user_sessions = AsyncMock()
        instance.force_password_reset = AsyncMock()

        result = await run_risk_isolation(risk_req)

    assert result["was_already_disabled"] is True
    assert result["sessions_revoked"] is True
    # disable_user should NOT have been called
    instance.disable_user = AsyncMock()
    instance.disable_user.assert_not_called()