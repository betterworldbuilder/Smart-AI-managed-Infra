"""The simulated datacenter.

`SimulatedInfrastructure` owns the mutable state that the Kubernetes, OpenStack
and Ceph adapters read from in SIMULATION_MODE. It is intentionally the *only*
place where simulated state is mutated, so "what happens when we deploy" has a
single, testable implementation.

Nothing here hard-codes a GPU model, host name or capacity -- it is all read
from `simulation/resources.yaml`.
"""

from __future__ import annotations

import random
import threading
from pathlib import Path
from typing import Any

import yaml

from ..models import (
    ComputeNode,
    GPUAllocationType,
    GPUDevice,
    GPUStatus,
    Platform,
    StoragePool,
)


class GPUModelCatalog:
    """Vendor/memory/cost facts about GPU models, loaded from YAML."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self._by_model: dict[str, dict[str, Any]] = {
            str(entry["model"]).upper(): entry for entry in entries
        }

    def get(self, model: str) -> dict[str, Any]:
        return self._by_model.get(
            model.upper(),
            {
                "model": model,
                "vendor": "unknown",
                "memory_gb": 0,
                "relative_cost": 1.0,
                "perf_tier": 1,
                "supported_allocation_types": ["passthrough"],
            },
        )

    def models(self) -> list[dict[str, Any]]:
        return list(self._by_model.values())

    def memory_gb(self, model: str) -> int:
        return int(self.get(model).get("memory_gb", 0))

    def relative_cost(self, model: str) -> float:
        return float(self.get(model).get("relative_cost", 1.0))

    def perf_tier(self, model: str) -> int:
        return int(self.get(model).get("perf_tier", 1))

    def pci_ids(self, model: str) -> tuple[str | None, str | None]:
        entry = self.get(model)
        return entry.get("pci_vendor_id"), entry.get("pci_device_id")


class SimulatedInfrastructure:
    """Mutable in-memory model of the whole fake datacenter."""

    def __init__(self, resources_file: Path, seed: int = 1337) -> None:
        self.resources_file = Path(resources_file)
        self.raw: dict[str, Any] = yaml.safe_load(self.resources_file.read_text(encoding="utf-8"))
        self.catalog = GPUModelCatalog(self.raw.get("gpu_models", []))
        self._rng = random.Random(seed)
        self._lock = threading.RLock()
        self.gpus: dict[str, GPUDevice] = {}
        self.k8s_nodes: dict[str, ComputeNode] = {}
        self.os_hosts: dict[str, ComputeNode] = {}
        self.ceph_pools: list[StoragePool] = []
        self.k8s_workloads: list[dict[str, Any]] = []
        self.os_instances: list[dict[str, Any]] = []
        self._build()

    # -- construction --------------------------------------------------------

    def _build(self) -> None:
        counter = 0

        def new_gpu(spec: dict[str, Any], host: str, platform: Platform) -> str:
            nonlocal counter
            counter += 1
            gpu_id = f"gpu-{counter:03d}"
            model = str(spec["model"]).upper()
            catalog = self.catalog.get(model)
            status = GPUStatus(spec.get("status", "available"))
            allocation = GPUAllocationType(
                spec.get(
                    "allocation_type",
                    "passthrough" if platform is Platform.OPENSTACK else "timeslice",
                )
            )
            memory_gb = int(catalog.get("memory_gb", 0))
            device = GPUDevice(
                id=gpu_id,
                vendor=str(catalog.get("vendor", "NVIDIA")),
                model=model,
                memory_gb=memory_gb,
                host=host,
                pci_address=spec.get("pci_address"),
                status=status,
                allocation_type=allocation,
                platform=platform,
                workload=spec.get("workload"),
                relative_cost=float(catalog.get("relative_cost", 1.0)),
                perf_tier=int(catalog.get("perf_tier", 1)),
            )
            if status is GPUStatus.ALLOCATED:
                device.utilization_pct = round(self._rng.uniform(35, 85), 1)
                device.memory_used_gb = round(memory_gb * self._rng.uniform(0.35, 0.8), 1)
                device.temperature_c = round(self._rng.uniform(52, 74), 1)
            else:
                device.temperature_c = round(self._rng.uniform(28, 38), 1)
            self.gpus[gpu_id] = device
            return gpu_id

        # Kubernetes
        k8s = self.raw.get("kubernetes", {})
        for node in k8s.get("nodes", []):
            gpu_ids = [new_gpu(g, node["name"], Platform.KUBERNETES) for g in node.get("gpus", [])]
            self.k8s_nodes[node["name"]] = ComputeNode(
                name=node["name"],
                platform=Platform.KUBERNETES,
                cpu_total=int(node["cpu_total"]),
                cpu_available=int(node["cpu_total"]) - int(node.get("cpu_allocated", 0)),
                ram_gb_total=int(node["ram_gb_total"]),
                ram_gb_available=int(node["ram_gb_total"]) - int(node.get("ram_gb_allocated", 0)),
                gpu_ids=gpu_ids,
                labels=dict(node.get("labels", {})),
                status="ready",
            )
        self.k8s_workloads = list(k8s.get("workloads", []))

        # OpenStack
        openstack = self.raw.get("openstack", {})
        for host in openstack.get("compute_hosts", []):
            gpu_ids = [new_gpu(g, host["name"], Platform.OPENSTACK) for g in host.get("gpus", [])]
            self.os_hosts[host["name"]] = ComputeNode(
                name=host["name"],
                platform=Platform.OPENSTACK,
                cpu_total=int(host["vcpu_total"]),
                cpu_available=int(host["vcpu_total"]) - int(host.get("vcpu_allocated", 0)),
                ram_gb_total=int(host["ram_gb_total"]),
                ram_gb_available=int(host["ram_gb_total"]) - int(host.get("ram_gb_allocated", 0)),
                disk_gb_total=int(host.get("disk_gb_total", 0)),
                disk_gb_available=int(host.get("disk_gb_total", 0))
                - int(host.get("disk_gb_allocated", 0)),
                gpu_ids=gpu_ids,
                availability_zone=host.get("availability_zone"),
                iommu=host.get("iommu"),
                status="up",
            )
        self.os_instances = list(openstack.get("instances", []))

        # Ceph
        ceph = self.raw.get("ceph", {})
        self.ceph_pools = [StoragePool(**pool) for pool in ceph.get("pools", [])]

    # -- read helpers --------------------------------------------------------

    @property
    def kubernetes_raw(self) -> dict[str, Any]:
        return self.raw.get("kubernetes", {})

    @property
    def openstack_raw(self) -> dict[str, Any]:
        return self.raw.get("openstack", {})

    @property
    def ceph_raw(self) -> dict[str, Any]:
        return self.raw.get("ceph", {})

    @property
    def opencenter_raw(self) -> dict[str, Any]:
        return self.raw.get("opencenter", {})

    def gpus_for(self, platform: Platform) -> list[GPUDevice]:
        return [g for g in self.gpus.values() if g.platform is platform]

    def nodes_for(self, platform: Platform) -> list[ComputeNode]:
        return list(
            (self.k8s_nodes if platform is Platform.KUBERNETES else self.os_hosts).values()
        )

    def ceph_capacity(self) -> tuple[float, float]:
        ceph = self.ceph_raw
        total = float(ceph.get("total_tb", 0))
        available = float(ceph.get("available_tb", 0))
        return total, available

    # -- allocation ----------------------------------------------------------

    def find_placement(
        self,
        platform: Platform,
        vcpu: int,
        ram_gb: int,
        gpu_count: int = 0,
        gpu_model: str | None = None,
    ) -> tuple[ComputeNode, list[GPUDevice]] | None:
        """Emulate the *native* scheduler (kube-scheduler / nova-scheduler).

        The Governor never calls this to make a recommendation -- only the
        deployment pipeline calls it, at the moment the platform would really
        schedule. That keeps the "native scheduler decides placement" boundary
        honest, including in simulation.
        """
        with self._lock:
            nodes = self.nodes_for(platform)
            # Pack onto the node with the least spare capacity that still fits
            # (bin-packing, like a real scheduler's NodeResourcesFit + MostAllocated).
            candidates: list[tuple[ComputeNode, list[GPUDevice]]] = []
            for node in nodes:
                if node.cpu_available < vcpu or node.ram_gb_available < ram_gb:
                    continue
                gpus: list[GPUDevice] = []
                if gpu_count:
                    free = [
                        self.gpus[gid]
                        for gid in node.gpu_ids
                        if self.gpus[gid].available
                        and (gpu_model is None or self.gpus[gid].model == gpu_model.upper())
                    ]
                    if len(free) < gpu_count:
                        continue
                    gpus = free[:gpu_count]
                candidates.append((node, gpus))
            if not candidates:
                return None
            candidates.sort(key=lambda item: (item[0].cpu_available, item[0].ram_gb_available))
            return candidates[0]

    def allocate(
        self,
        *,
        platform: Platform,
        vcpu: int,
        ram_gb: int,
        storage_gb: int = 0,
        gpu_count: int = 0,
        gpu_model: str | None = None,
        workload: str = "workload",
        allocation_type: GPUAllocationType | None = None,
    ) -> tuple[ComputeNode, list[GPUDevice]] | None:
        """Consume capacity. Returns the node and GPUs the scheduler picked."""
        placement = self.find_placement(platform, vcpu, ram_gb, gpu_count, gpu_model)
        if placement is None:
            return None
        node, gpus = placement
        with self._lock:
            node.cpu_available -= vcpu
            node.ram_gb_available -= ram_gb
            if node.disk_gb_total:
                node.disk_gb_available = max(node.disk_gb_available - storage_gb, 0)
            for gpu in gpus:
                gpu.status = GPUStatus.ALLOCATED
                gpu.workload = workload
                if allocation_type is not None:
                    gpu.allocation_type = allocation_type
                gpu.utilization_pct = round(self._rng.uniform(15, 45), 1)
                gpu.memory_used_gb = round(gpu.memory_gb * self._rng.uniform(0.2, 0.45), 1)
                gpu.temperature_c = round(self._rng.uniform(48, 66), 1)
            self._consume_storage(platform, storage_gb)
        return node, gpus

    def release(
        self,
        *,
        node_name: str,
        platform: Platform,
        vcpu: int,
        ram_gb: int,
        storage_gb: int = 0,
        gpu_ids: list[str] | None = None,
    ) -> None:
        with self._lock:
            nodes = self.k8s_nodes if platform is Platform.KUBERNETES else self.os_hosts
            node = nodes.get(node_name)
            if node is not None:
                node.cpu_available = min(node.cpu_total, node.cpu_available + vcpu)
                node.ram_gb_available = min(node.ram_gb_total, node.ram_gb_available + ram_gb)
                if node.disk_gb_total:
                    node.disk_gb_available = min(
                        node.disk_gb_total, node.disk_gb_available + storage_gb
                    )
            for gpu_id in gpu_ids or []:
                gpu = self.gpus.get(gpu_id)
                if gpu is None:
                    continue
                gpu.status = GPUStatus.AVAILABLE
                gpu.workload = None
                gpu.utilization_pct = 0.0
                gpu.memory_used_gb = 0.0
                gpu.temperature_c = round(self._rng.uniform(28, 38), 1)
            self._consume_storage(platform, -storage_gb)

    def _consume_storage(self, platform: Platform, storage_gb: float) -> None:
        """Charge storage to the Ceph pool backing that platform."""
        if not storage_gb:
            return
        consumer = "kubernetes" if platform is Platform.KUBERNETES else "openstack"
        pool = next((p for p in self.ceph_pools if p.consumer == consumer), None)
        if pool is None and self.ceph_pools:
            pool = self.ceph_pools[0]
        if pool is None:
            return
        delta_tb = storage_gb / 1024.0
        pool.available_tb = max(0.0, min(pool.total_tb, round(pool.available_tb - delta_tb, 4)))
        ceph = self.raw.setdefault("ceph", {})
        ceph["available_tb"] = round(sum(p.available_tb for p in self.ceph_pools), 4)

    # -- metrics drift -------------------------------------------------------

    def tick_metrics(self) -> None:
        """Nudge utilisation figures so the dashboards look alive."""
        with self._lock:
            for gpu in self.gpus.values():
                if gpu.status is not GPUStatus.ALLOCATED:
                    gpu.utilization_pct = 0.0
                    gpu.memory_used_gb = 0.0
                    continue
                drift = self._rng.uniform(-6, 6)
                gpu.utilization_pct = round(min(99.0, max(3.0, gpu.utilization_pct + drift)), 1)
                target_mem = gpu.memory_gb * (0.2 + gpu.utilization_pct / 200.0)
                gpu.memory_used_gb = round(
                    min(float(gpu.memory_gb), max(0.5, target_mem + self._rng.uniform(-1, 1))), 1
                )
                base_temp = 40 + gpu.utilization_pct * 0.45
                gpu.temperature_c = round(base_temp + self._rng.uniform(-2, 2), 1)

    def set_gpu_health(self, gpu_id: str, status: GPUStatus) -> None:
        gpu = self.gpus.get(gpu_id)
        if gpu:
            gpu.status = status
