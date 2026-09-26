"""Copilot conversation model."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .intent import WizardQuestion, WorkloadIntent


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: f"msg-{uuid4().hex[:10]}")
    role: str  # user | assistant | system
    content: str
    at: datetime = Field(default_factory=_now)
    #: Optional structured payload the UI can render (questions, rec id, ...).
    meta: dict[str, Any] = Field(default_factory=dict)


class Conversation(BaseModel):
    id: str = Field(default_factory=lambda: f"conv-{uuid4().hex[:10]}")
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    title: str = "New request"
    messages: list[ChatMessage] = Field(default_factory=list)
    intent: WorkloadIntent = Field(default_factory=WorkloadIntent)
    #: Intent fields the human actually supplied (as opposed to defaults).
    provided: list[str] = Field(default_factory=list)
    pending_questions: list[WizardQuestion] = Field(default_factory=list)
    recommendation_id: str | None = None
    deployment_id: str | None = None
    ready: bool = False
    llm_provider: str = "mock"

    def add(self, role: str, content: str, **meta: Any) -> ChatMessage:
        message = ChatMessage(role=role, content=content, meta=meta)
        self.messages.append(message)
        self.updated_at = _now()
        return message


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class WizardAnswers(BaseModel):
    """Answers submitted from the guided wizard (or the chat quick-replies)."""

    answers: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    conversation: Conversation
    recommendation_id: str | None = None
