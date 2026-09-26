"""Kubernetes inventory adapter (section 6).

Two implementations behind one interface:

* `SimulatedKubernetesAdapter` -- reads `simulation/resources.yaml`.
* `RealKubernetesAdapter`      -- talks to a cluster through the official
  client; understands `nvidia.com/gpu`, GPU Feature Discovery labels and
  DCGM-style metrics.

The Governor only ever sees the normalised models, so switching between the two
changes nothing upstream.
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

#: Extended resource name exposed by the NVIDIA device plugin.
GPU_RESOURCE = "nvidia.com/gpu"
#: Labels published by GPU Feature Discovery.
GFD_PRODUCT_LABEL = "nvidia.com/gpu.product"
GFD_MEMORY_LABEL = "nvidia.com/gpu.memory"
GFD_COUNT_LABEL = "nvidia.com/gpu.count"


class KubernetesInventoryAdapter(InfrastructureAdapter):
    name = "kubernetes"

    @abstractmethod
    async def list_nodes(self) -> list[ComputeNode]: ...

    @abstractmethod
    async def get_node_resources(self, node: str) -> ComputeNode | None: ...

    @abstractmethod
    async def get_gpu_resources(self) -> list[GPUDevice]: ...

    @abstractmethod
    async def get_running_workloads(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_persistent_storage(self) -> list[StoragePool]: ...

    @abstractmethod
    async def get_cluster_capacity(self) -> PlatformInventory: ...


class SimulatedKubernetesAdapter(KubernetesInventoryAdapter):
    simulated = True

    def __init__(self, infra: SimulatedInfrastructure) -> None:
        self.infra = infra

    async def health(self) -> dict[str, Any]:
        raw = self.infra.kubernetes_raw
        return {
            "adapter": self.name,
            "simulated": True,
            "reachable": True,
            "cluster": raw.get("cluster_name"),
            "version": raw.get("version"),
            "gpu_operator": raw.get("gpu_operator"),
        }

    async def list_nodes(self) -> list[ComputeNode]:
        return self.infra.nodes_for(Platform.KUBERNETES)

    async def get_node_resources(self, node: str) -> ComputeNode | None:
        return self.infra.k8s_nodes.get(node)

    async def get_gpu_resources(self) -> list[GPUDevice]:
        return self.infra.gpus_for(Platform.KUBERNETES)

    async def get_running_workloads(self) -> list[dict[str, Any]]:
        return list(self.infra.k8s_workloads)

    async def get_persistent_storage(self) -> list[StoragePool]:
        return [p for p in self.infra.ceph_pools if p.consumer in ("kubernetes", "shared")]

    async def get_cluster_capacity(self) -> PlatformInventory:
        nodes = await self.list_nodes()
        gpus = await self.get_gpu_resources()
        pools = await self.get_persistent_storage()
        raw = self.infra.kubernetes_raw
        return PlatformInventory(
            platform=Platform.KUBERNETES,
            available=True,
            name=raw.get("cluster_name"),
            version=raw.get("version"),
            nodes=nodes,
            cpu=Capacity(
                total=sum(n.cpu_total for n in nodes),
                available=sum(n.cpu_available for n in nodes),
            ),
            ram_gb=Capacity(
                total=sum(n.ram_gb_total for n in nodes),
                available=sum(n.ram_gb_available for n in nodes),
            ),
            gpu=GPUCapacity(
                total=len(gpus),
                available=sum(1 for g in gpus if g.available),
                devices=gpus,
            ),
            storage=StorageCapacity(
                total_tb=round(sum(p.total_tb for p in pools), 3),
                available_tb=round(sum(p.available_tb for p in pools), 3),
                pools=pools,
            ),
            details={
                "gpu_operator": raw.get("gpu_operator"),
                "storage_classes": raw.get("storage_classes", []),
                "workloads": len(self.infra.k8s_workloads),
                "gpu_resource_name": GPU_RESOURCE,
            },
        )


class RealKubernetesAdapter(KubernetesInventoryAdapter):
    """Live cluster inventory (Phase 2).

    Requires `pip install -r requirements-real.txt` and a working kubeconfig.
    Every call is bounced onto a worker thread because the official client is
    synchronous.
    """

    simulated = False

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._core = None
        self._apps = None
        self._loaded = False

    def _clients(self):
        if self._loaded:
            return self._core, self._apps
        k8s = require("kubernetes", "pip install -r backend/requirements-real.txt")
        config, client = k8s.config, k8s.client
        try:
            if self.settings.kubeconfig:
                config.load_kube_config(
                    config_file=self.settings.kubeconfig,
                    context=self.settings.kubernetes_context or None,
                )
            else:
                config.load_incluster_config()
        except Exception:  # noqa: BLE001 - fall back to the default kubeconfig
            config.load_kube_config(context=self.settings.kubernetes_context or None)
        self._core = client.CoreV1Api()
        self._apps = client.AppsV1Api()
        self._loaded = True
        return self._core, self._apps

    @staticmethod
    def _cpu_to_cores(value: str | None) -> float:
        if not value:
            return 0.0
        text = str(value)
        if text.endswith("m"):
            return float(text[:-1]) / 1000.0
        if text.endswith("n"):
            return float(text[:-1]) / 1e9
        return float(text)

    @staticmethod
    def _mem_to_gb(value: str | None) -> float:
        if not value:
            return 0.0
        text = str(value)
        units = {
            "Ki": 1 / (1024**2),
            "Mi": 1 / 1024,
            "Gi": 1.0,
            "Ti": 1024.0,
            "K": 1e3 / 1024**3,
            "M": 1e6 / 1024**3,
            "G": 1e9 / 1024**3,
        }
        for suffix, factor in units.items():
            if text.endswith(suffix):
                return float(text[: -len(suffix)]) * factor
        return float(text) / 1024**3

    async def health(self) -> dict[str, Any]:
        try:
            core, _ = await asyncio.to_thread(self._clients)
            version = await asyncio.to_thread(lambda: core.api_client.call_api)  # noqa: ARG005
            nodes = await asyncio.to_thread(core.list_node)
            return {
                "adapter": self.name,
                "simulated": False,
                "reachable": True,
                "nodes": len(nodes.items),
                "client": bool(version),
            }
        except Exception as exc:  # noqa: BLE001 - surfaced to the dashboard
            return {
                "adapter": self.name,
                "simulated": False,
                "reachable": False,
                "error": str(exc),
            }

    async def list_nodes(self) -> list[ComputeNode]:
        core, _ = await asyncio.to_thread(self._clients)
        node_list = await asyncio.to_thread(core.list_node)
        pods = await asyncio.to_thread(core.list_pod_for_all_namespaces)

        used: dict[str, dict[str, float]] = {}
        for pod in pods.items:
            node_name = pod.spec.node_name
            if not node_name or pod.status.phase not in ("Running", "Pending"):
                continue
            bucket = used.setdefault(node_name, {"cpu": 0.0, "ram": 0.0, "gpu": 0.0})
            for container in pod.spec.containers or []:
                requests = (container.resources.requests or {}) if container.resources else {}
                limits = (container.resources.limits or {}) if container.resources else {}
                bucket["cpu"] += self._cpu_to_cores(requests.get("cpu"))
                bucket["ram"] += self._mem_to_gb(requests.get("memory"))
                bucket["gpu"] += float(limits.get(GPU_RESOURCE, 0) or 0)

        nodes: list[ComputeNode] = []
        for item in node_list.items:
            name = item.metadata.name
            allocatable = item.status.allocatable or {}
            cpu_total = self._cpu_to_cores(allocatable.get("cpu"))
            ram_total = self._mem_to_gb(allocatable.get("memory"))
            spent = used.get(name, {"cpu": 0.0, "ram": 0.0})
            ready = any(
                c.type == "Ready" and c.status == "True" for c in (item.status.conditions or [])
            )
            nodes.append(
                ComputeNode(
                    name=name,
                    platform=Platform.KUBERNETES,
                    cpu_total=int(cpu_total),
                    cpu_available=max(int(cpu_total - spent["cpu"]), 0),
                    ram_gb_total=int(ram_total),
                    ram_gb_available=max(int(ram_total - spent["ram"]), 0),
                    gpu_ids=[
                        f"{name}-gpu-{i}"
                        for i in range(int(float(allocatable.get(GPU_RESOURCE, 0) or 0)))
                    ],
                    labels=dict(item.metadata.labels or {}),
                    status="ready" if ready else "notready",
                )
            )
        return nodes

    async def get_node_resources(self, node: str) -> ComputeNode | None:
        return next((n for n in await self.list_nodes() if n.name == node), None)

    async def get_gpu_resources(self) -> list[GPUDevice]:
        from ..models import GPUAllocationType, GPUStatus

        core, _ = await asyncio.to_thread(self._clients)
        node_list = await asyncio.to_thread(core.list_node)
        pods = await asyncio.to_thread(core.list_pod_for_all_namespaces)

        claimed: dict[str, list[str]] = {}
        for pod in pods.items:
            if not pod.spec.node_name or pod.status.phase != "Running":
                continue
            count = 0
            for container in pod.spec.containers or []:
                limits = (container.resources.limits or {}) if container.resources else {}
                count += int(float(limits.get(GPU_RESOURCE, 0) or 0))
            for _ in range(count):
                claimed.setdefault(pod.spec.node_name, []).append(
                    f"{pod.metadata.namespace}/{pod.metadata.name}"
                )

        devices: list[GPUDevice] = []
        for item in node_list.items:
            labels = item.metadata.labels or {}
            allocatable = item.status.allocatable or {}
            total = int(float(allocatable.get(GPU_RESOURCE, 0) or 0))
            if not total:
                continue
            model = labels.get(GFD_PRODUCT_LABEL, "UNKNOWN").replace("NVIDIA-", "")
            memory_mib = float(labels.get(GFD_MEMORY_LABEL, 0) or 0)
            node_claims = claimed.get(item.metadata.name, [])
            for index in range(total):
                workload = node_claims[index] if index < len(node_claims) else None
                devices.append(
                    GPUDevice(
                        id=f"{item.metadata.name}-gpu-{index}",
                        vendor="NVIDIA",
                        model=model.upper(),
                        memory_gb=int(memory_mib / 1024) if memory_mib else 0,
                        host=item.metadata.name,
                        status=GPUStatus.ALLOCATED if workload else GPUStatus.AVAILABLE,
                        allocation_type=GPUAllocationType.TIMESLICE,
                        platform=Platform.KUBERNETES,
                        workload=workload,
                    )
                )
        return devices

    async def get_running_workloads(self) -> list[dict[str, Any]]:
        core, apps = await asyncio.to_thread(self._clients)
        deployments = await asyncio.to_thread(apps.list_deployment_for_all_namespaces)
        out: list[dict[str, Any]] = []
        for item in deployments.items:
            gpu = 0
            for container in item.spec.template.spec.containers or []:
                limits = (container.resources.limits or {}) if container.resources else {}
                gpu += int(float(limits.get(GPU_RESOURCE, 0) or 0))
            out.append(
                {
                    "namespace": item.metadata.namespace,
                    "name": item.metadata.name,
                    "kind": "Deployment",
                    "replicas": item.status.ready_replicas or 0,
                    "gpu": gpu,
                }
            )
        return out

    async def get_persistent_storage(self) -> list[StoragePool]:
        core, _ = await asyncio.to_thread(self._clients)
        pvcs = await asyncio.to_thread(core.list_persistent_volume_claim_for_all_namespaces)
        by_class: dict[str, float] = {}
        for item in pvcs.items:
            storage_class = item.spec.storage_class_name or "default"
            size = (item.status.capacity or {}).get("storage")
            by_class[storage_class] = by_class.get(storage_class, 0.0) + self._mem_to_gb(size) / 1024
        return [
            StoragePool(
                name=name, backend=name, total_tb=0.0, available_tb=0.0, consumer="kubernetes"
            )
            for name in by_class
        ]

    async def get_cluster_capacity(self) -> PlatformInventory:
        nodes = await self.list_nodes()
        gpus = await self.get_gpu_resources()
        pools = await self.get_persistent_storage()
        return PlatformInventory(
            platform=Platform.KUBERNETES,
            available=True,
            name=self.settings.kubernetes_context or "kubernetes",
            nodes=nodes,
            cpu=Capacity(
                total=sum(n.cpu_total for n in nodes),
                available=sum(n.cpu_available for n in nodes),
            ),
            ram_gb=Capacity(
                total=sum(n.ram_gb_total for n in nodes),
                available=sum(n.ram_gb_available for n in nodes),
            ),
            gpu=GPUCapacity(
                total=len(gpus), available=sum(1 for g in gpus if g.available), devices=gpus
            ),
            storage=StorageCapacity(pools=pools),
            details={"gpu_resource_name": GPU_RESOURCE, "mode": "real"},
        )


def build_kubernetes_adapter(
    settings: Settings, infra: SimulatedInfrastructure | None
) -> KubernetesInventoryAdapter:
    if settings.kubernetes_simulated:
        if infra is None:
            raise AdapterNotConfigured("simulation mode requires the simulated datacenter")
        return SimulatedKubernetesAdapter(infra)
    return RealKubernetesAdapter(settings)
