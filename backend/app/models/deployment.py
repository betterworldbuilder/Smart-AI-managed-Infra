"""Deployment specification and lifecycle records (sections 14, 15, 16).

`DeploymentSpec` is the contract between the AI Governor and openCenter. It is
deliberately vendor neutral: no kubectl, no nova, no tofu -- just intent-shaped
infrastructure. Turning it into platform artefacts is the adapter's job.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .enums import ActorKind, DeploymentState, RuntimeType
from .intent import WorkloadIntent
from .recommendation import RecommendationOption


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# The vendor neutral specification (section 15)
# ---------------------------------------------------------------------------


class _SpecModel(BaseModel):
    """Base for spec fragments: camelCase on the wire, snake_case in Python."""

    model_config = ConfigDict(populate_by_name=True)


class SpecMetadata(_SpecModel):
    name: str
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)


class SpecIntent(_SpecModel):
    workload: str
    environment: str


class SpecRuntime(_SpecModel):
    type: str  # kubernetes | openstack
    accelerator: str = "none"  # gpu | none
    mode: str | None = None  # vm | container | managed-database


class SpecCompute(_SpecModel):
    replicas: int = 1
    cpu: int = 2
    memory: str = "4Gi"
    flavor: str | None = None
    image: str | None = None


class SpecGPU(_SpecModel):
    vendor: str = "nvidia"
    model: str
    count_per_replica: int = Field(default=1, alias="countPerReplica")
    allocation_type: str = Field(default="passthrough", alias="allocationType")
    memory_gb: int | None = Field(default=None, alias="memoryGb")
    pci_alias: str | None = Field(default=None, alias="pciAlias")


class SpecStorage(_SpecModel):
    type: str = "persistent"
    backend: str = "ceph-rbd"
    size: str = "20Gi"


class SpecNetwork(_SpecModel):
    exposure: str = "internal"
    network: str | None = None
    network_policy: bool = Field(default=True, alias="networkPolicy")


class SpecSecurity(_SpecModel):
    classification: str = "internal"
    encryption_at_rest: bool = Field(default=False, alias="encryptionAtRest")
    isolation: str = "medium"


class SpecAvailability(_SpecModel):
    high_availability: bool = Field(default=False, alias="highAvailability")
    anti_affinity: bool = Field(default=False, alias="antiAffinity")
    min_replicas: int = Field(default=1, alias="minReplicas")


class SpecObservability(_SpecModel):
    service_monitor: bool = Field(default=True, alias="serviceMonitor")
    gpu_metrics: bool = Field(default=False, alias="gpuMetrics")
    dashboards: list[str] = Field(default_factory=list)


class DeploymentSpec(_SpecModel):
    """`aiinfra/v1alpha1 WorkloadDeployment`."""

    api_version: str = Field(default="aiinfra/v1alpha1", alias="apiVersion")
    kind: str = "WorkloadDeployment"
    metadata: SpecMetadata
    intent: SpecIntent
    runtime: SpecRuntime
    compute: SpecCompute
    gpu: SpecGPU | None = None
    storage: SpecStorage
    network: SpecNetwork
    security: SpecSecurity
    availability: SpecAvailability
    observability: SpecObservability = Field(default_factory=SpecObservability)

    def to_wire(self) -> dict[str, Any]:
        """Dict with the aliased (camelCase) keys, ready to dump as YAML."""
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")

    def to_yaml(self) -> str:
        import yaml

        return yaml.safe_dump(self.to_wire(), sort_keys=False, default_flow_style=False)


# ---------------------------------------------------------------------------
# openCenter plan / artefacts (section 16)
# ---------------------------------------------------------------------------


class PlanChange(BaseModel):
    """One line of "WHAT WILL BE CREATED"."""

    action: str = "create"  # create | update | delete | noop
    kind: str
    name: str
    detail: str | None = None

    def render(self) -> str:
        sign = {"create": "+", "update": "~", "delete": "-", "noop": "="}.get(self.action, "+")
        tail = f" ({self.detail})" if self.detail else ""
        return f"{sign} {self.kind} {self.name}{tail}"


class ValidationIssue(BaseModel):
    severity: str = "error"  # error | warning | info
    code: str
    message: str


class DeploymentArtifact(BaseModel):
    """A generated configuration file destined for the GitOps repository."""

    path: str
    content: str
    language: str = "yaml"
    description: str | None = None


class DeploymentPlan(BaseModel):
    id: str = Field(default_factory=lambda: _uid("plan"))
    deployment_id: str
    created_at: datetime = Field(default_factory=_now)
    cluster_id: str
    changes: list[PlanChange] = Field(default_factory=list)
    artifacts: list[DeploymentArtifact] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    gitops: dict[str, Any] = Field(default_factory=dict)
    estimated_duration_seconds: int = 60
    valid: bool = True

    def rendered_changes(self) -> list[str]:
        return [c.render() for c in self.changes]


class Placement(BaseModel):
    """Where the *native* scheduler decided to put the workload.

    The Governor never chooses this -- Nova or kube-scheduler does. Replicas are
    placed individually, exactly as a real scheduler would, so `nodes` can hold
    several hosts while `node` names the first one.
    """

    scheduler: str
    node: str
    platform: str
    nodes: list[str] = Field(default_factory=list)
    gpu_ids: list[str] = Field(default_factory=list)
    #: Per-replica record, used to release exactly what was taken.
    allocations: list[dict[str, Any]] = Field(default_factory=list)
    reason: str | None = None


# ---------------------------------------------------------------------------
# Lifecycle records (section 14)
# ---------------------------------------------------------------------------


class Actor(BaseModel):
    kind: ActorKind = ActorKind.HUMAN
    name: str = "admin"

    @classmethod
    def human(cls, name: str = "admin") -> "Actor":
        return cls(kind=ActorKind.HUMAN, name=name)

    @classmethod
    def ai(cls, name: str = "ai-governor") -> "Actor":
        return cls(kind=ActorKind.AI, name=name)

    @classmethod
    def system(cls, name: str = "system") -> "Actor":
        return cls(kind=ActorKind.SYSTEM, name=name)


class StateTransition(BaseModel):
    at: datetime = Field(default_factory=_now)
    from_state: DeploymentState | None = None
    to_state: DeploymentState
    actor: Actor
    note: str | None = None


class DeploymentEvent(BaseModel):
    id: str = Field(default_factory=lambda: _uid("evt"))
    at: datetime = Field(default_factory=_now)
    deployment_id: str
    level: str = "info"  # info | warning | error | success
    source: str = "opencenter"
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class Deployment(BaseModel):
    id: str = Field(default_factory=lambda: _uid("dep"))
    name: str
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    state: DeploymentState = DeploymentState.DRAFT
    conversation_id: str | None = None
    recommendation_id: str | None = None
    selected_option: str | None = None
    runtime: RuntimeType
    intent: WorkloadIntent
    option: RecommendationOption
    spec: DeploymentSpec | None = None
    plan: DeploymentPlan | None = None
    placement: Placement | None = None
    history: list[StateTransition] = Field(default_factory=list)
    events: list[DeploymentEvent] = Field(default_factory=list)
    opencenter_run_id: str | None = None
    progress: int = 0
    health: str = "unknown"
    failure_reason: str | None = None
    allocated_gpu_ids: list[str] = Field(default_factory=list)

    @property
    def is_active(self) -> bool:
        from .enums import ACTIVE_STATES

        return self.state in ACTIVE_STATES


class DeploymentStatusReport(BaseModel):
    """What `OpenCenterAdapter.deployment_status()` returns."""

    deployment_id: str
    state: DeploymentState
    progress: int = 0
    health: str = "unknown"
    message: str = ""
    placement: Placement | None = None
    updated_at: datetime = Field(default_factory=_now)
