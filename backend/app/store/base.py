"""Tiny document store abstraction.

The POC keeps its state as JSON documents (conversations, recommendations,
deployments, audit events). That is deliberately boring: the interesting logic
lives in the Governor, not in an ORM. Two backends are provided -- in-memory
(default, zero dependencies) and PostgreSQL via SQLAlchemy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Store(ABC):
    """Key/value document store partitioned by `kind`."""

    @abstractmethod
    async def put(self, kind: str, key: str, data: dict[str, Any]) -> None: ...

    @abstractmethod
    async def get(self, kind: str, key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    async def list(self, kind: str, limit: int | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def delete(self, kind: str, key: str) -> None: ...

    async def startup(self) -> None:  # pragma: no cover - default no-op
        return None

    async def shutdown(self) -> None:  # pragma: no cover - default no-op
        return None


class Kind:
    CONVERSATION = "conversation"
    RECOMMENDATION = "recommendation"
    DEPLOYMENT = "deployment"
    AUDIT = "audit"
    ADVISOR = "advisor"
    ALERT = "alert"
