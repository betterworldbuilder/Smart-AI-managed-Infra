"""OpenStack inventory adapter (section 7).

Reads Nova compute hosts, Placement resource providers, PCI/GPU inventory,
flavors, images, networks and Cinder capacity. GPU models are *discovered*, not
assumed -- in real mode they come from the PCI device spec / Placement resource
classes, in simulation from `resources.yaml`.
"""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from typing import Any

from ..config import Settings
from ..models import (
    Capacity,
    ComputeNode,
    GPUCapacity,
    GPUDevice,
    Platform,
    PlatformInventory,
    StorageCapacity,
    StoragePool,
)
from .base import AdapterNotConfigured, InfrastructureAdapter, require
from .simulation import SimulatedInfrastructure

#: Custom Placement resource class used for whole-card passthrough GPUs.
DEFAULT_GPU_RESOURCE_CLASS = "CUSTOM_PGPU"


class OpenStackInventoryAdapter(InfrastructureAdapter):
    name = "openstack"

    @abstractmethod
    async def get_services(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_compute_hosts(self) -> list[ComputeNode]: ...

    @abstractmethod
    async def get_resource_providers(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_gpu_inventory(self) -> list[GPUDevice]: ...

    @abstractmethod
    async def get_flavors(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_images(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_networks(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_volume_capacity(self) -> StorageCapacity: ...

    @abstractmethod
    async def get_instances(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_cluster_capacity(self) -> PlatformInventory: ...


class SimulatedOpenStackAdapter(OpenStackInventoryAdapter):
    simulated = True

    def __init__(self, infra: SimulatedInfrastructure) -> None:
        self.infra = infra

    async def health(self) -> dict[str, Any]:
        raw = self.infra.openstack_raw
        services = raw.get("services", [])
        return {
            "adapter": self.name,
            "simulated": True,
            "reachable": True,
            "region": raw.get("region"),
            "release": raw.get("release"),
            "deployed_by": raw.get("deployed_by"),
            "services_up": sum(1 for s in services if s.get("state") == "up"),
            "services_total": len(services),
        }

    async def get_services(self) -> list[dict[str, Any]]:
        return list(self.infra.openstack_raw.get("services", []))

    async def get_compute_hosts(self) -> list[ComputeNode]:
        return self.infra.nodes_for(Platform.OPENSTACK)

    async def get_resource_providers(self) -> list[dict[str, Any]]:
        """Placement view: one provider per compute host, with GPU inventory."""
        providers: list[dict[str, Any]] = []
        for host in self.infra.nodes_for(Platform.OPENSTACK):
            gpus = [self.infra.gpus[gid] for gid in host.gpu_ids]
            inventories: dict[str, dict[str, int]] = {
                "VCPU": {"total": host.cpu_total, "used": host.cpu_total - host.cpu_available},
                "MEMORY_MB": {
                    "total": host.ram_gb_total * 1024,
                    "used": (host.ram_gb_total - host.ram_gb_available) * 1024,
                },
                "DISK_GB": {
                    "total": host.disk_gb_total,
                    "used": host.disk_gb_total - host.disk_gb_available,
                },
            }
            if gpus:
                inventories[DEFAULT_GPU_RESOURCE_CLASS] = {
                    "total": len(gpus),
                    "used": sum(1 for g in gpus if not g.available),
                }
            providers.append(
                {
                    "uuid": f"rp-{host.name}",
                    "name": host.name,
                    "availability_zone": host.availability_zone,
                    "inventories": inventories,
                    "traits": (
                        ["COMPUTE_STATUS_ENABLED"]
                        + ([f"CUSTOM_GPU_{gpus[0].model}"] if gpus else [])
                    ),
                }
            )
        return providers

    async def get_gpu_inventory(self) -> list[GPUDevice]:
        return self.infra.gpus_for(Platform.OPENSTACK)

    async def get_flavors(self) -> list[dict[str, Any]]:
        return list(self.infra.openstack_raw.get("flavors", []))

    async def get_images(self) -> list[dict[str, Any]]:
        return list(self.infra.openstack_raw.get("images", []))

    async def get_networks(self) -> list[dict[str, Any]]:
        return list(self.infra.openstack_raw.get("networks", []))

    async def get_volume_capacity(self) -> StorageCapacity:
        pools = [p for p in self.infra.ceph_pools if p.consumer in ("openstack", "shared")]
        return StorageCapacity(
            total_tb=round(sum(p.total_tb for p in pools), 3),
            available_tb=round(sum(p.available_tb for p in pools), 3),
            pools=pools,
        )

    async def get_instances(self) -> list[dict[str, Any]]:
        return list(self.infra.os_instances)

    async def get_cluster_capacity(self) -> PlatformInventory:
        hosts = await self.get_compute_hosts()
        gpus = await self.get_gpu_inventory()
        raw = self.infra.openstack_raw
        return PlatformInventory(
            platform=Platform.OPENSTACK,
            available=True,
            name=raw.get("region"),
            version=raw.get("release"),
            nodes=hosts,
            cpu=Capacity(
                total=sum(h.cpu_total for h in hosts),
                available=sum(h.cpu_available for h in hosts),
            ),
            ram_gb=Capacity(
                total=sum(h.ram_gb_total for h in hosts),
                available=sum(h.ram_gb_available for h in hosts),
            ),
            gpu=GPUCapacity(
                total=len(gpus), available=sum(1 for g in gpus if g.available), devices=gpus
            ),
            storage=await self.get_volume_capacity(),
            details={
                "deployed_by": raw.get("deployed_by"),
                "services": raw.get("services", []),
                "flavors": raw.get("flavors", []),
                "images": raw.get("images", []),
                "networks": raw.get("networks", []),
                "instances": self.infra.os_instances,
                "gpu_resource_class": DEFAULT_GPU_RESOURCE_CLASS,
            },
        )


class RealOpenStackAdapter(OpenStackInventoryAdapter):
    """Live OpenStack inventory via openstacksdk (Phase 4/5)."""

    simulated = False

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._conn = None

    def _connection(self):
        if self._conn is not None:
            return self._conn
        sdk = require("openstack", "pip install -r backend/requirements-real.txt")
        settings = self.settings
        if not settings.os_auth_url:
            raise AdapterNotConfigured("OS_AUTH_URL is not set")
        self._conn = sdk.connect(
            auth_url=settings.os_auth_url,
            username=settings.os_username,
            password=settings.os_password,
            project_name=settings.os_project_name,
            user_domain_name=settings.os_user_domain_name,
            project_domain_name=settings.os_project_domain_name,
            region_name=settings.os_region_name,
        )
        return self._conn

    async def health(self) -> dict[str, Any]:
        try:
            conn = await asyncio.to_thread(self._connection)
            services = await asyncio.to_thread(lambda: list(conn.compute.services()))
            return {
                "adapter": self.name,
                "simulated": False,
                "reachable": True,
                "services_up": sum(1 for s in services if s.state == "up"),
                "services_total": len(services),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "adapter": self.name,
                "simulated": False,
                "reachable": False,
                "error": str(exc),
            }

    async def get_services(self) -> list[dict[str, Any]]:
        conn = await asyncio.to_thread(self._connection)
        services = await asyncio.to_thread(lambda: list(conn.compute.services()))
        return [{"name": s.binary, "host": s.host, "state": s.state} for s in services]

    async def get_compute_hosts(self) -> list[ComputeNode]:
        conn = await asyncio.to_thread(self._connection)
        hypervisors = await asyncio.to_thread(lambda: list(conn.compute.hypervisors(details=True)))
        nodes: list[ComputeNode] = []
        for hv in hypervisors:
            vcpus = int(getattr(hv, "vcpus", 0) or 0)
            vcpus_used = int(getattr(hv, "vcpus_used", 0) or 0)
            ram_mb = int(getattr(hv, "memory_size", 0) or 0)
            ram_used_mb = int(getattr(hv, "memory_used", 0) or 0)
            disk = int(getattr(hv, "local_disk_size", 0) or 0)
            disk_used = int(getattr(hv, "local_disk_used", 0) or 0)
            nodes.append(
                ComputeNode(
                    name=hv.name,
                    platform=Platform.OPENSTACK,
                    cpu_total=vcpus,
                    cpu_available=max(vcpus - vcpus_used, 0),
                    ram_gb_total=ram_mb // 1024,
                    ram_gb_available=max((ram_mb - ram_used_mb) // 1024, 0),
                    disk_gb_total=disk,
                    disk_gb_available=max(disk - disk_used, 0),
                    status=str(getattr(hv, "state", "up")),
                )
            )
        return nodes

    async def get_resource_providers(self) -> list[dict[str, Any]]:
        """Placement inventory, including any CUSTOM_* GPU resource classes."""
        conn = await asyncio.to_thread(self._connection)

        def _fetch() -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for rp in conn.placement.resource_providers():
                inventories = conn.placement.get(
                    f"/resource_providers/{rp.id}/inventories"
                ).json()
                usages = conn.placement.get(f"/resource_providers/{rp.id}/usages").json()
                merged = {
                    name: {
                        "total": data.get("total", 0),
                        "used": usages.get("usages", {}).get(name, 0),
                    }
                    for name, data in inventories.get("inventories", {}).items()
                }
                out.append({"uuid": rp.id, "name": rp.name, "inventories": merged})
            return out

        return await asyncio.to_thread(_fetch)

    async def get_gpu_inventory(self) -> list[GPUDevice]:
        """Derive GPUs from Placement custom resource classes.

        A whole-card passthrough GPU shows up as a custom resource class (by
        convention `CUSTOM_PGPU`, configurable) on the compute host's resource
        provider. The model name is discovered from the provider traits.
        """
        from ..models import GPUAllocationType, GPUStatus

        providers = await self.get_resource_providers()
        devices: list[GPUDevice] = []
        index = 0
        for provider in providers:
            for name, data in provider.get("inventories", {}).items():
                if not name.startswith("CUSTOM_") or "GPU" not in name:
                    continue
                total = int(data.get("total", 0))
                used = int(data.get("used", 0))
                model = name.replace("CUSTOM_", "").replace("PGPU", "GPU")
                for slot in range(total):
                    index += 1
                    devices.append(
                        GPUDevice(
                            id=f"gpu-{index:03d}",
                            vendor="NVIDIA",
                            model=model,
                            memory_gb=0,
                            host=provider.get("name", "unknown"),
                            status=GPUStatus.ALLOCATED if slot < used else GPUStatus.AVAILABLE,
                            allocation_type=GPUAllocationType.PASSTHROUGH,
                            platform=Platform.OPENSTACK,
                        )
                    )
        return devices

    async def get_flavors(self) -> list[dict[str, Any]]:
        conn = await asyncio.to_thread(self._connection)
        flavors = await asyncio.to_thread(lambda: list(conn.compute.flavors(details=True)))
        return [
            {
                "name": f.name,
                "vcpu": f.vcpus,
                "ram_gb": int(f.ram / 1024),
                "disk_gb": f.disk,
                "extra_specs": dict(getattr(f, "extra_specs", {}) or {}),
            }
            for f in flavors
        ]

    async def get_images(self) -> list[dict[str, Any]]:
        conn = await asyncio.to_thread(self._connection)
        images = await asyncio.to_thread(lambda: list(conn.image.images()))
        return [{"name": i.name, "os": getattr(i, "os_type", None) or "linux"} for i in images]

    async def get_networks(self) -> list[dict[str, Any]]:
        conn = await asyncio.to_thread(self._connection)
        networks = await asyncio.to_thread(lambda: list(conn.network.networks()))
        return [
            {"name": n.name, "external": bool(getattr(n, "is_router_external", False))}
            for n in networks
        ]

    async def get_volume_capacity(self) -> StorageCapacity:
        conn = await asyncio.to_thread(self._connection)
        try:
            pools = await asyncio.to_thread(lambda: list(conn.block_storage.backend_pools()))
        except Exception:  # noqa: BLE001 - admin-only API
            return StorageCapacity()
        entries = []
        for pool in pools:
            caps = getattr(pool, "capabilities", {}) or {}
            entries.append(
                StoragePool(
                    name=getattr(pool, "name", "cinder"),
                    backend=str(caps.get("volume_backend_name", "cinder")),
                    total_tb=float(caps.get("total_capacity_gb", 0) or 0) / 1024,
                    available_tb=float(caps.get("free_capacity_gb", 0) or 0) / 1024,
                    consumer="openstack",
                )
            )
        return StorageCapacity(
            total_tb=round(sum(p.total_tb for p in entries), 3),
            available_tb=round(sum(p.available_tb for p in entries), 3),
            pools=entries,
        )

    async def get_instances(self) -> list[dict[str, Any]]:
        conn = await asyncio.to_thread(self._connection)
        servers = await asyncio.to_thread(lambda: list(conn.compute.servers(all_projects=True)))
        return [
            {
                "name": s.name,
                "status": s.status,
                "flavor": (s.flavor or {}).get("original_name"),
                "host": getattr(s, "compute_host", None),
            }
            for s in servers
        ]

    async def get_cluster_capacity(self) -> PlatformInventory:
        hosts = await self.get_compute_hosts()
        gpus = await self.get_gpu_inventory()
        return PlatformInventory(
            platform=Platform.OPENSTACK,
            available=True,
            name=self.settings.os_region_name,
            nodes=hosts,
            cpu=Capacity(
                total=sum(h.cpu_total for h in hosts),
                available=sum(h.cpu_available for h in hosts),
            ),
            ram_gb=Capacity(
                total=sum(h.ram_gb_total for h in hosts),
                available=sum(h.ram_gb_available for h in hosts),
            ),
            gpu=GPUCapacity(
                total=len(gpus), available=sum(1 for g in gpus if g.available), devices=gpus
            ),
            storage=await self.get_volume_capacity(),
            details={"mode": "real"},
        )


def build_openstack_adapter(
    settings: Settings, infra: SimulatedInfrastructure | None
) -> OpenStackInventoryAdapter:
    if settings.openstack_simulated:
        if infra is None:
            raise AdapterNotConfigured("simulation mode requires the simulated datacenter")
        return SimulatedOpenStackAdapter(infra)
    return RealOpenStackAdapter(settings)
