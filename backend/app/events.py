"""Realtime event bus feeding the SSE stream and the WebSocket endpoint."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, AsyncIterator

log = logging.getLogger(__name__)

MAX_BUFFER = 300
QUEUE_SIZE = 200


class EventBus:
    """Fan-out pub/sub with a small replay buffer.

    Slow consumers are dropped rather than allowed to block the deployment
    pipeline -- a stalled browser tab must never stall openCenter.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._buffer: deque[dict[str, Any]] = deque(maxlen=MAX_BUFFER)
        self._seq = 0

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self._buffer)[-limit:]

    def publish(self, topic: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self._seq += 1
        event = {
            "seq": self._seq,
            "topic": topic,
            "at": datetime.now(timezone.utc).isoformat(),
            "data": payload or {},
        }
        self._buffer.append(event)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover - defensive
                log.warning("event subscriber too slow, dropping it")
                self._subscribers.discard(queue)
        return event

    @contextlib.contextmanager
    def subscribe(self):
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    async def stream(self, replay: int = 10) -> AsyncIterator[str]:
        """Server-Sent Events formatted stream."""
        with self.subscribe() as queue:
            for event in self.recent(replay):
                yield _sse(event)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield _sse(event)


def _sse(event: dict[str, Any]) -> str:
    return f"id: {event['seq']}\nevent: {event['topic']}\ndata: {json.dumps(event, default=str)}\n\n"
