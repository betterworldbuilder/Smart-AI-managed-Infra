"""Inventory, GPU and topology endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..models import GlobalInventory, Platform
from ..topology import build_topology
from .deps import ContainerDep, UserDep

router = APIRouter(prefix="/inventory", tags=["inventory"])


@router.get("/global", response_model=GlobalInventory)
async def global_inventory(container: ContainerDep, user: UserDep, refresh: bool = False):
    return await container.inventory.get(refresh=refresh)


@router.get("/summary")
async def summary(container: ContainerDep, user: UserDep) -> dict:
    return await container.inventory.summary()


@router.get("/kubernetes")
async def kubernetes(container: ContainerDep, user: UserDep) -> dict:
    capacity = await container.kubernetes.get_cluster_capacity()
    return {
        "capacity": capacity.model_dump(),
        "workloads": await container.kubernetes.get_running_workloads(),
        "storage": [pool.model_dump() for pool in await container.kubernetes.get_persistent_storage()],
        "simulated": container.kubernetes.simulated,
    }


@router.get("/openstack")
async def openstack(container: ContainerDep, user: UserDep) -> dict:
    capacity = await container.openstack.get_cluster_capacity()
    return {
        "capacity": capacity.model_dump(),
        "services": await container.openstack.get_services(),
        "flavors": await container.openstack.get_flavors(),
        "images": await container.openstack.get_images(),
        "networks": await container.openstack.get_networks(),
        "instances": await container.openstack.get_instances(),
        "resource_providers": await container.openstack.get_resource_providers(),
        "simulated": container.openstack.simulated,
    }


@router.get("/genestack")
async def genestack(container: ContainerDep, user: UserDep) -> dict:
    return await container.genestack.summary()


@router.get("/ceph")
async def ceph(container: ContainerDep, user: UserDep) -> dict:
    capacity = await container.ceph.get_capacity()
    return {"capacity": capacity.model_dump(), "simulated": container.ceph.simulated}


@router.get("/gpus")
async def gpus(
    container: ContainerDep,
    user: UserDep,
    platform: str | None = Query(default=None, description="kubernetes | openstack"),
) -> dict:
    inventory = await container.inventory.get()
    devices = inventory.gpu.devices
    if platform:
        try:
            wanted = Platform(platform)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        devices = [device for device in devices if device.platform is wanted]
    return {
        "total": len(devices),
        "available": sum(1 for device in devices if device.available),
        "by_model": inventory.gpu_model_counts(),
        "devices": [device.model_dump() for device in devices],
    }


@router.get("/host-gpu")
async def host_gpu(container: ContainerDep, user: UserDep) -> dict:
    capabilities = await container.capabilities.capabilities()
    return await container.host_gpu.summary(
        visible_to_cluster=capabilities.get("kubernetes_gpu") == "real"
    )


@router.get("/tree")
async def tree(container: ContainerDep, user: UserDep) -> dict:
    """The Infrastructure page's tree view."""
    inventory = await container.inventory.get()
    kubernetes = inventory.platform_of(Platform.KUBERNETES)
    openstack = inventory.platform_of(Platform.OPENSTACK)

    def node_entry(node) -> dict:
        gpus = [
            device.model_dump()
            for device in inventory.gpu.devices
            if device.id in node.gpu_ids
        ]
        return {
            "name": node.name,
            "cpu": {"total": node.cpu_total, "available": node.cpu_available},
            "ram_gb": {"total": node.ram_gb_total, "available": node.ram_gb_available},
            "gpus": gpus,
            "labels": node.labels,
            "status": node.status,
            "availability_zone": node.availability_zone,
        }

    return {
        "kubernetes": {
            "name": kubernetes.name,
            "available": kubernetes.available,
            "nodes": [node_entry(node) for node in kubernetes.nodes],
            "storage": kubernetes.storage.model_dump(),
        },
        "openstack": {
            "name": openstack.name,
            "available": openstack.available,
            "hosts": [node_entry(node) for node in openstack.nodes],
            "instances": openstack.details.get("instances", []),
            "storage": openstack.storage.model_dump(),
        },
        "ceph": inventory.storage.model_dump(),
    }


topology_router = APIRouter(tags=["topology"])


@topology_router.get("/topology")
async def topology(
    container: ContainerDep,
    user: UserDep,
    deployment_id: str | None = Query(default=None),
) -> dict:
    inventory = await container.inventory.get()
    capabilities = await container.capabilities.capabilities()
    deployment = None
    if deployment_id:
        deployment = await container.repositories.deployments.get(deployment_id)
    return build_topology(inventory, capabilities, deployment)
