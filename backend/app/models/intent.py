"""The workload intent -- the structured form of "what the human wants".

This is the only thing the Copilot is allowed to produce. It carries *no*
infrastructure decisions: no runtime, no node, no flavor. Turning an intent
into infrastructure is the Governor's deterministic job.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from .enums import (
    Environment,
    IsolationLevel,
    NetworkExposure,
    OptimizationGoal,
    Sensitivity,
)


class WorkloadIntent(BaseModel):
    """Section 11 intent model (plus a few derived, non-user-facing hints)."""

    # --- identity -----------------------------------------------------------
    name: str | None = Field(
        default=None, description="Short DNS-safe name for the workload, e.g. llama-internal"
    )
    description: str | None = Field(default=None, description="Free-text summary of the request")

    # --- what is it ---------------------------------------------------------
    workload_type: str = Field(
        default="generic",
        description="llm-inference | web-api | database | ml-training | ai-dev-workstation | "
        "legacy-app | generic",
    )
    environment: Environment = Environment.DEV
    operating_system: str | None = None

    # --- compute ------------------------------------------------------------
    cpu_required: bool = True
    gpu_required: bool = False
    estimated_vcpu: int | None = Field(default=None, ge=1, le=1024)
    estimated_ram_gb: int | None = Field(default=None, ge=1, le=16384)
    gpu_count: int | None = Field(default=None, ge=0, le=64)
    gpu_memory_gb: int | None = Field(
        default=None, ge=1, le=1024, description="Minimum VRAM per GPU"
    )
    preferred_gpu: str | None = None
    storage_gb: int | None = Field(default=None, ge=1, le=1_000_000)

    # --- non functional -----------------------------------------------------
    high_availability: bool = False
    sensitive_data: Sensitivity = Sensitivity.INTERNAL
    internet_access: bool = False
    network_exposure: NetworkExposure = NetworkExposure.INTERNAL
    expected_users: int | None = Field(default=None, ge=1)
    concurrent_users: int | None = Field(default=None, ge=1)
    workload_isolation: IsolationLevel = IsolationLevel.MEDIUM
    optimization_goal: OptimizationGoal = OptimizationGoal.BALANCED
    latency_sensitive: bool = False

    # --- derived hints (set by the extractor, never asked directly) ---------
    container_compatible: bool | None = Field(
        default=None,
        description="False for workloads that genuinely need a full OS "
        "(legacy software, kernel modules, Windows, root-level dev boxes).",
    )
    replicas: int | None = Field(default=None, ge=1, le=64)

    @field_validator("name")
    @classmethod
    def _dns_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = "".join(c if c.isalnum() or c == "-" else "-" for c in value.lower())
        cleaned = "-".join(part for part in cleaned.split("-") if part)
        return cleaned[:40] or None

    @field_validator("preferred_gpu")
    @classmethod
    def _normalise_gpu(cls, value: str | None) -> str | None:
        return value.strip().upper() if value else None

    def merged_with(self, updates: dict[str, Any]) -> "WorkloadIntent":
        """Return a copy with `updates` applied, ignoring `None` values."""
        clean = {k: v for k, v in updates.items() if v is not None}
        return self.model_copy(update=clean)


class IntentPatch(BaseModel):
    """A partial intent update, produced by the LLM or by wizard answers.

    Everything is optional -- the Copilot fills the picture in over several
    turns and we never want a half-answered form to fail validation.
    """

    model_config = {"extra": "ignore"}

    name: str | None = None
    description: str | None = None
    workload_type: str | None = None
    environment: Environment | None = None
    operating_system: str | None = None
    cpu_required: bool | None = None
    gpu_required: bool | None = None
    estimated_vcpu: int | None = None
    estimated_ram_gb: int | None = None
    gpu_count: int | None = None
    gpu_memory_gb: int | None = None
    preferred_gpu: str | None = None
    storage_gb: int | None = None
    high_availability: bool | None = None
    sensitive_data: Sensitivity | None = None
    internet_access: bool | None = None
    network_exposure: NetworkExposure | None = None
    expected_users: int | None = None
    concurrent_users: int | None = None
    workload_isolation: IsolationLevel | None = None
    optimization_goal: OptimizationGoal | None = None
    latency_sensitive: bool | None = None
    container_compatible: bool | None = None
    replicas: int | None = None

    def as_updates(self) -> dict[str, Any]:
        return {k: v for k, v in self.model_dump(exclude_none=True).items()}


class WizardQuestion(BaseModel):
    """One question the Copilot needs answered before it can decide."""

    field: str
    question: str
    why: str = Field(description="Why this answer changes the recommendation")
    kind: str = Field(default="text", description="text | number | choice | boolean")
    options: list[str] = Field(default_factory=list)
    priority: int = Field(default=50, description="Lower = asked earlier")
    default: Any | None = None
