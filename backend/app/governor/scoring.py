"""Deterministic scoring (section 13).

    score = compatibility + capacity + performance + isolation + reliability
          + efficiency - cost_penalty - scarcity_penalty + policy_delta

No LLM is involved. Each component also produces a human-readable reason, so
the "WHY" the UI shows is generated from the same numbers that ranked the
options -- never from a model's prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..models import (
    CapacityImpact,
    GlobalInventory,
    IsolationLevel,
    Platform,
    PolicyEvaluation,
    ResourcePlan,
    RuntimeType,
    ScoreBreakdown,
    WorkloadIntent,
)
from .sizing import WorkloadProfile

SCORING_FILE = Path(__file__).with_name("scoring.yaml")


@lru_cache
def load_scoring(path: str | None = None) -> dict[str, Any]:
    file = Path(path) if path else SCORING_FILE
    return yaml.safe_load(file.read_text(encoding="utf-8"))


@dataclass
class ScoreResult:
    breakdown: ScoreBreakdown
    reasons: list[str]
    risks: list[str]
    capacity_impact: CapacityImpact
    relative_cost_units: float


class Scorer:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or load_scoring()
        self.weights = self.config["weights"]

    # -- entry point ---------------------------------------------------------

    def score(
        self,
        *,
        intent: WorkloadIntent,
        plan: ResourcePlan,
        runtime: RuntimeType,
        profile: WorkloadProfile,
        inventory: GlobalInventory,
        policy: PolicyEvaluation,
    ) -> ScoreResult:
        reasons: list[str] = []
        risks: list[str] = []
        goal_cfg = self.config["optimization_goal"][str(intent.optimization_goal)]

        compatibility = self._compatibility(runtime, profile, intent, reasons)
        capacity, impact = self._capacity(plan, runtime, inventory, reasons, risks)
        performance = self._performance(plan, runtime, intent, inventory, reasons) * float(
            goal_cfg["performance_multiplier"]
        )
        isolation = self._isolation(runtime, intent, reasons)
        reliability = self._reliability(plan, runtime, intent, reasons, risks)
        efficiency = self._efficiency(runtime, reasons)
        cost_penalty, cost_units = self._cost(plan, runtime, inventory, reasons)
        cost_penalty *= float(goal_cfg["cost_penalty_multiplier"])
        scarcity_penalty = self._scarcity(plan, runtime, inventory, reasons, risks)

        for decision in policy.decisions:
            if decision.score_delta:
                reasons.append(f"Policy {decision.rule_id}: {decision.message}")

        breakdown = ScoreBreakdown(
            compatibility=round(compatibility, 2),
            capacity=round(capacity, 2),
            performance=round(performance, 2),
            isolation=round(isolation, 2),
            reliability=round(reliability, 2),
            efficiency=round(efficiency, 2),
            cost_penalty=round(cost_penalty, 2),
            scarcity_penalty=round(scarcity_penalty, 2),
            policy_delta=round(policy.score_delta, 2),
        )
        return ScoreResult(
            breakdown=breakdown,
            reasons=reasons,
            risks=risks,
            capacity_impact=impact,
            relative_cost_units=round(cost_units, 2),
        )

    # -- components ----------------------------------------------------------

    def _compatibility(
        self,
        runtime: RuntimeType,
        profile: WorkloadProfile,
        intent: WorkloadIntent,
        reasons: list[str],
    ) -> float:
        affinity = profile.affinity(runtime)
        if affinity >= 0.95:
            reasons.append(f"{profile.display} maps naturally onto {_label(runtime)}")
        elif affinity >= 0.6:
            reasons.append(f"{_label(runtime)} is a workable fit for {profile.display}")
        elif affinity > 0:
            reasons.append(f"{_label(runtime)} is an unusual choice for {profile.display}")
        if intent.container_compatible is True and runtime.is_container:
            affinity = min(1.0, affinity + 0.05)
        if intent.container_compatible is False and runtime.is_vm:
            reasons.append(
                "The workload cannot be containerised, so it needs a full operating system"
            )
        return self.weights["compatibility"] * affinity

    def _capacity(
        self,
        plan: ResourcePlan,
        runtime: RuntimeType,
        inventory: GlobalInventory,
        reasons: list[str],
        risks: list[str],
    ) -> tuple[float, CapacityImpact]:
        platform = inventory.platform_of(runtime.platform)
        gpu_available = len(
            [
                d
                for d in inventory.available_gpus(runtime.platform)
                if plan.gpu_model is None or d.model == plan.gpu_model
            ]
        )
        impact = CapacityImpact(
            vcpu_required=plan.total_vcpu,
            vcpu_available=int(platform.cpu.available),
            ram_gb_required=plan.total_ram_gb,
            ram_gb_available=int(platform.ram_gb.available),
            gpu_required=plan.total_gpu,
            gpu_available=gpu_available,
            storage_gb_required=plan.storage_gb,
            storage_gb_available=int(inventory.storage.available_tb * 1024),
            fits=True,
        )

        ratios: list[float] = []
        if plan.total_vcpu:
            ratios.append(_ratio(platform.cpu.available, plan.total_vcpu))
        if plan.total_ram_gb:
            ratios.append(_ratio(platform.ram_gb.available, plan.total_ram_gb))
        if plan.total_gpu:
            ratios.append(_ratio(gpu_available, plan.total_gpu))
        if plan.storage_gb:
            ratios.append(_ratio(inventory.storage.available_tb * 1024, plan.storage_gb))

        headroom = min(ratios) if ratios else 1.0
        impact.fits = headroom >= 1.0

        if plan.total_gpu:
            if gpu_available >= plan.total_gpu:
                reasons.append(
                    f"{gpu_available} x {plan.gpu_model} available on "
                    f"{_platform_label(runtime.platform)}"
                )
            else:
                risks.append(
                    f"only {gpu_available} x {plan.gpu_model} free, {plan.total_gpu} needed"
                )
        if not impact.fits:
            risks.append("does not fit current free capacity")
        elif headroom < 1.5:
            risks.append("leaves less than 50% headroom on the target platform")

        # Full marks at 2x headroom, linear below that.
        return self.weights["capacity"] * min(headroom / 2.0, 1.0), impact

    def _performance(
        self,
        plan: ResourcePlan,
        runtime: RuntimeType,
        intent: WorkloadIntent,
        inventory: GlobalInventory,
        reasons: list[str],
    ) -> float:
        cfg = self.config["performance"]
        overhead = cfg["vm_overhead"] if runtime.is_vm else cfg["container_overhead"]
        if not plan.total_gpu:
            return self.weights["performance"] * cfg["cpu_only"] * overhead

        devices = [
            d for d in inventory.gpu.devices if plan.gpu_model and d.model == plan.gpu_model
        ]
        required_memory = plan.gpu_memory_gb or 0
        if not devices:
            factor = cfg["gpu_tier_below"]
        elif devices[0].memory_gb >= required_memory * 1.5:
            factor = cfg["gpu_tier_above"]
            reasons.append(
                f"{plan.gpu_model} ({devices[0].memory_gb} GB) exceeds the "
                f"{required_memory} GB VRAM requirement"
            )
        elif devices[0].memory_gb >= required_memory:
            factor = cfg["gpu_tier_match"]
            reasons.append(
                f"{plan.gpu_model} meets the {required_memory} GB VRAM requirement"
            )
        else:
            factor = cfg["gpu_tier_below"]
        return self.weights["performance"] * factor * overhead

    def _isolation(
        self, runtime: RuntimeType, intent: WorkloadIntent, reasons: list[str]
    ) -> float:
        matrix = self.config["isolation_matrix"][str(intent.workload_isolation)]
        key = "vm" if runtime.is_vm else "container"
        factor = float(matrix[key])
        if runtime.is_vm:
            reasons.append(
                "Dedicated VM provides kernel-level tenant isolation"
                if intent.workload_isolation is IsolationLevel.HIGH
                else "Provides stronger isolation than a shared-kernel container"
            )
        elif runtime.is_container and intent.workload_isolation is not IsolationLevel.HIGH:
            reasons.append("No strict VM isolation requirement")
        return self.weights["isolation"] * factor

    def _reliability(
        self,
        plan: ResourcePlan,
        runtime: RuntimeType,
        intent: WorkloadIntent,
        reasons: list[str],
        risks: list[str],
    ) -> float:
        cfg = self.config["reliability"]
        if runtime is RuntimeType.DATABASE_SERVICE:
            factor = cfg["database_service"]
            reasons.append(
                f"Operator-managed {plan.database_engine or 'PostgreSQL'}: automatic "
                "failover, backups and point-in-time recovery"
            )
        elif runtime.is_container:
            factor = cfg["container_ha"] if plan.replicas > 1 else cfg["container_single"]
            if plan.replicas > 1:
                reasons.append(f"{plan.replicas} replicas, self-healing via the platform")
        else:
            factor = cfg["vm_ha"] if intent.high_availability else cfg["vm_single"]
            if intent.high_availability:
                risks.append("VM high availability needs anti-affinity plus a load balancer")
        return self.weights["reliability"] * float(factor)

    def _efficiency(self, runtime: RuntimeType, reasons: list[str]) -> float:
        cfg = self.config["efficiency"]
        if runtime is RuntimeType.DATABASE_SERVICE:
            factor = cfg["database_service"]
        elif runtime.is_container:
            factor = cfg["container"]
            reasons.append("Container requests pack tightly onto existing nodes")
        else:
            factor = cfg["vm"]
        return self.weights["efficiency"] * float(factor)

    def _cost(
        self,
        plan: ResourcePlan,
        runtime: RuntimeType,
        inventory: GlobalInventory,
        reasons: list[str],
    ) -> tuple[float, float]:
        """Relative, unitless cost -- GPU dominates, VM overhead adds a little."""
        gpu_cost = 0.0
        max_cost = 1.0
        if plan.total_gpu and plan.gpu_model:
            devices = [d for d in inventory.gpu.devices if d.model == plan.gpu_model]
            unit = devices[0].relative_cost if devices else 1.0
            gpu_cost = unit * plan.total_gpu
            max_cost = max(
                (d.relative_cost for d in inventory.gpu.devices), default=1.0
            ) * max(plan.total_gpu, 1)
            if unit <= min((d.relative_cost for d in inventory.gpu.devices), default=unit):
                reasons.append(f"{plan.gpu_model} is the lowest-cost GPU that fits")
        cpu_cost = (plan.total_vcpu / 64.0) + (plan.total_ram_gb / 512.0)
        vm_overhead = 0.15 if runtime.is_vm else 0.0
        normalised = min((gpu_cost / max_cost if max_cost else 0) + vm_overhead, 1.0)
        units = round(gpu_cost + cpu_cost, 2)
        return self.weights["cost_penalty"] * normalised, units

    def _scarcity(
        self,
        plan: ResourcePlan,
        runtime: RuntimeType,
        inventory: GlobalInventory,
        reasons: list[str],
        risks: list[str],
    ) -> float:
        """Penalise consuming the last units of a scarce resource class."""
        if not plan.total_gpu or not plan.gpu_model:
            return 0.0
        counts = inventory.gpu_model_counts(runtime.platform).get(
            plan.gpu_model, {"total": 0, "available": 0}
        )
        available = counts["available"]
        if available <= 0:
            return self.weights["scarcity_penalty"]
        remaining_after = available - plan.total_gpu
        consumed_fraction = min(plan.total_gpu / available, 1.0)
        if remaining_after <= 0:
            risks.append(f"consumes the last free {plan.gpu_model}")
        # Higher-tier cards are the ones worth protecting.
        devices = [d for d in inventory.gpu.devices if d.model == plan.gpu_model]
        tier = devices[0].perf_tier if devices else 1
        max_tier = max((d.perf_tier for d in inventory.gpu.devices), default=tier)
        tier_weight = tier / max_tier if max_tier else 1.0

        cheaper_alternatives = [
            model
            for model, data in inventory.gpu_model_counts(runtime.platform).items()
            if data["available"] >= plan.total_gpu and model != plan.gpu_model
        ]
        if tier < max_tier and cheaper_alternatives:
            scarcest = [
                d.model
                for d in inventory.gpu.devices
                if d.perf_tier == max_tier and d.platform is runtime.platform
            ]
            if scarcest:
                reasons.append(f"Preserves {scarcest[0]} capacity for workloads that need it")
        return self.weights["scarcity_penalty"] * consumed_fraction * tier_weight


def _ratio(available: float, required: float) -> float:
    if required <= 0:
        return 2.0
    return float(available) / float(required)


def _label(runtime: RuntimeType) -> str:
    return {
        RuntimeType.KUBERNETES_GPU: "a Kubernetes GPU workload",
        RuntimeType.KUBERNETES_CPU: "a Kubernetes CPU workload",
        RuntimeType.OPENSTACK_GPU_VM: "an OpenStack GPU VM",
        RuntimeType.OPENSTACK_CPU_VM: "an OpenStack CPU VM",
        RuntimeType.DATABASE_SERVICE: "the managed database service",
    }.get(runtime, str(runtime))


def _platform_label(platform: Platform) -> str:
    return "Kubernetes" if platform is Platform.KUBERNETES else "OpenStack"
