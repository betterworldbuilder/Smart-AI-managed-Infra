"""Persistence layer."""

from __future__ import annotations

from ..config import Settings
from .base import Kind, Store
from .memory import InMemoryStore
from .repository import Repositories, Repository

__all__ = ["InMemoryStore", "Kind", "Repositories", "Repository", "Store", "build_store"]


def build_store(settings: Settings) -> Store:
    """In-memory unless a DATABASE_URL is configured."""
    if settings.database_url:
        from .sql import SqlStore

        return SqlStore(settings.database_url)
    return InMemoryStore()
