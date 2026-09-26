"""Shared enumerations for the whole platform.

Keeping every closed vocabulary in one module means the policy engine, the
recommendation engine, the adapters and the API all speak exactly the same
language -- and the frontend gets a single, stable set of string constants.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """`str` mixin so FastAPI/Pydantic serialise these as plain strings."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


# ---------------------------------------------------------------------------
# Platforms and runtimes
# ---------------------------------------------------------------------------


class Platform(StrEnum):
    KUBERNETES = "kubernetes"
    OPENSTACK = "openstack"
    CEPH = "ceph"
    OPENCENTER = "opencenter"


class RuntimeType(StrEnum):
    """Candidate execution models the Governor can recommend.

    `VMWARE_VM` and `BARE_METAL` are declared but not enabled -- they exist so
    the scoring/spec layers are already shaped for them (section 13 "Future").
    """

    OPENSTACK_CPU_VM = "OPENSTACK_CPU_VM"
    OPENSTACK_GPU_VM = "OPENSTACK_GPU_VM"
    KUBERNETES_CPU = "KUBERNETES_CPU"
    KUBERNETES_GPU = "KUBERNETES_GPU"
    DATABASE_SERVICE = "DATABASE_SERVICE"
    VMWARE_VM = "VMWARE_VM"
    BARE_METAL = "BARE_METAL"

    @property
    def platform(self) -> Platform:
        if self in (RuntimeType.OPENSTACK_CPU_VM, RuntimeType.OPENSTACK_GPU_VM):
            return Platform.OPENSTACK
        return Platform.KUBERNETES

    @property
    def uses_gpu(self) -> bool:
        return self in (RuntimeType.OPENSTACK_GPU_VM, RuntimeType.KUBERNETES_GPU)

    @property
    def is_vm(self) -> bool:
        return self in (
            RuntimeType.OPENSTACK_CPU_VM,
            RuntimeType.OPENSTACK_GPU_VM,
            RuntimeType.VMWARE_VM,
        )

    @property
    def is_container(self) -> bool:
        return self in (
            RuntimeType.KUBERNETES_CPU,
            RuntimeType.KUBERNETES_GPU,
            RuntimeType.DATABASE_SERVICE,
        )

    @property
    def native_scheduler(self) -> str:
        """The scheduler that keeps the final node-placement authority."""
        return "nova-scheduler" if self.platform is Platform.OPENSTACK else "kube-scheduler"


#: Runtimes the POC can actually deploy. Everything else is declared-but-off.
ENABLED_RUNTIMES: tuple[RuntimeType, ...] = (
    RuntimeType.OPENSTACK_CPU_VM,
    RuntimeType.OPENSTACK_GPU_VM,
    RuntimeType.KUBERNETES_CPU,
    RuntimeType.KUBERNETES_GPU,
    RuntimeType.DATABASE_SERVICE,
)


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------


class GPUStatus(StrEnum):
    AVAILABLE = "available"
    ALLOCATED = "allocated"
    RESERVED = "reserved"
    UNHEALTHY = "unhealthy"


class GPUAllocationType(StrEnum):
    """How a GPU is handed to a workload.

    The POC implements `passthrough` (VFIO) and `timeslice` (the Kubernetes
    device plugin default). The others are declared so adapters can grow into
    them without a schema change.
    """

    PASSTHROUGH = "passthrough"
    TIMESLICE = "timeslice"
    VGPU = "vgpu"
    MIG = "mig"
    SRIOV = "sriov"


# ---------------------------------------------------------------------------
# Intent vocabulary
# ---------------------------------------------------------------------------


class Environment(StrEnum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class IsolationLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OptimizationGoal(StrEnum):
    COST = "cost"
    BALANCED = "balanced"
    PERFORMANCE = "performance"


class NetworkExposure(StrEnum):
    INTERNAL = "internal"
    ORGANISATION = "organisation"
    PUBLIC = "public"


# ---------------------------------------------------------------------------
# Deployment lifecycle (section 14)
# ---------------------------------------------------------------------------


class DeploymentState(StrEnum):
    DRAFT = "DRAFT"
    RECOMMENDED = "RECOMMENDED"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    APPROVED = "APPROVED"
    PLANNED = "PLANNED"
    WAITING_FOR_FINAL_APPROVAL = "WAITING_FOR_FINAL_APPROVAL"
    DEPLOYING = "DEPLOYING"
    RUNNING = "RUNNING"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"


#: States in which the workload is consuming inventory.
ACTIVE_STATES: frozenset[DeploymentState] = frozenset(
    {DeploymentState.DEPLOYING, DeploymentState.RUNNING, DeploymentState.ROLLING_BACK}
)


class ActorKind(StrEnum):
    HUMAN = "human"
    AI = "ai"
    SYSTEM = "system"


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class PolicyEffect(StrEnum):
    """What a policy decision does to a candidate runtime."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE = "require"
    PREFER = "prefer"
    DISCOURAGE = "discourage"
    WARN = "warn"
    INFO = "info"
