"""Metrics, post-deployment advice and remediation models (sections 21-23)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MetricPoint(BaseModel):
    at: datetime
    value: float


class MetricSeries(BaseModel):
    name: str
    unit: str = "%"
    points: list[MetricPoint] = Field(default_factory=list)

    @property
    def latest(self) -> float:
        return self.points[-1].value if self.points else 0.0


class GPUMetric(BaseModel):
    gpu_id: str
    model: str
    host: str
    utilization_pct: float = 0.0
    memory_used_gb: float = 0.0
    memory_total_gb: float = 0.0
    temperature_c: float | None = None
    workload: str | None = None
    status: str = "available"


class ClusterMetrics(BaseModel):
    """Everything the observability dashboard needs in one payload."""

    generated_at: datetime = Field(default_factory=_now)
    cpu_utilization_pct: float = 0.0
    ram_utilization_pct: float = 0.0
    gpu_utilization_pct: float = 0.0
    gpu_memory_pct: float = 0.0
    storage_utilization_pct: float = 0.0
    gpu_allocated: int = 0
    gpu_total: int = 0
    openstack_vm_count: int = 0
    openstack_gpu_vm_count: int = 0
    kubernetes_pod_count: int = 0
    kubernetes_gpu_pod_count: int = 0
    database_count: int = 0
    failed_workloads: int = 0
    running_deployments: int = 0
    alerts: int = 0
    gpus: list[GPUMetric] = Field(default_factory=list)
    series: dict[str, MetricSeries] = Field(default_factory=dict)


class WorkloadMetrics(BaseModel):
    deployment_id: str
    name: str
    runtime: str
    healthy: bool = True
    cpu_utilization_pct: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    gpu_utilization_pct: float | None = None
    gpu_memory_pct: float | None = None
    storage_used_gb: float = 0.0
    storage_total_gb: float = 0.0
    requests_per_second: float = 0.0
    series: dict[str, MetricSeries] = Field(default_factory=dict)


class AdvisorRecommendation(BaseModel):
    """Post-deployment optimisation advice (section 22). Never auto-applied."""

    id: str = Field(default_factory=lambda: f"adv-{uuid4().hex[:8]}")
    deployment_id: str
    created_at: datetime = Field(default_factory=_now)
    kind: str = "rightsizing"  # rightsizing | scaling | placement | cost | reliability
    severity: str = "info"  # info | warning
    title: str
    observation: list[str] = Field(default_factory=list)
    recommendation: str = ""
    current: dict[str, Any] = Field(default_factory=dict)
    proposed: dict[str, Any] = Field(default_factory=dict)
    estimated_savings: str | None = None
    actions: list[str] = Field(default_factory=lambda: ["SIMULATE", "IGNORE"])
    status: str = "open"  # open | simulated | ignored | applied
    simulation: dict[str, Any] | None = None


class RemediationOption(BaseModel):
    key: str
    title: str
    description: str
    impact: str
    recommended: bool = False


class RemediationAlert(BaseModel):
    """A Prometheus-style alert routed to the Governor (section 23)."""

    id: str = Field(default_factory=lambda: f"alert-{uuid4().hex[:8]}")
    at: datetime = Field(default_factory=_now)
    severity: str = "warning"  # info | warning | critical
    source: str = "prometheus"
    alertname: str
    resource: str
    summary: str
    context: dict[str, Any] = Field(default_factory=dict)
    options: list[RemediationOption] = Field(default_factory=list)
    recommended_option: str | None = None
    status: str = "open"  # open | approved | investigating | ignored | resolved
    decision: str | None = None
