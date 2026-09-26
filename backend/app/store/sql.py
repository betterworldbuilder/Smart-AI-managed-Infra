"""PostgreSQL (or any SQLAlchemy URL) document store.

Activated by setting `DATABASE_URL`, e.g.

    DATABASE_URL=postgresql+asyncpg://aiinfra:aiinfra@postgres:5432/aiinfra

Documents are stored as JSON in a single `records` table: the POC's schema
changes constantly, and a document table keeps migrations out of the critical
path. Swap this for real tables when the model settles.
"""

from __future__ import annotations

import json
from typing import Any

from .base import Store

_DDL = """
CREATE TABLE IF NOT EXISTS records (
    kind        TEXT        NOT NULL,
    key         TEXT        NOT NULL,
    data        TEXT        NOT NULL,
    updated_at  TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (kind, key)
)
"""


class SqlStore(Store):
    def __init__(self, url: str) -> None:
        self._url = url
        self._engine = None
        self._seq = 0

    async def startup(self) -> None:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        self._engine = create_async_engine(self._url, future=True, pool_pre_ping=True)
        async with self._engine.begin() as conn:
            await conn.execute(text(_DDL))

    async def shutdown(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    def _require_engine(self):
        if self._engine is None:
            raise RuntimeError("SqlStore.startup() was never awaited")
        return self._engine

    async def put(self, kind: str, key: str, data: dict[str, Any]) -> None:
        from sqlalchemy import text

        engine = self._require_engine()
        payload = json.dumps(data, default=str)
        statement = text(
            "INSERT INTO records (kind, key, data) VALUES (:kind, :key, :data) "
            "ON CONFLICT (kind, key) DO UPDATE SET data = EXCLUDED.data, "
            "updated_at = CURRENT_TIMESTAMP"
        )
        async with engine.begin() as conn:
            await conn.execute(statement, {"kind": kind, "key": key, "data": payload})

    async def get(self, kind: str, key: str) -> dict[str, Any] | None:
        from sqlalchemy import text

        engine = self._require_engine()
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT data FROM records WHERE kind = :kind AND key = :key"),
                    {"kind": kind, "key": key},
                )
            ).first()
        return json.loads(row[0]) if row else None

    async def list(self, kind: str, limit: int | None = None) -> list[dict[str, Any]]:
        from sqlalchemy import text

        engine = self._require_engine()
        sql = "SELECT data FROM records WHERE kind = :kind ORDER BY updated_at ASC"
        params: dict[str, Any] = {"kind": kind}
        if limit is not None:
            sql += " LIMIT :limit"
            params["limit"] = limit
        async with engine.connect() as conn:
            rows = (await conn.execute(text(sql), params)).fetchall()
        return [json.loads(row[0]) for row in rows]

    async def delete(self, kind: str, key: str) -> None:
        from sqlalchemy import text

        engine = self._require_engine()
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM records WHERE kind = :kind AND key = :key"),
                {"kind": kind, "key": key},
            )
