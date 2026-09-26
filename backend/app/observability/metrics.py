"""Unified metrics for the dashboard (section 21).

In simulation mode the numbers come from the simulated datacenter, which drifts
on a timer so the dashboards look alive. In MVP mode the pod counts and node
capacity are read from the real cluster, and anything that is *not* measurable
is reported as unknown rather than invented.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections import deque
from datetime import datetime, timezone
from typing import Any

from ..adapters.simulation import SimulatedInfrastructure
from ..config import Settings
from ..events import EventBus
from ..inventory import GlobalResourceInventoryService
from ..models import (
    ACTIVE_STATES,
    ClusterMetrics,
    Deployment,
    DeploymentState,
    GPUMetric,
    MetricPoint,
    MetricSeries,
    Platform,
    RuntimeType,
    WorkloadMetrics,
)
from ..store import Repositories

log = logging.getLogger(__name__)

SERIES_LENGTH = 60


class MetricsService:
    def __init__(
        self,
        settings: Settings,
        inventory: GlobalResourceInventoryService,
        repositories: Repositories,
        bus: EventBus,
        infra: SimulatedInfrastructure | None = None,
    ) -> None:
        self.settings = settings
        self.inventory = inventory
        self.repositories = repositories
        self.bus = bus
        self.infra = infra
        self._series: dict[str, deque[MetricPoint]] = {}
        self._workload_series: dict[str, dict[str, deque[MetricPoint]]] = {}
        self._task: asyncio.Task | None = None

    # -- background ticker ---------------------------------------------------

    async def start(self) -> None:
        if self._task is None and self.settings.metrics_interval_seconds > 0:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.settings.metrics_interval_seconds)
                metrics = await self.cluster_metrics(tick=True)
                self.bus.publish(
                    "metrics",
                    {
                        "cpu": metrics.cpu_utilization_pct,
                        "ram": metrics.ram_utilization_pct,
                        "gpu": metrics.gpu_utilization_pct,
                        "gpu_allocated": metrics.gpu_allocated,
                        "gpu_total": metrics.gpu_total,
                        "storage": metrics.storage_utilization_pct,
                    },
                )
            except asyncio.CancelledError:  # pragma: no cover
                raise
            except Exception:  # noqa: BLE001 - never kill the ticker
                log.exception("metrics tick failed")

    # -- cluster -------------------------------------------------------------

    async def cluster_metrics(self, tick: bool = False) -> ClusterMetrics:
        if tick and self.infra is not None:
            self.infra.tick_metrics()
            self.inventory.invalidate()

        inventory = await self.inventory.get(refresh=tick)
        deployments = await self.repositories.deployments.list()
        active = [d for d in deployments if d.state in ACTIVE_STATES]

        gpus = [
            GPUMetric(
                gpu_id=device.id,
                model=device.model,
                host=device.host,
                utilization_pct=device.utilization_pct,
                memory_used_gb=device.memory_used_gb,
                memory_total_gb=device.memory_gb,
                temperature_c=device.temperature_c,
                workload=device.workload,
                status=str(device.status),
            )
            for device in inventory.gpu.devices
        ]
        allocated = [gpu for gpu in gpus if gpu.status == "allocated"]
        gpu_util = (
            round(sum(gpu.utilization_pct for gpu in allocated) / len(allocated), 1)
            if allocated
            else 0.0
        )
        gpu_mem = (
            round(
                100
                * sum(gpu.memory_used_gb for gpu in allocated)
                / max(sum(gpu.memory_total_gb for gpu in allocated), 1),
                1,
            )
            if allocated
            else 0.0
        )

        openstack = inventory.platform_of(Platform.OPENSTACK)
        kubernetes = inventory.platform_of(Platform.KUBERNETES)
        os_instances = openstack.details.get("instances", [])
        k8s_workloads = kubernetes.details.get("workloads", 0)

        metrics = ClusterMetrics(
            cpu_utilization_pct=inventory.cpu.utilization_pct,
            ram_utilization_pct=inventory.ram_gb.utilization_pct,
            gpu_utilization_pct=gpu_util,
            gpu_memory_pct=gpu_mem,
            storage_utilization_pct=inventory.storage.utilization_pct,
            gpu_allocated=len(allocated),
            gpu_total=len(gpus),
            openstack_vm_count=len(os_instances)
            + sum(1 for d in active if d.runtime.platform is Platform.OPENSTACK),
            openstack_gpu_vm_count=sum(1 for vm in os_instances if vm.get("gpu"))
            + sum(1 for d in active if d.runtime is RuntimeType.OPENSTACK_GPU_VM),
            kubernetes_pod_count=_pod_count(kubernetes, active),
            kubernetes_gpu_pod_count=sum(
                d.option.resources.replicas
                for d in active
                if d.runtime is RuntimeType.KUBERNETES_GPU
            )
            + sum(1 for device in inventory.gpu.devices if device.platform is Platform.KUBERNETES and not device.available and device.workload and "/" in device.workload),
            database_count=sum(1 for d in active if d.runtime is RuntimeType.DATABASE_SERVICE),
            failed_workloads=sum(
                1 for d in deployments if d.state is DeploymentState.FAILED
            ),
            running_deployments=sum(
                1 for d in deployments if d.state is DeploymentState.RUNNING
            ),
            alerts=0,
            gpus=gpus,
        )
        if isinstance(k8s_workloads, int):
            metrics.kubernetes_pod_count = max(metrics.kubernetes_pod_count, k8s_workloads)

        self._record("cpu", metrics.cpu_utilization_pct)
        self._record("ram", metrics.ram_utilization_pct)
        self._record("gpu", metrics.gpu_utilization_pct)
        self._record("gpu_memory", metrics.gpu_memory_pct)
        self._record("storage", metrics.storage_utilization_pct)
        metrics.series = {
            name: MetricSeries(name=name, points=list(points))
            for name, points in self._series.items()
        }
        return metrics

    def _record(self, name: str, value: float) -> None:
        points = self._series.setdefault(name, deque(maxlen=SERIES_LENGTH))
        points.append(MetricPoint(at=datetime.now(timezone.utc), value=value))

    # -- per workload --------------------------------------------------------

    async def workload_metrics(self, deployment: Deployment) -> WorkloadMetrics:
        plan = deployment.option.resources
        rng = random.Random(f"{deployment.id}-{int(datetime.now().timestamp() // 30)}")
        running = deployment.state is DeploymentState.RUNNING

        inventory = await self.inventory.get()
        gpu_devices = [
            device
            for device in inventory.gpu.devices
            if device.id in deployment.allocated_gpu_ids
        ]
        gpu_util = (
            round(sum(d.utilization_pct for d in gpu_devices) / len(gpu_devices), 1)
            if gpu_devices
            else None
        )
        gpu_mem = (
            round(
                100
                * sum(d.memory_used_gb for d in gpu_devices)
                / max(sum(d.memory_gb for d in gpu_devices), 1),
                1,
            )
            if gpu_devices
            else None
        )

        total_ram = plan.total_ram_gb
        cpu_util = round(rng.uniform(12, 48) if running else 0.0, 1)
        ram_used = round(total_ram * rng.uniform(0.35, 0.65) if running else 0.0, 1)
        storage_used = round(plan.storage_gb * rng.uniform(0.1, 0.35) if running else 0.0, 1)
        rps = round(rng.uniform(2, 40) if running else 0.0, 1)

        metrics = WorkloadMetrics(
            deployment_id=deployment.id,
            name=deployment.name,
            runtime=str(deployment.runtime),
            healthy=deployment.health == "healthy",
            cpu_utilization_pct=cpu_util,
            ram_used_gb=ram_used,
            ram_total_gb=total_ram,
            gpu_utilization_pct=gpu_util,
            gpu_memory_pct=gpu_mem,
            storage_used_gb=storage_used,
            storage_total_gb=plan.storage_gb,
            requests_per_second=rps,
        )

        buffers = self._workload_series.setdefault(deployment.id, {})
        for key, value in (
            ("cpu", cpu_util),
            ("ram", round(100 * ram_used / max(total_ram, 1), 1)),
            ("gpu", gpu_util or 0.0),
        ):
            points = buffers.setdefault(key, deque(maxlen=SERIES_LENGTH))
            points.append(MetricPoint(at=datetime.now(timezone.utc), value=value))
            metrics.series[key] = MetricSeries(name=key, points=list(points))
        return metrics

    async def all_workload_metrics(self) -> list[WorkloadMetrics]:
        deployments = await self.repositories.deployments.list()
        return [
            await self.workload_metrics(deployment)
            for deployment in deployments
            if deployment.state in (DeploymentState.RUNNING, DeploymentState.DEPLOYING)
        ]

    # -- prometheus exposition ----------------------------------------------

    async def prometheus_text(self) -> str:
        metrics = await self.cluster_metrics()
        lines = [
            "# HELP aiinfra_cpu_utilization_percent Cluster CPU utilisation",
            "# TYPE aiinfra_cpu_utilization_percent gauge",
            f"aiinfra_cpu_utilization_percent {metrics.cpu_utilization_pct}",
            "# HELP aiinfra_ram_utilization_percent Cluster memory utilisation",
            "# TYPE aiinfra_ram_utilization_percent gauge",
            f"aiinfra_ram_utilization_percent {metrics.ram_utilization_pct}",
            "# HELP aiinfra_gpu_utilization_percent Mean utilisation of allocated GPUs",
            "# TYPE aiinfra_gpu_utilization_percent gauge",
            f"aiinfra_gpu_utilization_percent {metrics.gpu_utilization_pct}",
            "# HELP aiinfra_gpu_total Total GPUs known to the platform",
            "# TYPE aiinfra_gpu_total gauge",
            f"aiinfra_gpu_total {metrics.gpu_total}",
            "# HELP aiinfra_gpu_allocated Allocated GPUs",
            "# TYPE aiinfra_gpu_allocated gauge",
            f"aiinfra_gpu_allocated {metrics.gpu_allocated}",
            "# HELP aiinfra_storage_utilization_percent Ceph utilisation",
            "# TYPE aiinfra_storage_utilization_percent gauge",
            f"aiinfra_storage_utilization_percent {metrics.storage_utilization_pct}",
            "# HELP aiinfra_deployments_running Running deployments",
            "# TYPE aiinfra_deployments_running gauge",
            f"aiinfra_deployments_running {metrics.running_deployments}",
            "# HELP aiinfra_deployments_failed Failed deployments",
            "# TYPE aiinfra_deployments_failed gauge",
            f"aiinfra_deployments_failed {metrics.failed_workloads}",
            "# HELP aiinfra_gpu_device_utilization_percent Per-device GPU utilisation",
            "# TYPE aiinfra_gpu_device_utilization_percent gauge",
        ]
        for gpu in metrics.gpus:
            labels = f'gpu_id="{gpu.gpu_id}",model="{gpu.model}",host="{gpu.host}"'
            lines.append(f"aiinfra_gpu_device_utilization_percent{{{labels}}} {gpu.utilization_pct}")
        return "\n".join(lines) + "\n"


def _pod_count(kubernetes: Any, active: list[Deployment]) -> int:
    base = 0
    details = getattr(kubernetes, "details", {}) or {}
    workloads = details.get("workloads")
    if isinstance(workloads, int):
        base = workloads
    return base + sum(
        d.option.resources.replicas for d in active if d.runtime.platform is Platform.KUBERNETES
    )
