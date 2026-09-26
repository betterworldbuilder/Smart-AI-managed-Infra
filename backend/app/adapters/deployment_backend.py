"""The deployment backend interface.

One interface, several implementations -- this is the seam between the shared
application (Copilot, Governor, policy, approval, audit, UI) and whatever
actually creates infrastructure:

    SimulationDeploymentBackend   mock openCenter over the simulated datacenter
    KubernetesDeploymentBackend   real kind/Kubernetes via GitOps + Flux
    OpenCenterDeploymentBackend   a real openCenter installation

Future: OpenStackDeploymentBackend, VMwareDeploymentBackend.

The business logic above never branches on which one is in use; the factory in
`app.container` picks one from `INFRA_MODE` / `OPEN_CENTER_MODE`.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field

from ..models import (
    Deployment,
    DeploymentArtifact,
    DeploymentPlan,
    DeploymentSpec,
    DeploymentStatusReport,
    RuntimeType,
    ValidationIssue,
)
from .base import InfrastructureAdapter


class RunStep(BaseModel):
    """One step of a deployment pipeline run, streamed to the UI."""

    key: str
    title: str
    message: str = ""
    progress: int = 0
    level: str = "info"
    failed: bool = False
    data: dict[str, Any] = Field(default_factory=dict)


class DeploymentBackend(InfrastructureAdapter):
    """Contract every deployment engine implements."""

    #: What the UI shows as the deployment engine, e.g. "Mock openCenter".
    engine_label: str = "deployment-backend"
    #: openCenter reality: mock | compat | real
    opencenter_mode: str = "mock"

    @abstractmethod
    async def get_clusters(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_cluster_resources(self, cluster_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def generate_deployment(self, spec: DeploymentSpec) -> list[DeploymentArtifact]: ...

    @abstractmethod
    async def validate_deployment(self, spec: DeploymentSpec) -> list[ValidationIssue]: ...

    @abstractmethod
    async def plan_deployment(self, spec: DeploymentSpec, deployment_id: str) -> DeploymentPlan: ...

    @abstractmethod
    def apply_deployment(
        self, spec: DeploymentSpec, deployment: Deployment
    ) -> AsyncIterator[RunStep]: ...

    @abstractmethod
    async def deployment_status(self, deployment_id: str) -> DeploymentStatusReport: ...

    @abstractmethod
    def rollback_deployment(self, deployment: Deployment) -> AsyncIterator[RunStep]: ...

    # -- section 26 aliases --------------------------------------------------
    # Shorter names for the same operations, as specified in the architecture.

    async def validate(self, spec: DeploymentSpec) -> list[ValidationIssue]:
        return await self.validate_deployment(spec)

    async def plan(self, spec: DeploymentSpec, deployment_id: str) -> DeploymentPlan:
        return await self.plan_deployment(spec, deployment_id)

    def deploy(self, spec: DeploymentSpec, deployment: Deployment) -> AsyncIterator[RunStep]:
        return self.apply_deployment(spec, deployment)

    async def status(self, deployment_id: str) -> DeploymentStatusReport:
        return await self.deployment_status(deployment_id)

    def destroy(self, deployment: Deployment) -> AsyncIterator[RunStep]:
        return self.rollback_deployment(deployment)

    # -- helpers -------------------------------------------------------------

    def cluster_for(self, runtime: RuntimeType, clusters: list[dict[str, Any]]) -> dict[str, Any]:
        wanted = "openstack" if runtime.platform.value == "openstack" else "kubernetes"
        for cluster in clusters:
            if cluster.get("type") == wanted:
                return cluster
        return clusters[0] if clusters else {"id": "unknown", "name": wanted, "type": wanted}

    async def capabilities(self) -> dict[str, Any]:
        return {
            "engine": self.engine_label,
            "opencenter_mode": self.opencenter_mode,
            "simulated": self.simulated,
        }
