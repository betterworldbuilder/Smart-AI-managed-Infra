"""In-memory document store -- the default for SIMULATION_MODE."""

from __future__ import annotations

import asyncio
import copy
from collections import OrderedDict
from typing import Any

from .base import Store


class InMemoryStore(Store):
    def __init__(self) -> None:
        self._data: dict[str, OrderedDict[str, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def put(self, kind: str, key: str, data: dict[str, Any]) -> None:
        async with self._lock:
            bucket = self._data.setdefault(kind, OrderedDict())
            bucket[key] = copy.deepcopy(data)

    async def get(self, kind: str, key: str) -> dict[str, Any] | None:
        bucket = self._data.get(kind, {})
        found = bucket.get(key)
        return copy.deepcopy(found) if found is not None else None

    async def list(self, kind: str, limit: int | None = None) -> list[dict[str, Any]]:
        bucket = list(self._data.get(kind, {}).values())
        if limit is not None:
            bucket = bucket[-limit:]
        return copy.deepcopy(bucket)

    async def delete(self, kind: str, key: str) -> None:
        async with self._lock:
            self._data.get(kind, {}).pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._data.clear()
