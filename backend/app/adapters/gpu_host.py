"""Host GPU detection (MVP section 11).

A physical GPU on the host is *not* the same thing as a GPU the cluster can
schedule. This detector reports the host truth via `nvidia-smi`; the Kubernetes
adapter reports the cluster truth via `nvidia.com/gpu`. The UI shows both, and
the platform never pretends one implies the other.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

log = logging.getLogger(__name__)

QUERY_FIELDS = (
    "name",
    "memory.total",
    "memory.used",
    "driver_version",
    "utilization.gpu",
    "temperature.gpu",
    "pci.bus_id",
)


class HostGPUDetector:
    """Reads the host's NVIDIA GPUs, if any, with `nvidia-smi`."""

    def __init__(self, binary: str = "nvidia-smi", timeout: float = 5.0) -> None:
        self.binary = binary
        self.timeout = timeout
        self._cache: dict[str, Any] | None = None

    async def detect(self, *, refresh: bool = False) -> dict[str, Any]:
        if self._cache is not None and not refresh:
            return self._cache
        result = await self._detect()
        self._cache = result
        return result

    async def _detect(self) -> dict[str, Any]:
        if shutil.which(self.binary) is None:
            return {
                "available": False,
                "reason": "nvidia-smi not found on the host (or not mounted into this container)",
                "gpus": [],
            }
        try:
            process = await asyncio.create_subprocess_exec(
                self.binary,
                f"--query-gpu={','.join(QUERY_FIELDS)}",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "reason": str(exc), "gpus": []}

        if process.returncode != 0:
            return {
                "available": False,
                "reason": (stderr or b"").decode().strip() or "nvidia-smi failed",
                "gpus": [],
            }

        gpus: list[dict[str, Any]] = []
        for index, line in enumerate(stdout.decode().splitlines()):
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < len(QUERY_FIELDS):
                continue
            gpus.append(
                {
                    "index": index,
                    "model": parts[0],
                    "memory_total_gb": _to_gb(parts[1]),
                    "memory_used_gb": _to_gb(parts[2]),
                    "driver_version": parts[3],
                    "utilization_pct": _to_float(parts[4]),
                    "temperature_c": _to_float(parts[5]),
                    "pci_bus_id": parts[6],
                }
            )
        return {
            "available": bool(gpus),
            "reason": None if gpus else "nvidia-smi returned no devices",
            "gpus": gpus,
            "cuda": bool(gpus),
        }

    async def summary(self, visible_to_cluster: bool) -> dict[str, Any]:
        detected = await self.detect()
        gpus = detected.get("gpus", [])
        return {
            "host_gpu": "real" if detected.get("available") else "none",
            "models": [gpu["model"] for gpu in gpus],
            "count": len(gpus),
            "visible_to_host": bool(detected.get("available")),
            "visible_to_cluster": visible_to_cluster,
            "reason": detected.get("reason"),
            "gpus": gpus,
        }


def _to_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_gb(value: str) -> float | None:
    number = _to_float(value)
    return round(number / 1024, 1) if number is not None else None
