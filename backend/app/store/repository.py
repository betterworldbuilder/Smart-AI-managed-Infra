"""Typed repositories on top of the document store."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

from ..models import (
    AdvisorRecommendation,
    AuditEvent,
    Conversation,
    Deployment,
    Recommendation,
    RemediationAlert,
)
from .base import Kind, Store

T = TypeVar("T", bound=BaseModel)


class Repository(Generic[T]):
    def __init__(self, store: Store, kind: str, model: type[T]) -> None:
        self._store = store
        self._kind = kind
        self._model = model

    async def save(self, item: T) -> T:
        await self._store.put(self._kind, getattr(item, "id"), item.model_dump(mode="json"))
        return item

    async def get(self, key: str) -> T | None:
        raw = await self._store.get(self._kind, key)
        return self._model.model_validate(raw) if raw else None

    async def require(self, key: str) -> T:
        item = await self.get(key)
        if item is None:
            raise KeyError(f"{self._kind} '{key}' not found")
        return item

    async def list(self, limit: int | None = None) -> list[T]:
        return [self._model.model_validate(raw) for raw in await self._store.list(self._kind, limit)]

    async def delete(self, key: str) -> None:
        await self._store.delete(self._kind, key)


class Repositories:
    """One place to reach every repository."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.conversations: Repository[Conversation] = Repository(
            store, Kind.CONVERSATION, Conversation
        )
        self.recommendations: Repository[Recommendation] = Repository(
            store, Kind.RECOMMENDATION, Recommendation
        )
        self.deployments: Repository[Deployment] = Repository(store, Kind.DEPLOYMENT, Deployment)
        self.audit: Repository[AuditEvent] = Repository(store, Kind.AUDIT, AuditEvent)
        self.advice: Repository[AdvisorRecommendation] = Repository(
            store, Kind.ADVISOR, AdvisorRecommendation
        )
        self.alerts: Repository[RemediationAlert] = Repository(store, Kind.ALERT, RemediationAlert)
