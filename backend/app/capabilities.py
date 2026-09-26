"""System capabilities and the reality matrix (MVP sections 10, 16, 25).

The single most dangerous failure mode for a platform like this is letting an
operator believe something simulated is real. This service answers, honestly,
for every component: is it REAL, SIMULATED, COMPAT, MOCK or UNAVAILABLE -- and
why.
"""

from __future__ import annotations

import logging
from typing import Any

from .adapters.ceph import CephAdapter
from .adapters.deployment_backend import DeploymentBackend
from .adapters.flux import FluxAdapter
from .adapters.genestack import GenestackAdapter
from .adapters.gpu_host import HostGPUDetector
from .adapters.kubernetes import KubernetesInventoryAdapter
from .adapters.openstack import OpenStackInventoryAdapter
from .config import Settings
from .copilot.llm import LLMProvider
from .policies import PolicyEngine

log = logging.getLogger(__name__)

REAL = "real"
SIMULATED = "simulated"
MOCK = "mock"
COMPAT = "compat"
UNAVAILABLE = "unavailable"
DISABLED = "disabled"


class CapabilityService:
    def __init__(
        self,
        settings: Settings,
        kubernetes: KubernetesInventoryAdapter,
        openstack: OpenStackInventoryAdapter,
        ceph: CephAdapter,
        genestack: GenestackAdapter,
        backend: DeploymentBackend,
        provider: LLMProvider,
        policy: PolicyEngine,
        host_gpu: HostGPUDetector,
        flux: FluxAdapter | None = None,
    ) -> None:
        self.settings = settings
        self.kubernetes = kubernetes
        self.openstack = openstack
        self.ceph = ceph
        self.genestack = genestack
        self.backend = backend
        self.provider = provider
        self.policy = policy
        self.host_gpu = host_gpu
        self.flux = flux

    async def capabilities(self) -> dict[str, Any]:
        settings = self.settings
        kubernetes_real = not self.kubernetes.simulated

        cluster_gpu, gpu_reason = await self._cluster_gpu()
        host = await self.host_gpu.summary(visible_to_cluster=cluster_gpu == REAL)
        flux_state = await self._flux_state()

        return {
            "mode": settings.infra_mode,
            "mode_label": "MVP - KIND" if settings.is_mvp else "POC - SIMULATION",
            "copilot": REAL,
            "governor": REAL,
            "policy": REAL,
            "policy_engine": self.policy.name,
            "human_approval": REAL,
            "kubernetes": REAL if kubernetes_real else SIMULATED,
            "kubernetes_cluster": settings.kind_cluster_name
            if settings.is_mvp
            else "simulated k8s-core",
            "flux": flux_state,
            "opencenter": self.backend.opencenter_mode,
            "deployment_engine": self.backend.engine_label,
            "openstack": REAL if not self.openstack.simulated else SIMULATED,
            "genestack": settings.genestack_mode,
            "ceph": REAL if not self.ceph.simulated else SIMULATED,
            "host_gpu": host["host_gpu"],
            "host_gpu_models": host["models"],
            "kubernetes_gpu": cluster_gpu,
            "kubernetes_gpu_reason": gpu_reason,
            "llm_provider": self.provider.name,
            "llm_model": self.provider.model,
            "auth_enabled": settings.auth_enabled,
            # Only a boolean -- never the credential. Lets the login page offer
            # the admin/admin hint locally, and stay quiet once install.sh has
            # replaced the defaults on a public host.
            "default_credentials": settings.auth_enabled
            and settings.auth_username == "admin"
            and settings.auth_password == "admin",
            "simulated_datacenter": settings.needs_simulated_infrastructure,
        }

    async def _cluster_gpu(self) -> tuple[str, str]:
        try:
            gpus = await self.kubernetes.get_gpu_resources()
        except Exception as exc:  # noqa: BLE001
            return UNAVAILABLE, f"could not read GPU resources: {exc}"
        if not gpus:
            return (
                UNAVAILABLE,
                "No schedulable 'nvidia.com/gpu' resource detected in the cluster.",
            )
        if self.kubernetes.simulated:
            return SIMULATED, "GPU inventory comes from simulation/inventory.yaml."
        return REAL, f"{len(gpus)} GPU(s) advertised by the device plugin."

    async def _flux_state(self) -> str:
        if self.settings.is_simulation:
            return SIMULATED
        if self.flux is None:
            return UNAVAILABLE
        health = await self.flux.health()
        if health.get("installed"):
            return REAL
        return UNAVAILABLE

    async def reality_matrix(self) -> list[dict[str, Any]]:
        """The table the UI renders: what is real in POC versus in this run."""
        caps = await self.capabilities()
        rows = [
            ("AI Copilot", REAL, caps["copilot"]),
            ("AI Governor", REAL, caps["governor"]),
            ("Policy engine", REAL, caps["policy"]),
            ("Human approval", REAL, caps["human_approval"]),
            ("Kubernetes", SIMULATED, caps["kubernetes"]),
            ("FluxCD", SIMULATED, caps["flux"]),
            ("Kubernetes workloads", SIMULATED, caps["kubernetes"]),
            ("Resource inventory", SIMULATED, caps["kubernetes"]),
            ("openCenter", MOCK, caps["opencenter"]),
            ("Genestack", MOCK, caps["genestack"]),
            ("OpenStack", SIMULATED, caps["openstack"]),
            ("Nova CPU VM", SIMULATED, caps["openstack"]),
            ("Nova GPU VM", SIMULATED, caps["openstack"]),
            ("Ceph storage", SIMULATED, caps["ceph"]),
            ("Host GPU inventory", SIMULATED, caps["host_gpu"]),
            ("Kubernetes GPU scheduling", SIMULATED, caps["kubernetes_gpu"]),
        ]
        return [
            {"component": component, "poc": poc, "current": current}
            for component, poc, current in rows
        ]

    async def integrations(self) -> list[dict[str, Any]]:
        """`/settings/integrations` -- one row per external system."""
        rows: list[dict[str, Any]] = []

        async def probe(name: str, label: str, adapter, extra: dict[str, Any] | None = None):
            try:
                health = await adapter.health()
                reachable = bool(health.get("reachable"))
                simulated = bool(getattr(adapter, "simulated", True))
                status = (
                    "SIMULATED"
                    if simulated
                    else ("CONNECTED" if reachable else "ERROR")
                )
                rows.append(
                    {
                        "key": name,
                        "name": label,
                        "status": status,
                        "detail": health,
                        **(extra or {}),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                rows.append(
                    {
                        "key": name,
                        "name": label,
                        "status": "ERROR",
                        "detail": {"error": str(exc)},
                        **(extra or {}),
                    }
                )

        provider_available = await self.provider.available()
        rows.append(
            {
                "key": "llm",
                "name": "AI provider",
                "status": "SIMULATED"
                if self.provider.name == "mock"
                else ("CONNECTED" if provider_available else "DISCONNECTED"),
                "detail": {"provider": self.provider.name, "model": self.provider.model},
                "configure": "LLM_PROVIDER / LLM_BASE_URL / LLM_MODEL",
            }
        )
        await probe("opencenter", "openCenter", self.backend, {"configure": "OPEN_CENTER_MODE"})
        await probe("kubernetes", "Kubernetes", self.kubernetes, {"configure": "INFRA_MODE"})
        await probe("openstack", "OpenStack", self.openstack, {"configure": "OPENSTACK_MODE"})
        await probe("ceph", "Ceph", self.ceph, {"configure": "CEPH_MODE"})
        await probe("genestack", "Genestack", self.genestack, {"configure": "GENESTACK_MODE"})
        rows.append(
            {
                "key": "policy",
                "name": "Policy engine",
                "status": "CONNECTED" if self.policy.name == "opa" else "SIMULATED",
                "detail": {"engine": self.policy.name, "url": self.settings.opa_url},
                "configure": "POLICY_ENGINE / OPA_URL",
            }
        )
        if self.flux is not None and not self.settings.is_simulation:
            await probe("flux", "FluxCD", self.flux, {"configure": "INFRA_MODE=mvp"})
        else:
            rows.append(
                {
                    "key": "flux",
                    "name": "FluxCD",
                    "status": "SIMULATED",
                    "detail": {"note": "GitOps is simulated in POC mode"},
                    "configure": "INFRA_MODE=mvp",
                }
            )
        host = await self.host_gpu.detect()
        rows.append(
            {
                "key": "host_gpu",
                "name": "Host GPU (nvidia-smi)",
                "status": "CONNECTED" if host.get("available") else "DISCONNECTED",
                "detail": host,
                "configure": "requires nvidia-smi on the host",
            }
        )
        return rows
