"""Global resource inventory (section 8).

Merges Kubernetes, OpenStack, Ceph and GPU telemetry into one model. The
Governor asks this service -- never an adapter -- so adding a platform later
does not ripple into the decision logic.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from ..adapters.ceph import CephAdapter
from ..adapters.genestack import GenestackAdapter
from ..adapters.kubernetes import KubernetesInventoryAdapter
from ..adapters.openstack import OpenStackInventoryAdapter
from ..models import (
    Capacity,
    GlobalInventory,
    GPUCapacity,
    GPUDevice,
    Platform,
    PlatformInventory,
    StorageCapacity,
)

CACHE_TTL_SECONDS = 2.0


class GlobalResourceInventoryService:
    def __init__(
        self,
        kubernetes: KubernetesInventoryAdapter,
        openstack: OpenStackInventoryAdapter,
        ceph: CephAdapter,
        genestack: GenestackAdapter | None = None,
        simulated: bool = True,
    ) -> None:
        self.kubernetes = kubernetes
        self.openstack = openstack
        self.ceph = ceph
        self.genestack = genestack
        self.simulated = simulated
        self._cache: tuple[float, GlobalInventory] | None = None

    def invalidate(self) -> None:
        self._cache = None

    async def get(self, *, refresh: bool = False) -> GlobalInventory:
        if not refresh and self._cache is not None:
            cached_at, inventory = self._cache
            if time.monotonic() - cached_at < CACHE_TTL_SECONDS:
                return inventory
        inventory = await self._build()
        self._cache = (time.monotonic(), inventory)
        return inventory

    async def _build(self) -> GlobalInventory:
        k8s, openstack, ceph_capacity = await asyncio.gather(
            self._safe_capacity(self.kubernetes, Platform.KUBERNETES),
            self._safe_capacity(self.openstack, Platform.OPENSTACK),
            self._safe_storage(),
        )

        devices: list[GPUDevice] = [*k8s.gpu.devices, *openstack.gpu.devices]
        # Ceph is the storage substrate for both platforms -- count it once.
        storage = ceph_capacity

        return GlobalInventory(
            cpu=Capacity(
                total=k8s.cpu.total + openstack.cpu.total,
                available=k8s.cpu.available + openstack.cpu.available,
            ),
            ram_gb=Capacity(
                total=k8s.ram_gb.total + openstack.ram_gb.total,
                available=k8s.ram_gb.available + openstack.ram_gb.available,
            ),
            gpu=GPUCapacity(
                total=len(devices),
                available=sum(1 for d in devices if d.available),
                devices=devices,
            ),
            storage=storage,
            platforms={
                str(Platform.KUBERNETES): k8s,
                str(Platform.OPENSTACK): openstack,
            },
            simulated=self.simulated,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    async def _safe_capacity(self, adapter: Any, platform: Platform) -> PlatformInventory:
        try:
            return await adapter.get_cluster_capacity()
        except Exception as exc:  # noqa: BLE001 - a dead platform must not break the UI
            return PlatformInventory(
                platform=platform, available=False, details={"error": str(exc)}
            )

    async def _safe_storage(self) -> StorageCapacity:
        try:
            return await self.ceph.get_capacity()
        except Exception:  # noqa: BLE001
            return StorageCapacity()

    # -- convenience views ---------------------------------------------------

    async def gpus(self) -> list[GPUDevice]:
        return (await self.get()).gpu.devices

    async def summary(self) -> dict[str, Any]:
        inventory = await self.get()
        return {
            "cpu": {"total": inventory.cpu.total, "available": inventory.cpu.available},
            "ram_gb": {"total": inventory.ram_gb.total, "available": inventory.ram_gb.available},
            "gpu": {
                "total": inventory.gpu.total,
                "available": inventory.gpu.available,
                "by_model": inventory.gpu_model_counts(),
            },
            "storage": {
                "total_tb": inventory.storage.total_tb,
                "available_tb": inventory.storage.available_tb,
            },
            "platforms": {
                name: {
                    "available": platform.available,
                    "nodes": len(platform.nodes),
                    "cpu": platform.cpu.model_dump(),
                    "ram_gb": platform.ram_gb.model_dump(),
                    "gpu": {"total": platform.gpu.total, "available": platform.gpu.available},
                }
                for name, platform in inventory.platforms.items()
            },
            "simulated": inventory.simulated,
            "generated_at": inventory.generated_at,
        }

    async def health(self) -> dict[str, Any]:
        adapters = [self.kubernetes, self.openstack, self.ceph]
        if self.genestack is not None:
            adapters.append(self.genestack)
        results = await asyncio.gather(*(a.health() for a in adapters), return_exceptions=True)
        out: dict[str, Any] = {}
        for adapter, result in zip(adapters, results, strict=False):
            out[adapter.name] = (
                {"adapter": adapter.name, "reachable": False, "error": str(result)}
                if isinstance(result, Exception)
                else result
            )
        return out
