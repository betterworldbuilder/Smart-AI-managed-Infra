"""Ceph capacity adapter."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from ..config import Settings
from ..models import StorageCapacity, StoragePool
from .base import AdapterNotConfigured, InfrastructureAdapter
from .simulation import SimulatedInfrastructure


class CephAdapter(InfrastructureAdapter):
    name = "ceph"

    @abstractmethod
    async def get_capacity(self) -> StorageCapacity: ...

    @abstractmethod
    async def get_pools(self) -> list[StoragePool]: ...


class SimulatedCephAdapter(CephAdapter):
    simulated = True

    def __init__(self, infra: SimulatedInfrastructure) -> None:
        self.infra = infra

    async def health(self) -> dict[str, Any]:
        raw = self.infra.ceph_raw
        return {
            "adapter": self.name,
            "simulated": True,
            "reachable": True,
            "cluster": raw.get("cluster_name"),
            "status": raw.get("health", "HEALTH_OK"),
        }

    async def get_pools(self) -> list[StoragePool]:
        return list(self.infra.ceph_pools)

    async def get_capacity(self) -> StorageCapacity:
        pools = await self.get_pools()
        total, _ = self.infra.ceph_capacity()
        available = round(sum(p.available_tb for p in pools), 3)
        return StorageCapacity(
            total_tb=total or round(sum(p.total_tb for p in pools), 3),
            available_tb=available,
            pools=pools,
        )


class RealCephAdapter(CephAdapter):
    """Reads capacity from the Ceph dashboard/RESTful API (Phase 2+)."""

    simulated = False

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def _get(self, path: str) -> Any:
        import httpx

        if not self.settings.ceph_dashboard_url:
            raise AdapterNotConfigured("CEPH_DASHBOARD_URL is not set")
        url = self.settings.ceph_dashboard_url.rstrip("/") + path
        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:  # noqa: S501
            response = await client.get(url, headers={"Accept": "application/vnd.ceph.api.v1.0+json"})
            response.raise_for_status()
            return response.json()

    async def health(self) -> dict[str, Any]:
        try:
            data = await self._get("/api/health/minimal")
            return {
                "adapter": self.name,
                "simulated": False,
                "reachable": True,
                "status": data.get("health", {}).get("status", "UNKNOWN"),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "adapter": self.name,
                "simulated": False,
                "reachable": False,
                "error": str(exc),
            }

    async def get_pools(self) -> list[StoragePool]:
        data = await self._get("/api/pool?stats=true")
        pools: list[StoragePool] = []
        for item in data:
            stats = item.get("stats", {})
            used = float(stats.get("bytes_used", {}).get("latest", 0) or 0)
            avail = float(stats.get("avail_raw", {}).get("latest", 0) or 0)
            total_tb = (used + avail) / 1024**4
            pools.append(
                StoragePool(
                    name=item.get("pool_name", "pool"),
                    backend="ceph-rbd",
                    total_tb=round(total_tb, 3),
                    available_tb=round(avail / 1024**4, 3),
                )
            )
        return pools

    async def get_capacity(self) -> StorageCapacity:
        pools = await self.get_pools()
        return StorageCapacity(
            total_tb=round(sum(p.total_tb for p in pools), 3),
            available_tb=round(sum(p.available_tb for p in pools), 3),
            pools=pools,
        )


def build_ceph_adapter(settings: Settings, infra: SimulatedInfrastructure | None) -> CephAdapter:
    if settings.ceph_simulated:
        if infra is None:
            raise AdapterNotConfigured("simulation mode requires the simulated datacenter")
        return SimulatedCephAdapter(infra)
    return RealCephAdapter(settings)


__all__ = ["CephAdapter", "RealCephAdapter", "SimulatedCephAdapter", "build_ceph_adapter"]
