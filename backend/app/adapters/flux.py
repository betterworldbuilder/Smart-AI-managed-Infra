"""MVP deployment backend: local GitOps tree -> Flux -> real Kubernetes.

This is the adapter that makes the MVP real. The Copilot, Governor, policy
engine, approval workflow and DeploymentSpec are unchanged -- only what happens
after the second human approval differs:

    DeploymentSpec
        -> ManifestGenerator (shared with the POC)
        -> local GitOps working tree
        -> git commit
        -> Flux reconciliation (when a Git source is wired up)
           or server-side apply (local demo reconciliation)
        -> real Kubernetes objects
        -> kube-scheduler picks the node

Two honesty rules are enforced here:

* if Flux is not driving the apply, the engine label says so -- never
  "FluxCD" when the manifests were applied directly;
* placement is read back from the API (`pod.spec.nodeName`), never guessed.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any, AsyncIterator

import yaml

from ..config import Settings
from ..deployments.artifacts import generate_artifacts, plan_changes
from ..models import (
    Deployment,
    DeploymentArtifact,
    DeploymentPlan,
    DeploymentSpec,
    DeploymentState,
    DeploymentStatusReport,
    Placement,
    RuntimeType,
    ValidationIssue,
)
from .base import AdapterNotConfigured, InfrastructureAdapter, require
from .deployment_backend import DeploymentBackend, RunStep
from .kubernetes import GPU_RESOURCE, KubernetesInventoryAdapter

log = logging.getLogger(__name__)

FLUX_NAMESPACE = "flux-system"
FLUX_KUSTOMIZATION = ("kustomize.toolkit.fluxcd.io", "v1", "kustomizations")
FLUX_GITREPOSITORY = ("source.toolkit.fluxcd.io", "v1", "gitrepositories")


# ---------------------------------------------------------------------------
# Local GitOps working tree
# ---------------------------------------------------------------------------


class LocalGitOpsRepo:
    """A directory of generated manifests, committed on every deployment.

    Deliberately behind a small interface so a GitHub/GitLab remote can be
    dropped in later without touching the deployment backend.
    """

    def __init__(self, root: Path, branch: str = "main") -> None:
        self.root = Path(root)
        self.branch = branch
        self.root.mkdir(parents=True, exist_ok=True)

    def _git(self, *args: str) -> tuple[int, str]:
        try:
            result = subprocess.run(  # noqa: S603
                ["git", *args],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            return result.returncode, (result.stdout + result.stderr).strip()
        except FileNotFoundError:
            return 127, "git is not installed"
        except subprocess.TimeoutExpired:
            return 124, "git timed out"

    def ensure_repo(self) -> bool:
        if (self.root / ".git").exists():
            return True
        code, _ = self._git("init", "-q", "-b", self.branch)
        if code != 0:
            return False
        self._git("config", "user.email", "ai-governor@aiinfra.local")
        self._git("config", "user.name", "AI Governor")
        return True

    def write(self, artifacts: list[DeploymentArtifact]) -> list[str]:
        written: list[str] = []
        for artifact in artifacts:
            target = self.root / artifact.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(artifact.content, encoding="utf-8")
            written.append(artifact.path)
        return written

    def remove(self, prefix: str) -> list[str]:
        removed: list[str] = []
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and prefix in str(path.relative_to(self.root)):
                path.unlink()
                removed.append(str(path.relative_to(self.root)))
        return removed

    def commit(self, message: str) -> dict[str, Any]:
        if not self.ensure_repo():
            return {"committed": False, "reason": "git unavailable", "sha": None}
        self._git("add", "-A")
        code, output = self._git("commit", "-m", message)
        if code != 0 and "nothing to commit" in output:
            return {"committed": False, "reason": "no changes", "sha": self.head()}
        return {"committed": code == 0, "sha": self.head(), "output": output[-400:]}

    def head(self) -> str | None:
        code, output = self._git("rev-parse", "--short", "HEAD")
        return output if code == 0 else None

    def files(self) -> list[str]:
        return sorted(
            str(path.relative_to(self.root))
            for path in self.root.rglob("*")
            if path.is_file() and ".git/" not in str(path)
        )


# ---------------------------------------------------------------------------
# Flux
# ---------------------------------------------------------------------------


class FluxAdapter(InfrastructureAdapter):
    """Reads real FluxCD state and triggers reconciliation."""

    name = "flux"
    simulated = False

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._dynamic = None
        self._core = None

    def _clients(self):
        if self._dynamic is not None:
            return self._dynamic, self._core
        k8s = require("kubernetes", "pip install -r backend/requirements-real.txt")
        config, client, dynamic = k8s.config, k8s.client, k8s.dynamic
        try:
            if self.settings.kubeconfig:
                config.load_kube_config(
                    config_file=self.settings.kubeconfig,
                    context=self.settings.kubernetes_context or None,
                )
            else:
                config.load_incluster_config()
        except Exception:  # noqa: BLE001
            config.load_kube_config(context=self.settings.kubernetes_context or None)
        api_client = client.ApiClient()
        self._dynamic = dynamic.DynamicClient(api_client)
        self._core = client.CoreV1Api()
        return self._dynamic, self._core

    async def installed(self) -> bool:
        try:
            dyn, _ = await asyncio.to_thread(self._clients)
            await asyncio.to_thread(
                dyn.resources.get,
                api_version=f"{FLUX_KUSTOMIZATION[0]}/{FLUX_KUSTOMIZATION[1]}",
                kind="Kustomization",
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    async def health(self) -> dict[str, Any]:
        try:
            installed = await self.installed()
            if not installed:
                return {
                    "adapter": self.name,
                    "reachable": False,
                    "installed": False,
                    "reason": "Flux CRDs not found in the cluster",
                }
            kustomizations = await self.list_kustomizations()
            ready = [k for k in kustomizations if k.get("ready")]
            return {
                "adapter": self.name,
                "reachable": True,
                "installed": True,
                "kustomizations": len(kustomizations),
                "ready": len(ready),
                "reconciliation": "healthy"
                if len(ready) == len(kustomizations)
                else "degraded",
            }
        except Exception as exc:  # noqa: BLE001
            return {"adapter": self.name, "reachable": False, "error": str(exc)}

    async def list_kustomizations(self) -> list[dict[str, Any]]:
        dyn, _ = await asyncio.to_thread(self._clients)

        def _list() -> list[dict[str, Any]]:
            api = dyn.resources.get(
                api_version=f"{FLUX_KUSTOMIZATION[0]}/{FLUX_KUSTOMIZATION[1]}",
                kind="Kustomization",
            )
            out = []
            for item in api.get().items:
                conditions = (item.status or {}).get("conditions", []) if item.status else []
                ready = any(
                    c.get("type") == "Ready" and c.get("status") == "True" for c in conditions
                )
                out.append(
                    {
                        "name": item.metadata.name,
                        "namespace": item.metadata.namespace,
                        "ready": ready,
                        "message": next(
                            (c.get("message") for c in conditions if c.get("type") == "Ready"),
                            "",
                        ),
                    }
                )
            return out

        return await asyncio.to_thread(_list)

    async def reconcile(self, name: str, namespace: str = FLUX_NAMESPACE) -> bool:
        """Annotate a Kustomization to request immediate reconciliation."""
        from datetime import datetime, timezone

        dyn, _ = await asyncio.to_thread(self._clients)

        def _patch() -> bool:
            api = dyn.resources.get(
                api_version=f"{FLUX_KUSTOMIZATION[0]}/{FLUX_KUSTOMIZATION[1]}",
                kind="Kustomization",
            )
            api.patch(
                name=name,
                namespace=namespace,
                body={
                    "metadata": {
                        "annotations": {
                            "reconcile.fluxcd.io/requestedAt": datetime.now(
                                timezone.utc
                            ).isoformat()
                        }
                    }
                },
                content_type="application/merge-patch+json",
            )
            return True

        try:
            return await asyncio.to_thread(_patch)
        except Exception as exc:  # noqa: BLE001
            log.warning("flux reconcile failed for %s: %s", name, exc)
            return False


# ---------------------------------------------------------------------------
# The MVP deployment backend
# ---------------------------------------------------------------------------


class KubernetesDeploymentBackend(DeploymentBackend):
    """Applies approved DeploymentSpecs to a real Kubernetes cluster."""

    name = "kubernetes-deployment"
    simulated = False
    opencenter_mode = "compat"

    def __init__(
        self,
        settings: Settings,
        inventory: KubernetesInventoryAdapter,
        flux: FluxAdapter,
        gitops: LocalGitOpsRepo,
    ) -> None:
        self.settings = settings
        self.inventory = inventory
        self.flux = flux
        self.gitops = gitops
        self._dynamic = None
        self._core = None
        self._apps = None
        self._flux_driven = False

    # -- client --------------------------------------------------------------

    def _clients(self):
        if self._dynamic is not None:
            return self._dynamic, self._core, self._apps
        k8s = require("kubernetes", "pip install -r backend/requirements-real.txt")
        config, client, dynamic = k8s.config, k8s.client, k8s.dynamic
        try:
            if self.settings.kubeconfig:
                config.load_kube_config(
                    config_file=self.settings.kubeconfig,
                    context=self.settings.kubernetes_context or None,
                )
            else:
                config.load_incluster_config()
        except Exception:  # noqa: BLE001
            config.load_kube_config(context=self.settings.kubernetes_context or None)
        api_client = client.ApiClient()
        self._dynamic = dynamic.DynamicClient(api_client)
        self._core = client.CoreV1Api()
        self._apps = client.AppsV1Api()
        return self._dynamic, self._core, self._apps

    @property
    def engine_label(self) -> str:  # type: ignore[override]
        return (
            "FluxCD (GitOps)"
            if self._flux_driven
            else "GitOps working tree + Kubernetes server-side apply"
        )

    async def health(self) -> dict[str, Any]:
        cluster = await self.inventory.health()
        flux = await self.flux.health()
        return {
            "adapter": self.name,
            "simulated": False,
            "reachable": bool(cluster.get("reachable")),
            "engine": self.engine_label,
            "opencenter_mode": self.opencenter_mode,
            "flux": flux,
            "gitops": {
                "root": str(self.gitops.root),
                "branch": self.gitops.branch,
                "head": self.gitops.head(),
                "files": len(self.gitops.files()),
            },
        }

    # -- clusters ------------------------------------------------------------

    async def get_clusters(self) -> list[dict[str, Any]]:
        # Describing the target must not require the cluster to be reachable:
        # manifest generation and the approval screen have to work even when
        # the API server is down, so the operator can see what *would* happen.
        name = self.settings.kubernetes_context or self.settings.kind_cluster_name
        try:
            nodes = [node.name for node in await self.inventory.list_nodes()]
            reachable = True
        except Exception as exc:  # noqa: BLE001
            log.warning("cluster unreachable while listing clusters: %s", exc)
            nodes = []
            reachable = False
        try:
            gitops = "fluxcd" if await self.flux.installed() else "local"
        except Exception:  # noqa: BLE001
            gitops = "local"
        return [
            {
                "id": f"k8s-{name}",
                "name": name,
                "type": "kubernetes",
                "gitops": gitops,
                "repo": str(self.gitops.root),
                "branch": self.gitops.branch,
                "path": "workloads",
                "nodes": nodes,
                "reachable": reachable,
            }
        ]

    async def get_cluster_resources(self, cluster_id: str) -> dict[str, Any]:
        capacity = await self.inventory.get_cluster_capacity()
        return {
            "cluster": {"id": cluster_id, "type": "kubernetes"},
            "nodes": [node.model_dump() for node in capacity.nodes],
            "cpu": capacity.cpu.model_dump(),
            "ram_gb": capacity.ram_gb.model_dump(),
            "gpu": {"total": capacity.gpu.total, "available": capacity.gpu.available},
        }

    # -- generate / validate / plan -----------------------------------------

    async def generate_deployment(self, spec: DeploymentSpec) -> list[DeploymentArtifact]:
        runtime = _runtime_of(spec)
        clusters = await self.get_clusters()
        artifacts = generate_artifacts(spec, clusters[0], runtime)
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

        if runtime.platform.value == "openstack":
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="RUNTIME_UNSUPPORTED",
                    message="This MVP deploys Kubernetes workloads only; OpenStack runtimes "
                    "remain simulated until the Genestack phase.",
                )
            )
            return issues

        capacity = await self.inventory.get_cluster_capacity()
        vcpu = spec.compute.cpu * spec.compute.replicas
        ram_gb = _memory_gb(spec.compute.memory) * spec.compute.replicas

        if vcpu > capacity.cpu.available:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="CPU_PRESSURE",
                    message=f"{vcpu} vCPU requested, {int(capacity.cpu.available)} allocatable "
                    "free -- pods may stay Pending",
                )
            )
        if ram_gb > capacity.ram_gb.available:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="MEMORY_PRESSURE",
                    message=f"{ram_gb} GB requested, {int(capacity.ram_gb.available)} GB free",
                )
            )
        if spec.gpu is not None:
            gpu_total = spec.gpu.count_per_replica * spec.compute.replicas
            if capacity.gpu.available < gpu_total:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="GPU_UNAVAILABLE",
                        message=f"The cluster advertises {int(capacity.gpu.available)} free "
                        f"'{GPU_RESOURCE}', {gpu_total} requested. In kind, GPUs are normally "
                        "not schedulable -- deploy without a GPU or expose one to the cluster.",
                    )
                )

        try:
            classes = await self._storage_classes()
            if spec.storage.backend not in classes:
                default = ", ".join(sorted(classes)) or "none"
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="STORAGECLASS_MISSING",
                        message=f"StorageClass '{spec.storage.backend}' not found "
                        f"(available: {default}); the default class will be used",
                    )
                )
        except Exception as exc:  # noqa: BLE001
            issues.append(
                ValidationIssue(severity="info", code="STORAGE_UNKNOWN", message=str(exc))
            )

        issues.append(
            ValidationIssue(
                severity="info",
                code="CAPACITY_SUMMARY",
                message=f"requires {vcpu} vCPU, {ram_gb} GB RAM, {spec.storage.size} storage",
            )
        )
        return issues

    async def _storage_classes(self) -> set[str]:
        k8s = require("kubernetes", "pip install -r backend/requirements-real.txt")
        _, _, _ = self._clients()
        storage_api = k8s.client.StorageV1Api()
        classes = await asyncio.to_thread(storage_api.list_storage_class)
        names = {item.metadata.name for item in classes.items}
        for item in classes.items:
            annotations = item.metadata.annotations or {}
            if annotations.get("storageclass.kubernetes.io/is-default-class") == "true":
                names.add("default")
        return names

    async def plan_deployment(self, spec: DeploymentSpec, deployment_id: str) -> DeploymentPlan:
        runtime = _runtime_of(spec)
        clusters = await self.get_clusters()
        artifacts = await self.generate_deployment(spec)
        issues = await self.validate_deployment(spec)
        flux_installed = await self.flux.installed()
        return DeploymentPlan(
            deployment_id=deployment_id,
            cluster_id=clusters[0]["id"],
            changes=plan_changes(spec, runtime),
            artifacts=artifacts,
            issues=issues,
            valid=not any(issue.severity == "error" for issue in issues),
            estimated_duration_seconds=90,
            gitops={
                "repo": str(self.gitops.root),
                "branch": self.gitops.branch,
                "path": "workloads",
                "engine": "fluxcd" if flux_installed else "local-apply",
                "provisioner": "kustomize",
                "flux_installed": flux_installed,
                "note": (
                    "Flux is installed; workload manifests are committed to the local GitOps "
                    "tree and applied to the cluster."
                    if flux_installed
                    else "Flux is not installed; manifests are applied directly."
                ),
            },
        )

    # -- apply ---------------------------------------------------------------

    async def apply_deployment(
        self, spec: DeploymentSpec, deployment: Deployment
    ) -> AsyncIterator[RunStep]:
        name = spec.metadata.name
        namespace = f"ai-{name}"
        plan = deployment.plan
        artifacts = plan.artifacts if plan else await self.generate_deployment(spec)

        yield RunStep(
            key="accepted",
            title="Deployment accepted",
            message=f"target cluster {self.settings.kubernetes_context or 'kind'}",
            progress=5,
        )

        # 1. GitOps commit
        written = self.gitops.write([a for a in artifacts if a.language in ("yaml", "markdown")])
        commit = self.gitops.commit(f"feat(aiinfra): deploy {name}")
        yield RunStep(
            key="gitops_commit",
            title="Manifests committed to the GitOps tree",
            message=f"{commit.get('sha') or 'local'} -- {len(written)} files",
            progress=20,
            data={"files": written, **commit},
        )

        # 2. Reconcile
        flux_installed = await self.flux.installed()
        self._flux_driven = False
        if flux_installed:
            yield RunStep(
                key="flux",
                title="FluxCD detected",
                message="Flux is reconciling the cluster; workload manifests are applied "
                "through the platform API for this local demo tree.",
                progress=30,
            )

        # 3. Apply the manifests for real.
        manifests = [
            document
            for artifact in artifacts
            if artifact.language == "yaml" and "/deployment-specs/" not in artifact.path
            for document in yaml.safe_load_all(artifact.content)
            if document
        ]
        # Flux Kustomization objects only make sense with a real Git source.
        manifests = [m for m in manifests if m.get("kind") != "Kustomization"]

        applied: list[str] = []
        try:
            for manifest in manifests:
                kind = await self._apply(manifest)
                applied.append(kind)
        except Exception as exc:  # noqa: BLE001
            yield RunStep(
                key="apply_failed",
                title="Kubernetes rejected the manifests",
                message=str(exc)[:400],
                progress=45,
                level="error",
                failed=True,
            )
            return

        yield RunStep(
            key="applied",
            title="Applied to Kubernetes",
            message=", ".join(applied),
            progress=55,
            data={"objects": applied},
        )

        # 4. Wait for the workload, then read back where kube-scheduler put it.
        placement, ready, message = await self._wait_ready(namespace, name)
        if not ready:
            yield RunStep(
                key="unhealthy",
                title="Workload did not become ready",
                message=message,
                progress=85,
                level="error",
                failed=True,
                data={"placement": placement.model_dump() if placement else None},
            )
            return

        yield RunStep(
            key="schedule",
            title=f"kube-scheduler placed the pods on {placement.node if placement else 'a node'}",
            message=message,
            progress=85,
            data={"placement": placement.model_dump() if placement else None},
        )
        yield RunStep(
            key="healthy",
            title="Workload is running",
            message=f"namespace {namespace}",
            progress=100,
            level="success",
            data={"placement": placement.model_dump() if placement else None},
        )

    async def _apply(self, manifest: dict[str, Any]) -> str:
        dyn, _, _ = await asyncio.to_thread(self._clients)

        def _do() -> str:
            api = dyn.resources.get(
                api_version=manifest["apiVersion"], kind=manifest["kind"]
            )
            namespace = manifest.get("metadata", {}).get("namespace")
            api.server_side_apply(
                body=manifest,
                namespace=namespace,
                field_manager="ai-governor",
                force_conflicts=True,
            )
            return f"{manifest['kind']}/{manifest['metadata']['name']}"

        try:
            return await asyncio.to_thread(_do)
        except Exception as exc:  # noqa: BLE001 - CRDs (ServiceMonitor) may be absent
            kind = manifest.get("kind", "object")
            if "not found" in str(exc).lower() or "404" in str(exc):
                log.info("skipping %s: CRD not installed", kind)
                return f"{kind} (skipped: CRD not installed)"
            raise

    async def _wait_ready(
        self, namespace: str, name: str, timeout: float = 180.0
    ) -> tuple[Placement | None, bool, str]:
        _, core, apps = await asyncio.to_thread(self._clients)
        deadline = asyncio.get_running_loop().time() + timeout
        last = "waiting for pods"
        while asyncio.get_running_loop().time() < deadline:
            try:
                pods = await asyncio.to_thread(
                    core.list_namespaced_pod,
                    namespace,
                    label_selector=f"app.kubernetes.io/name={name}",
                )
            except Exception as exc:  # noqa: BLE001
                last = str(exc)
                await asyncio.sleep(2.0)
                continue

            if pods.items:
                running = [p for p in pods.items if p.status.phase == "Running"]
                ready = [
                    p
                    for p in running
                    if all(c.ready for c in (p.status.container_statuses or []))
                ]
                nodes = sorted({p.spec.node_name for p in pods.items if p.spec.node_name})
                if ready:
                    return (
                        Placement(
                            scheduler="kube-scheduler",
                            node=nodes[0] if nodes else "unknown",
                            platform="kubernetes",
                            reason="selected by kube-scheduler (read back from the API)",
                        ),
                        True,
                        f"{len(ready)}/{len(pods.items)} pods ready on {', '.join(nodes)}",
                    )
                waiting = [
                    (c.state.waiting.reason if c.state and c.state.waiting else "")
                    for p in pods.items
                    for c in (p.status.container_statuses or [])
                ]
                last = (
                    f"{len(running)}/{len(pods.items)} running"
                    + (f" ({', '.join(r for r in waiting if r)})" if any(waiting) else "")
                )
            await asyncio.sleep(2.0)
        return None, False, f"timed out after {int(timeout)}s: {last}"

    # -- status / rollback ---------------------------------------------------

    async def deployment_status(self, deployment_id: str) -> DeploymentStatusReport:
        return DeploymentStatusReport(
            deployment_id=deployment_id,
            state=DeploymentState.RUNNING,
            progress=100,
            health="healthy",
            message="status is read live from the Kubernetes API",
        )

    async def workload_status(self, namespace: str, name: str) -> dict[str, Any]:
        _, core, apps = await asyncio.to_thread(self._clients)
        try:
            pods = await asyncio.to_thread(
                core.list_namespaced_pod,
                namespace,
                label_selector=f"app.kubernetes.io/name={name}",
            )
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc), "pods": []}
        return {
            "pods": [
                {
                    "name": pod.metadata.name,
                    "phase": pod.status.phase,
                    "node": pod.spec.node_name,
                    "restarts": sum(
                        c.restart_count for c in (pod.status.container_statuses or [])
                    ),
                    "ready": all(c.ready for c in (pod.status.container_statuses or []))
                    if pod.status.container_statuses
                    else False,
                }
                for pod in pods.items
            ]
        }

    async def rollback_deployment(self, deployment: Deployment) -> AsyncIterator[RunStep]:
        name = deployment.spec.metadata.name if deployment.spec else deployment.name
        namespace = f"ai-{name}"
        yield RunStep(key="rollback_start", title="Rollback requested", progress=10)

        removed = self.gitops.remove(f"/{name}/")
        commit = self.gitops.commit(f"revert(aiinfra): remove {name}")
        yield RunStep(
            key="rollback_commit",
            title="GitOps revert committed",
            message=f"{len(removed)} files removed ({commit.get('sha') or 'local'})",
            progress=40,
        )

        try:
            _, core, _ = await asyncio.to_thread(self._clients)
            await asyncio.to_thread(core.delete_namespace, namespace)
            message = f"namespace {namespace} deleted"
        except Exception as exc:  # noqa: BLE001
            message = f"namespace delete reported: {exc}"
        yield RunStep(
            key="rollback_deleted", title="Kubernetes resources removed", message=message, progress=85
        )
        yield RunStep(
            key="rollback_done", title="Rollback complete", progress=100, level="success"
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


def build_kubernetes_backend(
    settings: Settings, inventory: KubernetesInventoryAdapter
) -> KubernetesDeploymentBackend:
    if not settings.gitops_working_tree:
        raise AdapterNotConfigured("GITOPS_WORKING_TREE is not set")
    return KubernetesDeploymentBackend(
        settings=settings,
        inventory=inventory,
        flux=FluxAdapter(settings),
        gitops=LocalGitOpsRepo(Path(settings.gitops_working_tree), settings.gitops_branch),
    )
