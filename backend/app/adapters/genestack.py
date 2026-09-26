"""Genestack adapter (section 4).

Genestack *is* OpenStack-on-Kubernetes. We do not reimplement any of it: this
adapter is a read-only lens that answers "is the Genestack-managed OpenStack
control plane healthy, and what does it have to offer?" by combining the
Kubernetes view (where the control plane pods live) with the OpenStack view
(what the cloud exposes).
"""

from __future__ import annotations

from typing import Any

from ..models import Platform
from .base import InfrastructureAdapter
from .kubernetes import KubernetesInventoryAdapter
from .openstack import DEFAULT_GPU_RESOURCE_CLASS, OpenStackInventoryAdapter

#: The OpenStack services Genestack deploys that we care about (section 4).
REQUIRED_SERVICES = (
    "keystone",
    "nova",
    "placement",
    "neutron",
    "ovn",
    "glance",
    "cinder",
    "horizon",
    "mariadb",
    "rabbitmq",
)
OPTIONAL_SERVICES = ("manila", "swift")


class GenestackAdapter(InfrastructureAdapter):
    name = "genestack"

    def __init__(
        self,
        openstack: OpenStackInventoryAdapter,
        kubernetes: KubernetesInventoryAdapter,
    ) -> None:
        self.openstack = openstack
        self.kubernetes = kubernetes
        self.simulated = openstack.simulated

    async def health(self) -> dict[str, Any]:
        openstack_health = await self.openstack.health()
        services = await self.openstack.get_services()
        by_name = {str(s.get("name", "")).lower(): s for s in services}
        missing = [name for name in REQUIRED_SERVICES if name not in by_name]
        down = [
            name
            for name, service in by_name.items()
            if service.get("state") not in (None, "up", "enabled")
        ]
        return {
            "adapter": self.name,
            "simulated": self.simulated,
            "reachable": bool(openstack_health.get("reachable")),
            "openstack": openstack_health,
            "required_services_missing": missing,
            "services_down": down,
            "ready": openstack_health.get("reachable") and not missing and not down,
        }

    async def get_openstack_availability(self) -> dict[str, Any]:
        services = await self.openstack.get_services()
        return {
            "services": services,
            "required": list(REQUIRED_SERVICES),
            "optional": list(OPTIONAL_SERVICES),
        }

    async def get_nova_compute_nodes(self) -> list[dict[str, Any]]:
        hosts = await self.openstack.get_compute_hosts()
        return [
            {
                "host": host.name,
                "availability_zone": host.availability_zone,
                "vcpu_total": host.cpu_total,
                "vcpu_available": host.cpu_available,
                "ram_gb_total": host.ram_gb_total,
                "ram_gb_available": host.ram_gb_available,
                "gpu_count": len(host.gpu_ids),
                "iommu": host.iommu,
                "status": host.status,
            }
            for host in hosts
        ]

    async def get_placement_resources(self) -> list[dict[str, Any]]:
        return await self.openstack.get_resource_providers()

    async def get_gpu_resource_classes(self) -> dict[str, Any]:
        gpus = await self.openstack.get_gpu_inventory()
        per_model: dict[str, dict[str, int]] = {}
        for gpu in gpus:
            entry = per_model.setdefault(gpu.model, {"total": 0, "available": 0})
            entry["total"] += 1
            if gpu.available:
                entry["available"] += 1
        return {
            "default_resource_class": DEFAULT_GPU_RESOURCE_CLASS,
            "per_model": per_model,
            "allocation_types": sorted({str(g.allocation_type) for g in gpus}),
        }

    async def summary(self) -> dict[str, Any]:
        """Everything the Infrastructure page shows under "Genestack"."""
        inventory = await self.openstack.get_cluster_capacity()
        control_plane_nodes = await self.kubernetes.list_nodes()
        return {
            "health": await self.health(),
            "availability": await self.get_openstack_availability(),
            "compute_nodes": await self.get_nova_compute_nodes(),
            "placement": await self.get_placement_resources(),
            "gpu_resource_classes": await self.get_gpu_resource_classes(),
            "flavors": await self.openstack.get_flavors(),
            "images": await self.openstack.get_images(),
            "networks": await self.openstack.get_networks(),
            "storage": (await self.openstack.get_volume_capacity()).model_dump(),
            "hosted_on": {
                "platform": str(Platform.KUBERNETES),
                "nodes": [node.name for node in control_plane_nodes],
            },
            "capacity": inventory.model_dump(),
        }
