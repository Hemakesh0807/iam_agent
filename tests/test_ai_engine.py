import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from shared.models import (
    FlowName, FlowStatus, RiskSeverity,
    UserOnboardingRequest, UserOffboardingRequest,
    RiskIsolationRequest, AppOnboardingRequest, AppOffboardingRequest,
)
from shared.exceptions import (
    ApprovalRequiredError, IntentClassificationError,
    PolicyViolationError, RiskScoringError,
)
from ai_engine.intent_classifier import IntentClassifier
from ai_engine.risk_scorer import RiskScorer
from ai_engine.policy_engine import PolicyEngine
from ai_engine.approval_gate import ApprovalGate
from ai_engine.orchestrator import IAMOrchestrator


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def user_onboarding_req():
    return UserOnboardingRequest(
        display_name="Jane Doe",
        mail_nickname="janedoe",
        user_principal_name="janedoe@org.com",
        department="Engineering",
        job_title="Engineer",
        group_ids=["grp-001"],
        requested_by="hr_system",
    )

@pytest.fixture
def user_offboarding_req():
    return UserOffboardingRequest(
        user_id="user-123",
        user_principal_name="jane@org.com",
        requested_by="hr_system",
        reason="Employee resigned",
        revoke_sessions=True,
    )

@pytest.fixture
def risk_isolation_req():
    return RiskIsolationRequest(
        user_id="user-123",
        user_principal_name="jane@org.com",
        alert_id="alert-456",
        severity=RiskSeverity.HIGH,
        alert_reason="Impossible travel detected",
        auto_isolate=True,
    )

@pytest.fixture
def app_onboarding_req():
    return AppOnboardingRequest(
        display_name="My App",
        owner_id="user-001",
        requested_scopes=["User.Read", "Mail.Read"],
        requested_by="dev_team",
    )

@pytest.fixture
def app_offboarding_req():
    return AppOffboardingRequest(
        app_id="app-abc",
        object_id="obj-abc",
        service_principal_id="sp-abc",
        requested_by="admin",
        reason="App decommissioned",
    )


# ── Intent Classifier ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_intent_classifier_user_onboarding():
    llm_response = MagicMock()
    llm_response.choices[0].message.content = (
        '{"intent": "user_onboarding", "confidence": 0.95, "reasoning": "New hire request"}'
    )

    with patch("ai_engine.intent_classifier.AsyncOpenAI") as mock_openai:
        mock_client = AsyncMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=llm_response)

        classifier = IntentClassifier()
        result = await classifier.classify("Onboard new hire Jane Doe from Engineering")

    assert result["intent"] == FlowName.USER_ONBOARDING
    assert result["confidence"] == 0.95


@pytest.mark.asyncio
async def test_intent_classifier_low_confidence_raises():
    llm_response = MagicMock()
    llm_response.choices[0].message.content = (
        '{"intent": "user_onboarding", "confidence": 0.3, "reasoning": "Uncertain"}'
    )

    with patch("ai_engine.intent_classifier.AsyncOpenAI") as mock_openai:
        mock_client = AsyncMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=llm_response)

        classifier = IntentClassifier()
        with pytest.raises(IntentClassificationError, match="confidence too low"):
            await classifier.classify("Do something with an account maybe")


@pytest.mark.asyncio
async def test_intent_classifier_unknown_raises():
    llm_response = MagicMock()
    llm_response.choices[0].message.content = (
        '{"intent": "unknown", "confidence": 0.9, "reasoning": "Cannot classify"}'
    )

    with patch("ai_engine.intent_classifier.AsyncOpenAI") as mock_openai:
        mock_client = AsyncMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=llm_response)

        classifier = IntentClassifier()
        with pytest.raises(IntentClassificationError):
            await classifier.classify("Reset my wifi password")


# ── Risk Scorer ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_risk_scorer_high_severity():
    llm_response = MagicMock()
    llm_response.choices[0].message.content = (
        '{"severity": "high", "confidence": 0.98, "auto_isolate": true, '
        '"reasoning": "Impossible travel", "recommended_actions": ["disable", "revoke"]}'
    )

    with patch("ai_engine.risk_scorer.AsyncOpenAI") as mock_openai:
        mock_client = AsyncMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=llm_response)

        scorer = RiskScorer()
        result = await scorer.score("jane@org.com", "Impossible travel", "INC-001")

    assert result["severity"] == RiskSeverity.HIGH
    assert result["auto_isolate"] is True


@pytest.mark.asyncio
async def test_risk_scorer_high_severity_forces_auto_isolate():
    """High severity must always auto-isolate even if LLM returns False."""
    llm_response = MagicMock()
    llm_response.choices[0].message.content = (
        '{"severity": "high", "confidence": 0.9, "auto_isolate": false, '
        '"reasoning": "High risk", "recommended_actions": []}'
    )

    with patch("ai_engine.risk_scorer.AsyncOpenAI") as mock_openai:
        mock_client = AsyncMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=llm_response)

        scorer = RiskScorer()
        result = await scorer.score("jane@org.com", "High risk alert")

    # Safety override must kick in
    assert result["auto_isolate"] is True


# ── Policy Engine ─────────────────────────────────────────────────────────────

def test_policy_user_onboarding_passes(user_onboarding_req):
    PolicyEngine().check(FlowName.USER_ONBOARDING, user_onboarding_req)  # No exception


def test_policy_user_onboarding_no_groups_fails():
    req = UserOnboardingRequest(
        display_name="X", mail_nickname="x", user_principal_name="x@org.com",
        department="Engineering", job_title="Eng", group_ids=[], requested_by="hr",
    )
    with pytest.raises(PolicyViolationError, match="group_id"):
        PolicyEngine().check(FlowName.USER_ONBOARDING, req)


def test_policy_high_privilege_dept_no_manager_fails():
    req = UserOnboardingRequest(
        display_name="X", mail_nickname="x", user_principal_name="x@org.com",
        department="Security", job_title="Analyst", group_ids=["grp-1"],
        requested_by="hr", manager_id=None,
    )
    with pytest.raises(PolicyViolationError, match="manager_id"):
        PolicyEngine().check(FlowName.USER_ONBOARDING, req)


def test_policy_offboarding_no_reason_fails(user_offboarding_req):
    user_offboarding_req.reason = ""
    with pytest.raises(PolicyViolationError, match="reason"):
        PolicyEngine().check(FlowName.USER_OFFBOARDING, user_offboarding_req)


def test_policy_offboarding_no_revoke_fails(user_offboarding_req):
    user_offboarding_req.revoke_sessions = False
    with pytest.raises(PolicyViolationError, match="revoke_sessions"):
        PolicyEngine().check(FlowName.USER_OFFBOARDING, user_offboarding_req)


def test_policy_app_forbidden_scope_fails(app_onboarding_req):
    app_onboarding_req.requested_scopes = ["User.Read", "Directory.ReadWrite.All"]
    with pytest.raises(PolicyViolationError, match="forbidden scopes"):
        PolicyEngine().check(FlowName.APP_ONBOARDING, app_onboarding_req)


def test_policy_app_onboarding_passes(app_onboarding_req):
    PolicyEngine().check(FlowName.APP_ONBOARDING, app_onboarding_req)  # No exception


# ── Approval Gate ─────────────────────────────────────────────────────────────

def test_approval_gate_onboarding_auto_executes():
    ApprovalGate().evaluate(FlowName.USER_ONBOARDING, {})  # No exception


def test_approval_gate_offboarding_requires_approval():
    with pytest.raises(ApprovalRequiredError):
        ApprovalGate().evaluate(FlowName.USER_OFFBOARDING, {})


def test_approval_gate_app_offboarding_requires_approval():
    with pytest.raises(ApprovalRequiredError):
        ApprovalGate().evaluate(FlowName.APP_OFFBOARDING, {})


def test_approval_gate_high_risk_auto_executes():
    ApprovalGate().evaluate(
        FlowName.RISK_ISOLATION, {"severity": RiskSeverity.HIGH}
    )  # No exception


def test_approval_gate_medium_risk_escalates():
    with pytest.raises(ApprovalRequiredError, match="MEDIUM"):
        ApprovalGate().evaluate(
            FlowName.RISK_ISOLATION, {"severity": RiskSeverity.MEDIUM}
        )


# ── Orchestrator ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_orchestrator_routes_onboarding_to_execute(user_onboarding_req):
    mock_flow = AsyncMock(return_value={"user_id": "new-user-123"})

    with patch("ai_engine.orchestrator.IntentClassifier") as MockClassifier, \
         patch("ai_engine.orchestrator.RiskScorer"), \
         patch("ai_engine.orchestrator.PolicyEngine") as MockPolicy, \
         patch("ai_engine.orchestrator.ApprovalGate") as MockGate:

        MockClassifier.return_value.classify = AsyncMock(return_value={
            "intent": FlowName.USER_ONBOARDING,
            "confidence": 0.97,
            "reasoning": "New hire",
        })
        MockPolicy.return_value.check = MagicMock()
        MockGate.return_value.evaluate = MagicMock()  # Does not raise = auto-execute

        orchestrator = IAMOrchestrator(bot_registry={
            FlowName.USER_ONBOARDING: mock_flow,
        })
        state = await orchestrator.run(
            request_text="Onboard Jane Doe",
            request_payload=user_onboarding_req,
        )

    assert state["status"] == FlowStatus.COMPLETED
    assert state["result"]["user_id"] == "new-user-123"
    mock_flow.assert_called_once()


@pytest.mark.asyncio
async def test_orchestrator_escalates_offboarding(user_offboarding_req):
    with patch("ai_engine.orchestrator.IntentClassifier") as MockClassifier, \
         patch("ai_engine.orchestrator.RiskScorer"), \
         patch("ai_engine.orchestrator.PolicyEngine") as MockPolicy, \
         patch("ai_engine.orchestrator.ApprovalGate") as MockGate:

        MockClassifier.return_value.classify = AsyncMock(return_value={
            "intent": FlowName.USER_OFFBOARDING,
            "confidence": 0.95,
            "reasoning": "Termination request",
        })
        MockPolicy.return_value.check = MagicMock()
        MockGate.return_value.evaluate = MagicMock(
            side_effect=ApprovalRequiredError("user_offboarding", "Destructive action")
        )

        orchestrator = IAMOrchestrator(bot_registry={})
        state = await orchestrator.run(
            request_text="Offboard Jane Doe",
            request_payload=user_offboarding_req,
        )

    assert state["status"] == FlowStatus.ESCALATED
    assert state["result"]["escalated"] is True


@pytest.mark.asyncio
async def test_orchestrator_handles_classification_failure(user_onboarding_req):
    with patch("ai_engine.orchestrator.IntentClassifier") as MockClassifier, \
         patch("ai_engine.orchestrator.RiskScorer"), \
         patch("ai_engine.orchestrator.PolicyEngine"), \
         patch("ai_engine.orchestrator.ApprovalGate"):

        MockClassifier.return_value.classify = AsyncMock(
            side_effect=IntentClassificationError("Cannot classify")
        )

        orchestrator = IAMOrchestrator(bot_registry={})
        state = await orchestrator.run(
            request_text="Do something vague",
            request_payload=user_onboarding_req,
        )

    assert state["status"] == FlowStatus.FAILED
    assert "Cannot classify" in state["error"]