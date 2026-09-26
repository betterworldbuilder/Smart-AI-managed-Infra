"""Post-deployment Copilot (section 22) and remediation flow (section 23).

The AI keeps advising after the deployment is live -- and keeps its hands off.
Every recommendation is a proposal with SIMULATE / IGNORE actions; nothing is
applied without a human asking for it.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from ..audit import AuditService
from ..events import EventBus
from ..models import (
    ActorKind,
    AdvisorRecommendation,
    AuditAction,
    Deployment,
    DeploymentState,
    GPUStatus,
    RemediationAlert,
    RemediationOption,
    RuntimeType,
    WorkloadMetrics,
)
from ..store import Repositories
from .metrics import MetricsService

log = logging.getLogger(__name__)

#: Below this GPU utilisation a workload is a right-sizing candidate.
GPU_UNDERUSE_PCT = 35.0
GPU_OVERLOAD_PCT = 85.0
CPU_OVERLOAD_PCT = 80.0


class PostDeploymentAdvisor:
    def __init__(
        self,
        metrics: MetricsService,
        repositories: Repositories,
        audit: AuditService,
        bus: EventBus,
    ) -> None:
        self.metrics = metrics
        self.repositories = repositories
        self.audit = audit
        self.bus = bus

    # -- generation ----------------------------------------------------------

    async def analyse(self) -> list[AdvisorRecommendation]:
        """Look at every running workload and propose improvements."""
        deployments = await self.repositories.deployments.list()
        existing = await self.repositories.advice.list()
        open_by_deployment = {
            advice.deployment_id
            for advice in existing
            if advice.status == "open"
        }

        produced: list[AdvisorRecommendation] = []
        for deployment in deployments:
            if deployment.state is not DeploymentState.RUNNING:
                continue
            if deployment.id in open_by_deployment:
                continue
            workload = await self.metrics.workload_metrics(deployment)
            advice = self._evaluate(deployment, workload)
            if advice is None:
                continue
            await self.repositories.advice.save(advice)
            await self.audit.record(
                action=AuditAction.POST_DEPLOY_RECOMMENDATION,
                actor_kind=ActorKind.AI,
                user="ai-copilot",
                deployment=deployment.id,
                detail={"current": advice.current, "proposed": advice.proposed},
                message=advice.title,
            )
            self.bus.publish("advisor", advice.model_dump(mode="json"))
            produced.append(advice)
        return produced

    def _evaluate(
        self, deployment: Deployment, workload: WorkloadMetrics
    ) -> AdvisorRecommendation | None:
        plan = deployment.option.resources

        # 1. Over-provisioned GPUs.
        if (
            plan.total_gpu > 1
            and workload.gpu_utilization_pct is not None
            and workload.gpu_utilization_pct < GPU_UNDERUSE_PCT
            and (workload.gpu_memory_pct or 0) < 50
        ):
            proposed_gpus = max(1, plan.total_gpu - 1)
            return AdvisorRecommendation(
                deployment_id=deployment.id,
                kind="rightsizing",
                title=f"{deployment.name}: GPU allocation looks oversized",
                observation=[
                    f"GPU utilisation: {workload.gpu_utilization_pct}%",
                    f"GPU memory: {workload.gpu_memory_pct}%",
                    f"Requests: {workload.requests_per_second}/s (low)",
                ],
                recommendation=(
                    f"Move from {plan.total_gpu} x {plan.gpu_model} to "
                    f"{proposed_gpus} x {plan.gpu_model}. Estimated capacity remains "
                    "sufficient for the observed load."
                ),
                current={
                    "gpu": plan.total_gpu,
                    "gpu_model": plan.gpu_model,
                    "replicas": plan.replicas,
                    "memory_gb": plan.total_ram_gb,
                },
                proposed={
                    "gpu": proposed_gpus,
                    "gpu_model": plan.gpu_model,
                    "replicas": max(1, proposed_gpus) if plan.gpu_per_replica == 1 else plan.replicas,
                    "memory_gb": plan.total_ram_gb,
                },
                estimated_savings=f"{plan.total_gpu - proposed_gpus} x {plan.gpu_model} returned "
                "to the shared pool",
            )

        # 2. Single GPU but barely used -- suggest a cheaper card.
        if (
            plan.total_gpu == 1
            and workload.gpu_utilization_pct is not None
            and workload.gpu_utilization_pct < 20
        ):
            return AdvisorRecommendation(
                deployment_id=deployment.id,
                kind="cost",
                severity="info",
                title=f"{deployment.name}: GPU is mostly idle",
                observation=[
                    f"GPU utilisation: {workload.gpu_utilization_pct}%",
                    f"GPU memory: {workload.gpu_memory_pct}%",
                ],
                recommendation=(
                    "Consider a smaller GPU class or time-slicing this workload with another "
                    "tenant. Keeping a full card reserved for this load is expensive."
                ),
                current={"gpu_model": plan.gpu_model, "gpu": 1},
                proposed={"gpu_model": "smallest compatible", "gpu": 1, "sharing": "time-slicing"},
            )

        # 3. Saturated GPU -- scale out.
        if (
            workload.gpu_utilization_pct is not None
            and workload.gpu_utilization_pct > GPU_OVERLOAD_PCT
        ):
            return AdvisorRecommendation(
                deployment_id=deployment.id,
                kind="scaling",
                severity="warning",
                title=f"{deployment.name}: GPU saturated",
                observation=[f"GPU utilisation: {workload.gpu_utilization_pct}%"],
                recommendation=f"Add one replica ({plan.gpu_per_replica} x {plan.gpu_model}) "
                "to keep latency stable.",
                current={"replicas": plan.replicas, "gpu": plan.total_gpu},
                proposed={
                    "replicas": plan.replicas + 1,
                    "gpu": plan.total_gpu + plan.gpu_per_replica,
                },
            )

        # 4. CPU-bound workload.
        if not plan.total_gpu and workload.cpu_utilization_pct > CPU_OVERLOAD_PCT:
            return AdvisorRecommendation(
                deployment_id=deployment.id,
                kind="scaling",
                severity="warning",
                title=f"{deployment.name}: CPU saturated",
                observation=[f"CPU utilisation: {workload.cpu_utilization_pct}%"],
                recommendation="Increase the replica count or the CPU request.",
                current={"replicas": plan.replicas, "vcpu": plan.total_vcpu},
                proposed={"replicas": plan.replicas + 1, "vcpu": plan.total_vcpu + plan.vcpu_per_replica},
            )

        # 5. Single replica in production.
        if (
            plan.replicas == 1
            and deployment.intent.environment.value == "prod"
            and deployment.runtime.is_container
        ):
            return AdvisorRecommendation(
                deployment_id=deployment.id,
                kind="reliability",
                title=f"{deployment.name}: single replica in production",
                observation=["replicas: 1", "a node drain interrupts the service"],
                recommendation="Run at least two replicas with anti-affinity.",
                current={"replicas": 1},
                proposed={"replicas": 2, "anti_affinity": True},
            )
        return None

    # -- human actions -------------------------------------------------------

    async def list(self) -> list[AdvisorRecommendation]:
        advice = await self.repositories.advice.list()
        return sorted(advice, key=lambda item: item.created_at, reverse=True)

    async def simulate(self, advice_id: str, user: str = "admin") -> AdvisorRecommendation:
        """Show what *would* happen. Changes nothing."""
        advice = await self.repositories.advice.require(advice_id)
        deployment = await self.repositories.deployments.get(advice.deployment_id)
        current_gpu = int(advice.current.get("gpu", 0) or 0)
        proposed_gpu = int(advice.proposed.get("gpu", current_gpu) or 0)

        advice.simulation = {
            "gpu_released": max(current_gpu - proposed_gpu, 0),
            "gpu_added": max(proposed_gpu - current_gpu, 0),
            "projected_gpu_utilization_pct": _project_utilisation(advice),
            "projected_state": "healthy",
            "capacity_after": {
                "gpu_free_delta": max(current_gpu - proposed_gpu, 0),
            },
            "requires_restart": bool(deployment and deployment.runtime.is_container),
            "note": "Simulation only -- nothing was changed. Applying this requires a new "
            "recommendation and the usual two approvals.",
        }
        advice.status = "simulated"
        await self.repositories.advice.save(advice)
        await self.audit.record(
            action=AuditAction.ADVISOR_DECISION,
            user=user,
            actor_kind=ActorKind.HUMAN,
            deployment=advice.deployment_id,
            detail={"advice": advice.id, "decision": "simulate", "result": advice.simulation},
            message=f"{user} simulated '{advice.title}'",
        )
        self.bus.publish("advisor", advice.model_dump(mode="json"))
        return advice

    async def ignore(self, advice_id: str, user: str = "admin") -> AdvisorRecommendation:
        advice = await self.repositories.advice.require(advice_id)
        advice.status = "ignored"
        await self.repositories.advice.save(advice)
        await self.audit.record(
            action=AuditAction.ADVISOR_DECISION,
            user=user,
            actor_kind=ActorKind.HUMAN,
            deployment=advice.deployment_id,
            detail={"advice": advice.id, "decision": "ignore"},
            message=f"{user} ignored '{advice.title}'",
        )
        self.bus.publish("advisor", advice.model_dump(mode="json"))
        return advice


def _project_utilisation(advice: AdvisorRecommendation) -> float:
    observed = 0.0
    for line in advice.observation:
        if "GPU utilisation" in line:
            try:
                observed = float(line.split(":")[1].strip().rstrip("%"))
            except (IndexError, ValueError):
                observed = 0.0
    current = max(int(advice.current.get("gpu", 1) or 1), 1)
    proposed = max(int(advice.proposed.get("gpu", current) or current), 1)
    return round(min(observed * current / proposed, 99.0), 1)


# ---------------------------------------------------------------------------
# Remediation (section 23)
# ---------------------------------------------------------------------------


class RemediationService:
    """Turns platform alerts into options a human chooses between."""

    def __init__(
        self,
        repositories: Repositories,
        audit: AuditService,
        bus: EventBus,
        metrics: MetricsService,
    ) -> None:
        self.repositories = repositories
        self.audit = audit
        self.bus = bus
        self.metrics = metrics

    async def list(self) -> list[RemediationAlert]:
        alerts = await self.repositories.alerts.list()
        return sorted(alerts, key=lambda alert: alert.at, reverse=True)

    async def ingest(self, alert: RemediationAlert) -> RemediationAlert:
        """Accept a Prometheus-style alert and attach recommended actions."""
        if not alert.options:
            alert.options, alert.recommended_option = _options_for(alert)
        await self.repositories.alerts.save(alert)
        await self.audit.record(
            action=AuditAction.POST_DEPLOY_RECOMMENDATION,
            actor_kind=ActorKind.AI,
            user="ai-governor",
            detail={"alert": alert.alertname, "resource": alert.resource},
            message=f"alert {alert.alertname} on {alert.resource}: "
            f"recommended option {alert.recommended_option}",
        )
        self.bus.publish("alert", alert.model_dump(mode="json"))
        return alert

    async def simulate_gpu_failure(self, gpu_id: str | None = None) -> RemediationAlert:
        """Demo hook: raise a realistic unhealthy-GPU alert."""
        cluster = await self.metrics.cluster_metrics()
        candidates = [gpu for gpu in cluster.gpus if gpu.status == "allocated"] or cluster.gpus
        if not candidates:
            raise ValueError("no GPUs in inventory")
        target = next((g for g in candidates if g.gpu_id == gpu_id), None) or random.choice(
            candidates
        )
        alert = RemediationAlert(
            severity="critical",
            alertname="GPUWorkerUnhealthy",
            resource=target.host,
            summary=f"GPU worker {target.host} reports an unhealthy {target.model} "
            f"({target.gpu_id}); DCGM health check failed.",
            context={
                "gpu_id": target.gpu_id,
                "model": target.model,
                "temperature_c": target.temperature_c,
                "workload": target.workload,
            },
        )
        return await self.ingest(alert)

    async def decide(
        self, alert_id: str, option_key: str, user: str = "admin"
    ) -> RemediationAlert:
        alert = await self.repositories.alerts.require(alert_id)
        chosen = next((o for o in alert.options if o.key == option_key), None)
        if chosen is None and option_key not in ("INVESTIGATE", "IGNORE"):
            raise KeyError(f"unknown remediation option '{option_key}'")
        alert.decision = option_key
        alert.status = {
            "IGNORE": "ignored",
            "INVESTIGATE": "investigating",
        }.get(option_key, "approved")
        await self.repositories.alerts.save(alert)
        await self.audit.record(
            action=AuditAction.REMEDIATION_DECISION,
            user=user,
            actor_kind=ActorKind.HUMAN,
            detail={
                "alert": alert.alertname,
                "resource": alert.resource,
                "decision": option_key,
                "recommended": alert.recommended_option,
            },
            message=f"{user} chose '{option_key}' for {alert.alertname}",
        )
        self.bus.publish("alert", alert.model_dump(mode="json"))
        return alert


def _options_for(alert: RemediationAlert) -> tuple[list[RemediationOption], str]:
    if alert.alertname == "GPUWorkerUnhealthy":
        return (
            [
                RemediationOption(
                    key="A",
                    title="Drain the node and reschedule its workloads",
                    description="Cordon the node, evict GPU pods, let the scheduler place them "
                    "on healthy GPU workers.",
                    impact="Short interruption for the affected workloads; capacity drops by "
                    "the node's GPUs until it returns.",
                    recommended=True,
                ),
                RemediationOption(
                    key="B",
                    title="Restart the GPU operator on the node",
                    description="Roll the device plugin and driver daemonset pods.",
                    impact="Faster, but leaves the workload on a node whose GPU just failed a "
                    "health check.",
                ),
                RemediationOption(
                    key="C",
                    title="Put the node into maintenance mode",
                    description="Cordon only: no new workloads, existing pods keep running.",
                    impact="No interruption now, but the unhealthy GPU stays in service.",
                ),
            ],
            "A",
        )
    return (
        [
            RemediationOption(
                key="A",
                title="Investigate before acting",
                description="Collect logs and metrics for the affected resource.",
                impact="No change to running infrastructure.",
                recommended=True,
            ),
            RemediationOption(
                key="B",
                title="Restart the affected workload",
                description="Roll the workload's pods or instances.",
                impact="Brief interruption.",
            ),
        ],
        "A",
    )


def advisor_context(advice: AdvisorRecommendation) -> dict[str, Any]:
    """Payload the Copilot uses to phrase the advice."""
    return {"task": "post_deploy", "advice": advice.model_dump(mode="json")}
