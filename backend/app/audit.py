"""Audit service (section 29).

Every AI suggestion, every policy verdict and every human decision is recorded.
Audit events are also published on the event bus so the UI can show the trail
building up live.
"""

from __future__ import annotations

import logging
from typing import Any

from .events import EventBus
from .models import ActorKind, AuditEvent
from .store import Repositories

log = logging.getLogger(__name__)


class AuditService:
    def __init__(self, repositories: Repositories, bus: EventBus) -> None:
        self.repositories = repositories
        self.bus = bus

    async def record(
        self,
        *,
        action: str,
        user: str = "admin",
        actor_kind: ActorKind = ActorKind.HUMAN,
        deployment: str | None = None,
        conversation: str | None = None,
        recommendation: str | None = None,
        resources: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
        message: str = "",
    ) -> AuditEvent:
        event = AuditEvent(
            action=action,
            user=user,
            actor_kind=actor_kind,
            deployment=deployment,
            conversation=conversation,
            recommendation=recommendation,
            resources=resources or {},
            detail=detail or {},
            message=message,
        )
        await self.repositories.audit.save(event)
        self.bus.publish("audit", event.model_dump(mode="json"))
        log.info("audit %s by %s (%s)", action, user, actor_kind)
        return event

    async def list(self, limit: int = 200) -> list[AuditEvent]:
        events = await self.repositories.audit.list()
        return sorted(events, key=lambda event: event.timestamp, reverse=True)[:limit]

    async def for_deployment(self, deployment_id: str) -> list[AuditEvent]:
        events = await self.repositories.audit.list()
        return sorted(
            (event for event in events if event.deployment == deployment_id),
            key=lambda event: event.timestamp,
        )
