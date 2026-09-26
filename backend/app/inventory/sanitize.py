"""LLM safety boundary (section 28).

The model is an architect, not an operator. It gets *facts about capacity* and
nothing else: no kubeconfig, no OpenStack credentials, no SSH keys, no secrets,
no endpoints. `sanitize_inventory()` is the only function allowed to build the
infrastructure context passed into a prompt.
"""

from __future__ import annotations

import re
from typing import Any

from ..models import GlobalInventory

#: Substrings that must never appear in anything sent to an LLM.
FORBIDDEN_KEY_PATTERNS = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "credential",
    "kubeconfig",
    "private_key",
    "privatekey",
    "ssh",
    "cert",
    "auth_url",
    "endpoint",
    "clouds_yaml",
    "bearer",
)

_SECRET_VALUE_RE = re.compile(
    r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)|(eyJ[A-Za-z0-9_\-]{10,}\.)|(AKIA[0-9A-Z]{16})"
)


class SanitizationError(RuntimeError):
    """Raised when something secret-shaped was about to reach the model."""


def scrub(value: Any) -> Any:
    """Recursively drop forbidden keys and redact secret-looking strings."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if any(pattern in str(key).lower() for pattern in FORBIDDEN_KEY_PATTERNS):
                continue
            cleaned[key] = scrub(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    if isinstance(value, str) and _SECRET_VALUE_RE.search(value):
        return "[redacted]"
    return value


def assert_clean(payload: Any) -> None:
    """Fail loudly if a forbidden key survived. Used by the LLM client."""
    def walk(node: Any, path: str = "") -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if any(pattern in str(key).lower() for pattern in FORBIDDEN_KEY_PATTERNS):
                    raise SanitizationError(f"refusing to send '{path}.{key}' to the LLM")
                walk(item, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")
        elif isinstance(node, str) and _SECRET_VALUE_RE.search(node):
            raise SanitizationError(f"secret-shaped value at '{path}'")

    walk(payload)


def sanitize_inventory(inventory: GlobalInventory) -> dict[str, Any]:
    """Capacity facts only -- the shape shown as "GOOD" in section 28."""
    gpu_models: dict[str, dict[str, Any]] = {}
    for device in inventory.gpu.devices:
        entry = gpu_models.setdefault(
            device.model,
            {
                "gpu_type": device.model,
                "memory_gb": device.memory_gb,
                "total": 0,
                "available": 0,
                "platforms": set(),
            },
        )
        entry["total"] += 1
        if device.available:
            entry["available"] += 1
        entry["platforms"].add(str(device.platform))

    for entry in gpu_models.values():
        entry["platforms"] = sorted(entry["platforms"])

    payload = {
        "cpu_cores": {"total": inventory.cpu.total, "available": inventory.cpu.available},
        "ram_gb": {"total": inventory.ram_gb.total, "available": inventory.ram_gb.available},
        "gpu": {
            "total": inventory.gpu.total,
            "available": inventory.gpu.available,
            "models": list(gpu_models.values()),
        },
        "storage_tb": {
            "total": inventory.storage.total_tb,
            "available": inventory.storage.available_tb,
            "backends": sorted({pool.backend for pool in inventory.storage.pools}),
        },
        "platforms": {
            name: {
                "available": platform.available,
                "node_count": len(platform.nodes),
                "cpu_available": platform.cpu.available,
                "ram_gb_available": platform.ram_gb.available,
                "gpu_available": platform.gpu.available,
            }
            for name, platform in inventory.platforms.items()
        },
    }
    assert_clean(payload)
    return payload
