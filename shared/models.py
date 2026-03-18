from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Enums ─────────────────────────────────────────────────────────────────────

class FlowName(str, Enum):
    USER_ONBOARDING   = "user_onboarding"
    USER_OFFBOARDING  = "user_offboarding"
    RISK_ISOLATION    = "risk_isolation"
    APP_ONBOARDING    = "app_onboarding"
    APP_OFFBOARDING   = "app_offboarding"


class FlowStatus(str, Enum):
    PENDING    = "pending"
    APPROVED   = "approved"
    EXECUTING  = "executing"
    COMPLETED  = "completed"
    FAILED     = "failed"
    ESCALATED  = "escalated"


class RiskSeverity(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"


# ── User Models ───────────────────────────────────────────────────────────────

class UserOnboardingRequest(BaseModel):
    display_name: str
    mail_nickname: str
    user_principal_name: str
    department: str
    job_title: str
    manager_id: str | None = None
    group_ids: list[str] = Field(default_factory=list)
    license_sku_id: str | None = None
    app_role_assignments: list[dict[str, str]] = Field(default_factory=list)
    requested_by: str
    source: str = "hr_system"   # hr_system | admin_portal | ad_sync | bulk_import


class UserOffboardingRequest(BaseModel):
    user_id: str                # Entra ID object ID
    user_principal_name: str
    requested_by: str
    reason: str
    revoke_sessions: bool = True
    remove_licenses: bool = True
    remove_group_memberships: bool = True
    backup_manager_id: str | None = None   # Reassign direct reports to this manager


class RiskIsolationRequest(BaseModel):
    user_id: str
    user_principal_name: str
    alert_id: str
    severity: RiskSeverity
    alert_reason: str
    sentinel_incident_id: str | None = None
    auto_isolate: bool = True  # False triggers escalation instead


# ── App Models ────────────────────────────────────────────────────────────────

class AppOnboardingRequest(BaseModel):
    display_name: str
    owner_id: str              # Entra ID object ID of the app owner
    requested_scopes: list[str]
    redirect_uris: list[str] = Field(default_factory=list)
    description: str | None = None
    requested_by: str


class AppOffboardingRequest(BaseModel):
    app_id: str                # Entra ID application ID (client ID)
    object_id: str             # Entra ID application object ID
    service_principal_id: str
    requested_by: str
    reason: str
    revoke_user_assignments: bool = True


# ── Graph API Response Models ─────────────────────────────────────────────────

class GraphUser(BaseModel):
    id: str
    display_name: str = Field(alias="displayName")
    user_principal_name: str = Field(alias="userPrincipalName")
    account_enabled: bool = Field(alias="accountEnabled")
    department: str | None = None
    job_title: str | None = Field(None, alias="jobTitle")

    class Config:
        populate_by_name = True


class GraphApplication(BaseModel):
    id: str
    app_id: str = Field(alias="appId")
    display_name: str = Field(alias="displayName")

    class Config:
        populate_by_name = True


# ── Audit Log Entry ───────────────────────────────────────────────────────────

class AuditLogEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    bot_type: str                           # Cosmos partition key
    flow_name: FlowName
    status: FlowStatus
    principal_id: str                       # User or app object ID acted upon
    requested_by: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    details: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    graph_operations: list[str] = Field(default_factory=list)  # Log each API call made