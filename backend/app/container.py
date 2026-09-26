"""Composition root.

This is the *only* module that knows which mode the platform is running in.
Everything else receives finished collaborators. Adding a new execution mode
means adding a branch here and an adapter -- never an `if mode ==` anywhere in
the Copilot, the Governor or the API.
"""

from __future__ import annotations

import logging
from typing import Any

from .adapters import (
    GenestackAdapter,
    HostGPUDetector,
    SimulatedInfrastructure,
    build_ceph_adapter,
    build_kubernetes_adapter,
    build_opencenter_adapter,
    build_openstack_adapter,
)
from .adapters.deployment_backend import DeploymentBackend
from .adapters.flux import FluxAdapter, LocalGitOpsRepo, build_kubernetes_backend
from .audit import AuditService
from .capabilities import CapabilityService
from .config import Settings, get_settings
from .copilot import CopilotService, Wizard, build_llm_provider
from .copilot.llm import FallbackProvider, MockProvider
from .deployments import DeploymentService
from .events import EventBus
from .governor import RecommendationEngine, Scorer, SizingCalculator, load_profiles
from .inventory import GlobalResourceInventoryService
from .observability import MetricsService, PostDeploymentAdvisor, RemediationService
from .policies import build_policy_engine
from .redis_bridge import RedisBridge
from .scenarios import ScenarioService
from .store import Repositories, build_store

log = logging.getLogger(__name__)


class Container:
    """Builds and owns every long-lived service."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        s = self.settings

        # --- infrastructure state ------------------------------------------
        self.infra: SimulatedInfrastructure | None = None
        if s.needs_simulated_infrastructure:
            self.infra = SimulatedInfrastructure(s.inventory_file)

        # --- plumbing --------------------------------------------------------
        self.bus = EventBus()
        self.redis = RedisBridge(s.redis_url, self.bus)
        self.store = build_store(s)
        self.repositories = Repositories(self.store)
        self.audit = AuditService(self.repositories, self.bus)

        # --- inventory adapters ---------------------------------------------
        self.kubernetes = build_kubernetes_adapter(s, self.infra)
        self.openstack = build_openstack_adapter(s, self.infra)
        self.ceph = build_ceph_adapter(s, self.infra)
        self.genestack = GenestackAdapter(self.openstack, self.kubernetes)
        self.host_gpu = HostGPUDetector()
        self.inventory = GlobalResourceInventoryService(
            kubernetes=self.kubernetes,
            openstack=self.openstack,
            ceph=self.ceph,
            genestack=self.genestack,
            simulated=s.is_simulation,
        )

        # --- governor --------------------------------------------------------
        self.profiles = load_profiles()
        self.sizing = SizingCalculator(self.profiles)
        self.scorer = Scorer()
        self.policy = build_policy_engine(s)
        self.engine = RecommendationEngine(
            profiles=self.profiles,
            sizing=self.sizing,
            scorer=self.scorer,
            policy_engine=self.policy,
            inventory_service=self.inventory,
        )

        # --- copilot ---------------------------------------------------------
        provider = build_llm_provider(s)
        if s.llm_fallback_to_mock and provider.name != "mock":
            provider = FallbackProvider(provider, MockProvider())
        self.llm = provider
        self.wizard = Wizard(self.profiles)
        self.copilot = CopilotService(
            provider=self.llm,
            profiles=self.profiles,
            wizard=self.wizard,
            engine=self.engine,
            inventory=self.inventory,
            repositories=self.repositories,
            audit=self.audit,
        )

        # --- deployment backend ---------------------------------------------
        self.flux: FluxAdapter | None = None
        self.backend: DeploymentBackend = self._build_backend()

        self.deployments = DeploymentService(
            backend=self.backend,
            repositories=self.repositories,
            audit=self.audit,
            bus=self.bus,
            inventory=self.inventory,
            engine=self.engine,
        )

        # --- observability ---------------------------------------------------
        self.metrics = MetricsService(
            settings=s,
            inventory=self.inventory,
            repositories=self.repositories,
            bus=self.bus,
            infra=self.infra,
        )
        self.advisor = PostDeploymentAdvisor(
            metrics=self.metrics,
            repositories=self.repositories,
            audit=self.audit,
            bus=self.bus,
        )
        self.remediation = RemediationService(
            repositories=self.repositories,
            audit=self.audit,
            bus=self.bus,
            metrics=self.metrics,
        )

        # --- misc -------------------------------------------------------------
        self.capabilities = CapabilityService(
            settings=s,
            kubernetes=self.kubernetes,
            openstack=self.openstack,
            ceph=self.ceph,
            genestack=self.genestack,
            backend=self.backend,
            provider=self.llm,
            policy=self.policy,
            host_gpu=self.host_gpu,
            flux=self.flux,
        )
        self.scenarios = ScenarioService(self.copilot, s.scenarios_file)

        log.info(
            "container ready: infra_mode=%s opencenter=%s llm=%s policy=%s store=%s",
            s.infra_mode,
            self.backend.opencenter_mode,
            self.llm.name,
            self.policy.name,
            type(self.store).__name__,
        )

    # -- backend selection ---------------------------------------------------

    def _build_backend(self) -> DeploymentBackend:
        s = self.settings
        if s.open_center_mode == "real":
            return build_opencenter_adapter(s, self.infra)
        if s.is_mvp:
            # MVP: real Kubernetes through the local GitOps tree and Flux.
            # openCenter is honestly labelled "compat", never "real".
            self.flux = FluxAdapter(s)
            from pathlib import Path

            return KubernetesBackendFactory.build(s, self.kubernetes, self.flux)
        return build_opencenter_adapter(s, self.infra)

    # -- lifecycle -----------------------------------------------------------

    async def startup(self) -> None:
        await self.store.startup()
        await self.redis.start()
        await self.metrics.start()

    async def shutdown(self) -> None:
        await self.metrics.stop()
        await self.redis.stop()
        await self.store.shutdown()

    # -- health --------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        adapters = await self.inventory.health()
        backend = await self.backend.health()
        return {
            "status": "ok",
            "mode": self.settings.infra_mode,
            "simulation": self.settings.is_simulation,
            "adapters": adapters,
            "deployment_backend": backend,
            "redis": await self.redis.health(),
            "store": type(self.store).__name__,
            "llm": {"provider": self.llm.name, "model": self.llm.model},
            "policy_engine": self.policy.name,
        }


class KubernetesBackendFactory:
    """Small indirection so tests can stub the MVP backend."""

    @staticmethod
    def build(settings: Settings, kubernetes, flux: FluxAdapter):
        from pathlib import Path

        from .adapters.flux import KubernetesDeploymentBackend

        return KubernetesDeploymentBackend(
            settings=settings,
            inventory=kubernetes,
            flux=flux,
            gitops=LocalGitOpsRepo(
                Path(settings.gitops_working_tree), settings.gitops_branch
            ),
        )


_container: Container | None = None


def get_container() -> Container:
    global _container
    if _container is None:
        _container = Container()
    return _container


def set_container(container: Container | None) -> None:
    """Used by tests to inject a container built with custom settings."""
    global _container
    _container = container
