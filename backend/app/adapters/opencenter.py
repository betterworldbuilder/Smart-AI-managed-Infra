"""openCenter adapter (sections 3 and 16).

openCenter is the deterministic deployment system. The Copilot never runs
commands against infrastructure: it produces a `DeploymentSpec`, a human
approves it, and *this* adapter is the only thing that turns that spec into
configuration and hands it to openCenter.

Two modes, selected with `OPEN_CENTER_MODE`:

* `mock` -- implements the complete workflow locally (generate -> validate ->
  plan -> apply -> status -> rollback) against the simulated datacenter.
* `real` -- pushes generated configuration to the GitOps repository and drives
  the openCenter API, letting Flux/OpenTofu/Kubespray do the work.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

from ..config import Settings
from ..deployments.artifacts import generate_artifacts, plan_changes
from ..models import (
    Deployment,
    DeploymentArtifact,
    DeploymentPlan,
    DeploymentSpec,
    DeploymentState,
    DeploymentStatusReport,
    GPUAllocationType,
    Placement,
    RuntimeType,
    ValidationIssue,
)
from .base import AdapterNotConfigured
from .deployment_backend import DeploymentBackend, RunStep
from .simulation import SimulatedInfrastructure

log = logging.getLogger(__name__)


class OpenCenterAdapter(DeploymentBackend):
    """Deployment backend that speaks openCenter (mock or real)."""

    name = "opencenter"


class MockOpenCenterAdapter(OpenCenterAdapter):
    """Complete workflow without a real openCenter installation."""

    simulated = True
    engine_label = "Mock openCenter (simulated GitOps)"
    opencenter_mode = "mock"

    def __init__(self, settings: Settings, infra: SimulatedInfrastructure) -> None:
        self.settings = settings
        self.infra = infra
        self._status: dict[str, DeploymentStatusReport] = {}
        self._repo: dict[str, str] = {}  # simulated GitOps repository
        self._commits: list[dict[str, Any]] = []
        self._step_delay = max(settings.deploy_step_seconds, 0.0)

    # -- clusters ------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        raw = self.infra.opencenter_raw
        return {
            "adapter": self.name,
            "simulated": True,
            "mode": "mock",
            "reachable": True,
            "version": raw.get("version"),
            "endpoint": raw.get("endpoint"),
            "clusters": len(raw.get("clusters", [])),
        }

    async def get_clusters(self) -> list[dict[str, Any]]:
        return list(self.infra.opencenter_raw.get("clusters", []))

    async def get_cluster_resources(self, cluster_id: str) -> dict[str, Any]:
        clusters = await self.get_clusters()
        cluster = next((c for c in clusters if c["id"] == cluster_id), None)
        if cluster is None:
            raise KeyError(f"unknown openCenter cluster '{cluster_id}'")
        from ..models import Platform

        platform = (
            Platform.OPENSTACK if cluster.get("type") == "openstack" else Platform.KUBERNETES
        )
        nodes = self.infra.nodes_for(platform)
        gpus = self.infra.gpus_for(platform)
        return {
            "cluster": cluster,
            "nodes": [node.model_dump() for node in nodes],
            "cpu": {
                "total": sum(n.cpu_total for n in nodes),
                "available": sum(n.cpu_available for n in nodes),
            },
            "ram_gb": {
                "total": sum(n.ram_gb_total for n in nodes),
                "available": sum(n.ram_gb_available for n in nodes),
            },
            "gpu": {
                "total": len(gpus),
                "available": sum(1 for g in gpus if g.available),
            },
        }

    # -- generate / validate / plan -----------------------------------------

    async def generate_deployment(self, spec: DeploymentSpec) -> list[DeploymentArtifact]:
        runtime = _runtime_of(spec)
        cluster = self.cluster_for(runtime, await self.get_clusters())
        artifacts = generate_artifacts(spec, cluster, runtime)
        artifacts.append(
            DeploymentArtifact(
                path=f"deployment-specs/{spec.metadata.name}.yaml",
                content=spec.to_yaml(),
                description="The approved vendor-neutral WorkloadDeployment",
            )
        )
        return artifacts

    async def validate_deployment(self, spec: DeploymentSpec) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        runtime = _runtime_of(spec)
        name = spec.metadata.name

        if not name or not name.replace("-", "").isalnum():
            issues.append(
                ValidationIssue(
                    severity="error", code="INVALID_NAME", message=f"'{name}' is not DNS-1123 safe"
                )
            )

        from ..models import Platform

        platform = runtime.platform
        vcpu = spec.compute.cpu * spec.compute.replicas
        ram_gb = _memory_gb(spec.compute.memory) * spec.compute.replicas
        gpu_count = (spec.gpu.count_per_replica * spec.compute.replicas) if spec.gpu else 0

        placement = self.infra.find_placement(
            platform,
            spec.compute.cpu,
            _memory_gb(spec.compute.memory),
            spec.gpu.count_per_replica if spec.gpu else 0,
            spec.gpu.model if spec.gpu else None,
        )
        if placement is None:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="NO_CAPACITY",
                    message=(
                        f"No {platform} host can satisfy {spec.compute.cpu} vCPU / "
                        f"{spec.compute.memory}"
                        + (f" / {spec.gpu.count_per_replica}x {spec.gpu.model}" if spec.gpu else "")
                    ),
                )
            )

        if spec.gpu is not None:
            free = [
                g
                for g in self.infra.gpus_for(platform)
                if g.available and g.model == spec.gpu.model.upper()
            ]
            if len(free) < gpu_count:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="GPU_UNAVAILABLE",
                        message=f"{gpu_count}x {spec.gpu.model} requested, {len(free)} free",
                    )
                )
            if platform is Platform.OPENSTACK:
                hosts_without_iommu = [
                    g.host
                    for g in free
                    if self.infra.os_hosts.get(g.host) is not None
                    and self.infra.os_hosts[g.host].iommu != "enabled"
                ]
                if hosts_without_iommu:
                    issues.append(
                        ValidationIssue(
                            severity="warning",
                            code="IOMMU_UNKNOWN",
                            message=f"IOMMU not reported as enabled on {sorted(set(hosts_without_iommu))}",
                        )
                    )

        storage_gb = _storage_gb(spec.storage.size)
        _, ceph_free_tb = self.infra.ceph_capacity()
        free_tb = sum(p.available_tb for p in self.infra.ceph_pools)
        if storage_gb / 1024 > max(free_tb, ceph_free_tb):
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="STORAGE_UNAVAILABLE",
                    message=f"{storage_gb} GB requested, {round(free_tb * 1024)} GB free",
                )
            )

        if spec.security.classification in ("confidential", "restricted"):
            if spec.network.exposure == "public":
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="EXPOSURE_VIOLATION",
                        message=f"{spec.security.classification} data cannot be publicly exposed",
                    )
                )
            if not spec.network.network_policy and runtime.is_container:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="MISSING_NETWORK_POLICY",
                        message="Classified workloads require a NetworkPolicy",
                    )
                )

        if spec.availability.high_availability and spec.compute.replicas < 2:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="HA_REPLICAS",
                    message="highAvailability=true requires at least 2 replicas",
                )
            )

        if runtime.platform.value == "openstack":
            images = {img["name"] for img in self.infra.openstack_raw.get("images", [])}
            if spec.compute.image and spec.compute.image not in images:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="IMAGE_NOT_FOUND",
                        message=f"image '{spec.compute.image}' is not in Glance",
                    )
                )

        issues.append(
            ValidationIssue(
                severity="info",
                code="CAPACITY_SUMMARY",
                message=f"requires {vcpu} vCPU, {ram_gb} GB RAM, {gpu_count} GPU, {storage_gb} GB storage",
            )
        )
        return issues

    async def plan_deployment(self, spec: DeploymentSpec, deployment_id: str) -> DeploymentPlan:
        runtime = _runtime_of(spec)
        cluster = self.cluster_for(runtime, await self.get_clusters())
        artifacts = await self.generate_deployment(spec)
        issues = await self.validate_deployment(spec)
        changes = plan_changes(spec, runtime)
        return DeploymentPlan(
            deployment_id=deployment_id,
            cluster_id=cluster.get("id", "unknown"),
            changes=changes,
            artifacts=artifacts,
            issues=issues,
            valid=not any(i.severity == "error" for i in issues),
            estimated_duration_seconds=90 if runtime.platform.value == "openstack" else 60,
            gitops={
                "repo": cluster.get("repo", self.settings.open_center_gitops_repo),
                "branch": cluster.get("branch", self.settings.open_center_gitops_branch),
                "path": cluster.get("path", "clusters"),
                "engine": cluster.get("gitops", "fluxcd"),
                "provisioner": cluster.get("provisioner", "kustomize"),
            },
        )

    # -- apply ---------------------------------------------------------------

    async def apply_deployment(
        self, spec: DeploymentSpec, deployment: Deployment
    ) -> AsyncIterator[RunStep]:
        """Run the simulated openCenter pipeline, streaming progress."""
        runtime = deployment.runtime
        plan = deployment.plan
        cluster = self.cluster_for(runtime, await self.get_clusters())
        gitops_engine = cluster.get("gitops", "fluxcd")
        provisioner = cluster.get("provisioner", "kustomize")

        yield RunStep(
            key="accepted",
            title="openCenter accepted the deployment",
            message=f"run created for cluster {cluster.get('name')}",
            progress=5,
            data={"cluster": cluster.get("name"), "run_id": deployment.opencenter_run_id},
        )
        await self._pause()

        # 1. Commit generated configuration to the GitOps repository.
        for artifact in (plan.artifacts if plan else []):
            self._repo[artifact.path] = artifact.content
        commit = {
            "sha": f"{abs(hash(deployment.id)) % 0xFFFFFFF:07x}",
            "message": f"feat(aiinfra): deploy {spec.metadata.name} [{runtime}]",
            "files": [a.path for a in (plan.artifacts if plan else [])],
            "branch": cluster.get("branch", "main"),
        }
        self._commits.append(commit)
        yield RunStep(
            key="gitops_commit",
            title="Configuration committed to GitOps",
            message=f"{commit['sha']} -- {len(commit['files'])} files on {commit['branch']}",
            progress=20,
            data=commit,
        )
        await self._pause()

        # 2. Reconciliation.
        if runtime.platform.value == "openstack":
            yield RunStep(
                key="reconcile",
                title=f"{gitops_engine} triggered {provisioner}",
                message="tofu init && tofu apply -auto-approve",
                progress=35,
            )
        else:
            yield RunStep(
                key="reconcile",
                title="FluxCD reconciliation",
                message="Kustomization applied to the cluster",
                progress=35,
            )
        await self._pause()

        # 3. The *native* scheduler picks a host per replica. Not us.
        vcpu_each = spec.compute.cpu
        ram_each = _memory_gb(spec.compute.memory)
        storage_total = _storage_gb(spec.storage.size)
        gpu_each = spec.gpu.count_per_replica if spec.gpu else 0
        allocations: list[dict[str, Any]] = []
        nodes: list[str] = []
        gpu_ids: list[str] = []

        for replica in range(spec.compute.replicas):
            allocation = self.infra.allocate(
                platform=runtime.platform,
                vcpu=vcpu_each,
                ram_gb=ram_each,
                # The volume is charged once, not once per replica.
                storage_gb=storage_total if replica == 0 else 0,
                gpu_count=gpu_each,
                gpu_model=spec.gpu.model if spec.gpu else None,
                workload=f"{spec.metadata.name}-{replica + 1}",
                allocation_type=(
                    GPUAllocationType(spec.gpu.allocation_type) if spec.gpu else None
                ),
            )
            if allocation is None:
                # Give back whatever this run already took: a failed deployment
                # must not leak capacity.
                self._release(runtime, allocations)
                yield RunStep(
                    key="schedule",
                    title=f"{runtime.native_scheduler} could not place replica "
                    f"{replica + 1}/{spec.compute.replicas}",
                    message="insufficient capacity at apply time",
                    progress=40,
                    level="error",
                    failed=True,
                )
                self._status[deployment.id] = DeploymentStatusReport(
                    deployment_id=deployment.id,
                    state=DeploymentState.FAILED,
                    progress=40,
                    health="failed",
                    message="no capacity",
                )
                return
            node, gpus = allocation
            nodes.append(node.name)
            gpu_ids.extend(gpu.id for gpu in gpus)
            allocations.append(
                {
                    "node": node.name,
                    "vcpu": vcpu_each,
                    "ram_gb": ram_each,
                    "storage_gb": storage_total if replica == 0 else 0,
                    "gpu_ids": [gpu.id for gpu in gpus],
                }
            )

        placement = Placement(
            scheduler=runtime.native_scheduler,
            node=nodes[0],
            nodes=nodes,
            platform=str(runtime.platform),
            gpu_ids=gpu_ids,
            allocations=allocations,
            reason="selected by the platform scheduler based on live capacity",
        )
        yield RunStep(
            key="schedule",
            title=f"{runtime.native_scheduler} selected {', '.join(dict.fromkeys(nodes))}",
            message=(
                f"GPUs {', '.join(gpu_ids)} ({spec.gpu.model})"
                if gpu_ids and spec.gpu
                else "CPU-only placement"
            ),
            progress=55,
            data={"placement": placement.model_dump()},
        )
        await self._pause()

        yield RunStep(
            key="resources",
            title="Resources allocated",
            message=f"{spec.compute.cpu * spec.compute.replicas} vCPU, "
            f"{_memory_gb(spec.compute.memory) * spec.compute.replicas} GB RAM, "
            f"{spec.storage.size} {spec.storage.backend}",
            progress=70,
            data={"placement": placement.model_dump()},
        )
        await self._pause()

        yield RunStep(
            key="starting",
            title=(
                "Instances booting"
                if runtime.platform.value == "openstack"
                else "Pods starting"
            ),
            message=f"{spec.compute.replicas} replica(s)",
            progress=85,
        )
        await self._pause()

        yield RunStep(
            key="healthy",
            title="Health checks passed",
            message="workload is READY",
            progress=100,
            level="success",
            data={"placement": placement.model_dump()},
        )
        self._status[deployment.id] = DeploymentStatusReport(
            deployment_id=deployment.id,
            state=DeploymentState.RUNNING,
            progress=100,
            health="healthy",
            message="running",
            placement=placement,
        )

    async def deployment_status(self, deployment_id: str) -> DeploymentStatusReport:
        report = self._status.get(deployment_id)
        if report is None:
            return DeploymentStatusReport(
                deployment_id=deployment_id,
                state=DeploymentState.DRAFT,
                message="openCenter has no run for this deployment yet",
            )
        return report

    async def rollback_deployment(self, deployment: Deployment) -> AsyncIterator[RunStep]:
        spec = deployment.spec
        yield RunStep(key="rollback_start", title="Rollback requested", progress=10)
        await self._pause()

        for path in list(self._repo):
            if deployment.name in path:
                self._repo.pop(path, None)
        self._commits.append(
            {
                "sha": f"{abs(hash(deployment.id + 'revert')) % 0xFFFFFFF:07x}",
                "message": f"revert(aiinfra): remove {deployment.name}",
                "files": [],
                "branch": self.settings.open_center_gitops_branch,
            }
        )
        yield RunStep(
            key="rollback_commit",
            title="GitOps revert committed",
            message="Flux will prune the removed resources",
            progress=45,
        )
        await self._pause()

        if spec is not None and deployment.placement is not None:
            self._release(deployment.runtime, deployment.placement.allocations)
        yield RunStep(
            key="rollback_released",
            title="Resources released",
            message=(
                f"{len(deployment.allocated_gpu_ids)} GPU(s) returned to the pool"
                if deployment.allocated_gpu_ids
                else "capacity returned to the pool"
            ),
            progress=90,
        )
        await self._pause()

        yield RunStep(
            key="rollback_done", title="Rollback complete", progress=100, level="success"
        )
        self._status[deployment.id] = DeploymentStatusReport(
            deployment_id=deployment.id,
            state=DeploymentState.ROLLED_BACK,
            progress=100,
            health="rolled_back",
            message="rolled back",
        )

    def _release(self, runtime: RuntimeType, allocations: list[dict[str, Any]]) -> None:
        """Return per-replica capacity to the simulated datacenter."""
        for allocation in allocations:
            self.infra.release(
                node_name=str(allocation.get("node", "")),
                platform=runtime.platform,
                vcpu=int(allocation.get("vcpu", 0)),
                ram_gb=int(allocation.get("ram_gb", 0)),
                storage_gb=int(allocation.get("storage_gb", 0)),
                gpu_ids=list(allocation.get("gpu_ids", [])),
            )

    # -- introspection used by the UI ---------------------------------------

    def gitops_repository(self) -> dict[str, Any]:
        return {
            "repo": self.settings.open_center_gitops_repo,
            "branch": self.settings.open_center_gitops_branch,
            "files": sorted(self._repo),
            "commits": list(reversed(self._commits))[:25],
        }

    def gitops_file(self, path: str) -> str | None:
        return self._repo.get(path)

    async def _pause(self) -> None:
        if self._step_delay:
            await asyncio.sleep(self._step_delay)


class RealOpenCenterAdapter(OpenCenterAdapter):
    """Drives a real openCenter installation (Phase 3).

    The integration surface is deliberately small and documented in
    `docs/opencenter-integration.md`:

    1. configuration is generated locally (identical to mock mode),
    2. it is committed to the GitOps repository openCenter watches,
    3. openCenter's API is asked to validate / plan / apply the change,
    4. run status is polled until the workflow terminates.
    """

    simulated = False
    engine_label = "openCenter"
    opencenter_mode = "real"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not settings.open_center_token:
            log.warning("OPEN_CENTER_TOKEN is empty -- real openCenter calls will fail")

    def _client(self):
        import httpx

        if not self.settings.open_center_url:
            raise AdapterNotConfigured("OPEN_CENTER_URL is not set")
        return httpx.AsyncClient(
            base_url=self.settings.open_center_url.rstrip("/"),
            headers={"Authorization": f"Bearer {self.settings.open_center_token}"},
            timeout=60.0,
        )

    async def health(self) -> dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client.get("/api/v1/health")
                response.raise_for_status()
                return {
                    "adapter": self.name,
                    "simulated": False,
                    "mode": "real",
                    "reachable": True,
                    **response.json(),
                }
        except Exception as exc:  # noqa: BLE001
            return {
                "adapter": self.name,
                "simulated": False,
                "mode": "real",
                "reachable": False,
                "error": str(exc),
            }

    async def get_clusters(self) -> list[dict[str, Any]]:
        async with self._client() as client:
            response = await client.get("/api/v1/clusters")
            response.raise_for_status()
            return response.json().get("items", [])

    async def get_cluster_resources(self, cluster_id: str) -> dict[str, Any]:
        async with self._client() as client:
            response = await client.get(f"/api/v1/clusters/{cluster_id}/resources")
            response.raise_for_status()
            return response.json()

    async def generate_deployment(self, spec: DeploymentSpec) -> list[DeploymentArtifact]:
        runtime = _runtime_of(spec)
        cluster = self.cluster_for(runtime, await self.get_clusters())
        artifacts = generate_artifacts(spec, cluster, runtime)
        artifacts.append(
            DeploymentArtifact(
                path=f"deployment-specs/{spec.metadata.name}.yaml", content=spec.to_yaml()
            )
        )
        return artifacts

    async def validate_deployment(self, spec: DeploymentSpec) -> list[ValidationIssue]:
        async with self._client() as client:
            response = await client.post("/api/v1/deployments/validate", json=spec.to_wire())
            response.raise_for_status()
            return [ValidationIssue(**issue) for issue in response.json().get("issues", [])]

    async def plan_deployment(self, spec: DeploymentSpec, deployment_id: str) -> DeploymentPlan:
        runtime = _runtime_of(spec)
        cluster = self.cluster_for(runtime, await self.get_clusters())
        artifacts = await self.generate_deployment(spec)
        async with self._client() as client:
            response = await client.post(
                "/api/v1/deployments/plan",
                json={"spec": spec.to_wire(), "cluster": cluster.get("id")},
            )
            response.raise_for_status()
            body = response.json()
        from ..models import PlanChange

        changes = [PlanChange(**change) for change in body.get("changes", [])] or plan_changes(
            spec, runtime
        )
        issues = [ValidationIssue(**issue) for issue in body.get("issues", [])]
        return DeploymentPlan(
            deployment_id=deployment_id,
            cluster_id=cluster.get("id", "unknown"),
            changes=changes,
            artifacts=artifacts,
            issues=issues,
            valid=not any(i.severity == "error" for i in issues),
            gitops=body.get("gitops", {}),
        )

    async def apply_deployment(
        self, spec: DeploymentSpec, deployment: Deployment
    ) -> AsyncIterator[RunStep]:
        async with self._client() as client:
            response = await client.post(
                "/api/v1/deployments/apply",
                json={
                    "spec": spec.to_wire(),
                    "plan_id": deployment.plan.id if deployment.plan else None,
                    "approved_by": "human",
                },
            )
            response.raise_for_status()
            run_id = response.json()["run_id"]
            yield RunStep(
                key="accepted",
                title="openCenter run created",
                message=run_id,
                progress=5,
                data={"run_id": run_id},
            )

            terminal = {"succeeded", "failed", "cancelled"}
            seen: set[str] = set()
            while True:
                await asyncio.sleep(3.0)
                status = (await client.get(f"/api/v1/runs/{run_id}")).json()
                for step in status.get("steps", []):
                    if step["id"] in seen:
                        continue
                    seen.add(step["id"])
                    yield RunStep(
                        key=step.get("name", "step"),
                        title=step.get("title", step.get("name", "step")),
                        message=step.get("message", ""),
                        progress=int(step.get("progress", 0)),
                        level="error" if step.get("state") == "failed" else "info",
                        failed=step.get("state") == "failed",
                    )
                if status.get("state") in terminal:
                    failed = status["state"] != "succeeded"
                    yield RunStep(
                        key="finished",
                        title=f"openCenter run {status['state']}",
                        progress=100,
                        level="error" if failed else "success",
                        failed=failed,
                        data=status,
                    )
                    return

    async def deployment_status(self, deployment_id: str) -> DeploymentStatusReport:
        async with self._client() as client:
            response = await client.get(f"/api/v1/deployments/{deployment_id}/status")
            response.raise_for_status()
            body = response.json()
        return DeploymentStatusReport(
            deployment_id=deployment_id,
            state=DeploymentState(body.get("state", "RUNNING")),
            progress=int(body.get("progress", 0)),
            health=body.get("health", "unknown"),
            message=body.get("message", ""),
        )

    async def rollback_deployment(self, deployment: Deployment) -> AsyncIterator[RunStep]:
        async with self._client() as client:
            response = await client.post(f"/api/v1/deployments/{deployment.id}/rollback")
            response.raise_for_status()
        yield RunStep(
            key="rollback_started",
            title="openCenter rollback started",
            progress=50,
        )
        yield RunStep(
            key="rollback_done", title="Rollback requested", progress=100, level="success"
        )


# ---------------------------------------------------------------------------


def _runtime_of(spec: DeploymentSpec) -> RuntimeType:
    mode = (spec.runtime.mode or "").lower()
    if mode == "managed-database":
        return RuntimeType.DATABASE_SERVICE
    if spec.runtime.type == "openstack":
        return (
            RuntimeType.OPENSTACK_GPU_VM
            if spec.runtime.accelerator == "gpu"
            else RuntimeType.OPENSTACK_CPU_VM
        )
    return (
        RuntimeType.KUBERNETES_GPU
        if spec.runtime.accelerator == "gpu"
        else RuntimeType.KUBERNETES_CPU
    )


def _memory_gb(memory: str) -> int:
    text = str(memory).strip()
    if text.endswith("Gi"):
        return int(float(text[:-2]))
    if text.endswith("Mi"):
        return max(int(float(text[:-2]) / 1024), 1)
    return int(float(text))


def _storage_gb(size: str) -> int:
    text = str(size).strip()
    if text.endswith("Gi"):
        return int(float(text[:-2]))
    if text.endswith("Ti"):
        return int(float(text[:-2]) * 1024)
    return int(float(text))


def build_opencenter_adapter(
    settings: Settings, infra: SimulatedInfrastructure | None
) -> OpenCenterAdapter:
    if settings.open_center_mode == "real":
        return RealOpenCenterAdapter(settings)
    if infra is None:
        raise AdapterNotConfigured("mock openCenter requires simulated infrastructure")
    return MockOpenCenterAdapter(settings, infra)
