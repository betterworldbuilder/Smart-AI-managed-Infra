"""Optional Redis bridge for events and caching.

Redis is part of the compose stack because a real deployment runs several
backend replicas and needs a shared event channel. The application works
perfectly without it -- if `REDIS_URL` is unset or Redis is down, the in-process
`EventBus` is authoritative and the UI still receives everything.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .events import EventBus

log = logging.getLogger(__name__)

CHANNEL = "aiinfra:events"


class RedisBridge:
    def __init__(self, url: str, bus: EventBus) -> None:
        self.url = url
        self.bus = bus
        self._client = None
        self._task: asyncio.Task | None = None
        self.connected = False

    async def start(self) -> None:
        if not self.url:
            return
        try:
            import redis.asyncio as redis

            self._client = redis.from_url(self.url, encoding="utf-8", decode_responses=True)
            await self._client.ping()
            self.connected = True
            log.info("connected to redis at %s", self.url)
        except Exception as exc:  # noqa: BLE001
            log.warning("redis unavailable (%s) -- continuing with the in-process bus", exc)
            self._client = None
            self.connected = False

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            self.connected = False

    async def publish(self, event: dict[str, Any]) -> None:
        if self._client is None:
            return
        try:
            await self._client.publish(CHANNEL, json.dumps(event, default=str))
        except Exception as exc:  # noqa: BLE001
            log.debug("redis publish failed: %s", exc)

    async def cache_set(self, key: str, value: Any, ttl: int = 30) -> None:
        if self._client is None:
            return
        try:
            await self._client.set(key, json.dumps(value, default=str), ex=ttl)
        except Exception as exc:  # noqa: BLE001
            log.debug("redis set failed: %s", exc)

    async def cache_get(self, key: str) -> Any | None:
        if self._client is None:
            return None
        try:
            raw = await self._client.get(key)
            return json.loads(raw) if raw else None
        except Exception as exc:  # noqa: BLE001
            log.debug("redis get failed: %s", exc)
            return None

    async def health(self) -> dict[str, Any]:
        if not self.url:
            return {"configured": False, "reachable": False, "note": "REDIS_URL not set"}
        if self._client is None:
            return {"configured": True, "reachable": False, "url": self.url}
        try:
            await self._client.ping()
            return {"configured": True, "reachable": True, "url": self.url}
        except Exception as exc:  # noqa: BLE001
            return {"configured": True, "reachable": False, "url": self.url, "error": str(exc)}
