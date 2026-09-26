"""Audit events (section 29).

Every meaningful action -- especially every AI suggestion and every human
decision -- lands here. The audit trail is what makes "the AI cannot deploy on
its own" an auditable claim rather than a promise.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .enums import ActorKind


class AuditAction:
    """Canonical action names (section 29 "Track" list)."""

    USER_REQUEST = "user_request"
    AI_INTERPRETATION = "ai_interpretation"
    AI_QUESTION = "ai_question"
    AI_RECOMMENDATION = "ai_recommendation"
    POLICY_EVALUATION = "policy_evaluation"
    HUMAN_MODIFICATION = "human_modification"
    DEPLOYMENT_SELECTED = "deployment_option_selected"
    DEPLOYMENT_APPROVED = "deployment_approved"
    DEPLOYMENT_REJECTED = "deployment_rejected"
    SPEC_GENERATED = "deployment_spec_generated"
    PLAN_GENERATED = "deployment_plan_generated"
    FINAL_APPROVAL = "deployment_final_approved"
    DEPLOYMENT_EXECUTION = "deployment_execution"
    DEPLOYMENT_RESULT = "deployment_result"
    DEPLOYMENT_ROLLBACK = "deployment_rollback"
    POST_DEPLOY_RECOMMENDATION = "post_deployment_recommendation"
    ADVISOR_DECISION = "advisor_decision"
    REMEDIATION_DECISION = "remediation_decision"
    STATE_TRANSITION = "state_transition"
    POLICY_DENIAL = "policy_denial"


class AuditEvent(BaseModel):
    id: str = Field(default_factory=lambda: f"aud-{uuid4().hex[:10]}")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    user: str = "admin"
    actor_kind: ActorKind = ActorKind.HUMAN
    action: str
    deployment: str | None = None
    conversation: str | None = None
    recommendation: str | None = None
    resources: dict[str, Any] = Field(default_factory=dict)
    detail: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
