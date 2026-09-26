"""Recommendation output (section 13).

The engine always returns *all* viable options with their scores and reasons --
never a single answer. The human picks.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from .enums import GPUAllocationType, NetworkExposure, Platform, RuntimeType
from .intent import WizardQuestion, WorkloadIntent
from .policy import PolicyDecision


def _uid(prefix: str) -> str:
    from uuid import uuid4

    return f"{prefix}-{uuid4().hex[:10]}"


class ResourcePlan(BaseModel):
    """Concrete sizing for one candidate runtime."""

    runtime: RuntimeType
    replicas: int = 1
    vcpu_per_replica: int = 2
    ram_gb_per_replica: int = 4
    gpu_per_replica: int = 0
    gpu_model: str | None = None
    gpu_memory_gb: int | None = None
    gpu_allocation_type: GPUAllocationType | None = None
    storage_gb: int = 20
    storage_backend: str = "ceph-rbd"
    network_exposure: NetworkExposure = NetworkExposure.INTERNAL
    # Platform specific extras
    flavor: str | None = None
    image: str | None = None
    namespace: str | None = None
    database_engine: str | None = None
    notes: list[str] = Field(default_factory=list)

    @property
    def platform(self) -> Platform:
        return self.runtime.platform

    @property
    def total_vcpu(self) -> int:
        return self.vcpu_per_replica * self.replicas

    @property
    def total_ram_gb(self) -> int:
        return self.ram_gb_per_replica * self.replicas

    @property
    def total_gpu(self) -> int:
        return self.gpu_per_replica * self.replicas

    def summary(self) -> str:
        bits = [f"{self.replicas}x", f"{self.vcpu_per_replica} vCPU", f"{self.ram_gb_per_replica} GB RAM"]
        if self.total_gpu:
            bits.append(f"{self.gpu_per_replica}x {self.gpu_model or 'GPU'}")
        bits.append(f"{self.storage_gb} GB {self.storage_backend}")
        return ", ".join(bits)


class ScoreBreakdown(BaseModel):
    """Transparent scoring -- every number the engine used is visible."""

    compatibility: float = 0.0
    capacity: float = 0.0
    performance: float = 0.0
    isolation: float = 0.0
    reliability: float = 0.0
    efficiency: float = 0.0
    cost_penalty: float = 0.0
    scarcity_penalty: float = 0.0
    policy_delta: float = 0.0

    @property
    def total(self) -> float:
        return round(
            self.compatibility
            + self.capacity
            + self.performance
            + self.isolation
            + self.reliability
            + self.efficiency
            - self.cost_penalty
            - self.scarcity_penalty
            + self.policy_delta,
            2,
        )


class CapacityImpact(BaseModel):
    """What this option would consume versus what is free right now."""

    vcpu_required: int = 0
    vcpu_available: int = 0
    ram_gb_required: int = 0
    ram_gb_available: int = 0
    gpu_required: int = 0
    gpu_available: int = 0
    storage_gb_required: int = 0
    storage_gb_available: int = 0
    fits: bool = True


class RecommendationOption(BaseModel):
    option: str  # e.g. "kubernetes_gpu" -- the stable key used by the UI
    runtime: RuntimeType
    title: str
    score: float
    viable: bool = True
    recommended: bool = False
    reason: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    resources: ResourcePlan
    breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    capacity_impact: CapacityImpact = Field(default_factory=CapacityImpact)
    policy_decisions: list[PolicyDecision] = Field(default_factory=list)
    relative_cost_units: float = 0.0
    native_scheduler: str = "kube-scheduler"


class Recommendation(BaseModel):
    """A full Governor answer: options + the intent it was derived from."""

    id: str = Field(default_factory=lambda: _uid("rec"))
    conversation_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    intent: WorkloadIntent
    options: list[RecommendationOption] = Field(default_factory=list)
    rejected_options: list[RecommendationOption] = Field(default_factory=list)
    open_questions: list[WizardQuestion] = Field(default_factory=list)
    summary: str = ""
    inventory_snapshot: dict = Field(default_factory=dict)
    policy_engine: str = "internal"

    @property
    def top(self) -> RecommendationOption | None:
        return self.options[0] if self.options else None

    def option_by_key(self, key: str) -> RecommendationOption | None:
        for option in [*self.options, *self.rejected_options]:
            if option.option == key or str(option.runtime) == key:
                return option
        return None
