"""Shared adapter plumbing."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AdapterError(RuntimeError):
    """Raised when an adapter cannot talk to its backend."""


class AdapterNotConfigured(AdapterError):
    """Real-mode adapter is missing credentials or client libraries."""


class InfrastructureAdapter(ABC):
    """Every adapter reports whether it is simulated and whether it is healthy."""

    name: str = "adapter"
    simulated: bool = True

    @abstractmethod
    async def health(self) -> dict[str, Any]:
        """Light-weight reachability probe, safe to call on every dashboard load."""


def require(module_name: str, hint: str):
    """Import a real-mode client library or explain how to install it."""
    try:
        import importlib

        return importlib.import_module(module_name)
    except ImportError as exc:  # pragma: no cover - only hit in real mode
        raise AdapterNotConfigured(
            f"'{module_name}' is required for real mode. {hint}"
        ) from exc
