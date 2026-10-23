from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class IncidentStatus(StrEnum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    INVESTIGATING = "INVESTIGATING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class IncidentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=3, max_length=5000)
    service: str = Field(default="checkout-api", min_length=1, max_length=100)
    tenant_id: str = Field(default="demo", min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    approved: bool
    comment: str | None = Field(default=None, max_length=1000)


class PlanStep(BaseModel):
    id: str
    title: str
    tool: str
    status: str = "PENDING"


class Evidence(BaseModel):
    id: str = Field(default_factory=lambda: f"ev-{uuid4().hex[:10]}")
    source: str
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1, default=1.0)


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: f"tc-{uuid4().hex[:10]}")
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: str
    attempts: int = 1
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None


class ApprovalRequest(BaseModel):
    action: str
    reason: str
    risk: str = "medium"
    requested_at: datetime = Field(default_factory=utc_now)


class IncidentState(BaseModel):
    id: str = Field(default_factory=lambda: f"inc-{uuid4().hex[:12]}")
    run_id: str = Field(default_factory=lambda: f"run-{uuid4().hex[:12]}")
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    tenant_id: str
    title: str
    description: str
    service: str
    status: IncidentStatus = IncidentStatus.CREATED
    scenario: str | None = None
    priority: str = "P2"
    plan: list[PlanStep] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    diagnosis: str | None = None
    recommended_action: str | None = None
    approval: ApprovalRequest | None = None
    approval_decision: ApprovalDecision | None = None
    final_summary: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Event(BaseModel):
    id: int | None = None
    incident_id: str
    type: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class IncidentResponse(BaseModel):
    incident: IncidentState
    next_action: str | None = None

