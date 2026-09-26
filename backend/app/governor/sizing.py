"""Workload profiles and deterministic sizing.

Users describe outcomes ("Llama for 50 employees"); this module turns that into
vCPU, RAM, GPU count, GPU model and storage -- per candidate runtime, using the
*live* inventory to pick a GPU model that actually exists.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..models import (
    ENABLED_RUNTIMES,
    Environment,
    GlobalInventory,
    GPUAllocationType,
    GPUDevice,
    IsolationLevel,
    NetworkExposure,
    OptimizationGoal,
    Platform,
    ResourcePlan,
    RuntimeType,
    WorkloadIntent,
)

PROFILES_FILE = Path(__file__).with_name("profiles.yaml")


@dataclass
class WorkloadProfile:
    key: str
    display: str
    keywords: list[str] = field(default_factory=list)
    gpu_required: bool = False
    container_compatible: bool = True
    default_isolation: str = "medium"
    gpu_scaling: str = "scale-up"
    default_storage_backend: str = "ceph-rbd"
    base: dict[str, Any] = field(default_factory=dict)
    scaling: dict[str, Any] = field(default_factory=dict)
    questions: list[str] = field(default_factory=list)
    runtime_affinity: dict[str, float] = field(default_factory=dict)

    def affinity(self, runtime: RuntimeType) -> float:
        return float(self.runtime_affinity.get(str(runtime), 0.0))


class WorkloadProfiles:
    def __init__(self, data: dict[str, Any]) -> None:
        self.default_key = data.get("default_profile", "generic")
        self.profiles: dict[str, WorkloadProfile] = {
            key: WorkloadProfile(key=key, **value)
            for key, value in data.get("profiles", {}).items()
        }

    def get(self, workload_type: str | None) -> WorkloadProfile:
        if workload_type and workload_type in self.profiles:
            return self.profiles[workload_type]
        # Accept aliases such as "postgresql" -> "database".
        if workload_type:
            lowered = workload_type.lower()
            for profile in self.profiles.values():
                if lowered == profile.key or lowered in profile.keywords:
                    return profile
        return self.profiles[self.default_key]

    def match_keywords(self, text: str) -> WorkloadProfile | None:
        """Longest-keyword-wins matching, used by the deterministic extractor."""
        lowered = text.lower()
        best: tuple[int, WorkloadProfile] | None = None
        for profile in self.profiles.values():
            for keyword in profile.keywords:
                if keyword in lowered and (best is None or len(keyword) > best[0]):
                    best = (len(keyword), profile)
        return best[1] if best else None

    def all(self) -> list[WorkloadProfile]:
        return list(self.profiles.values())


@lru_cache
def load_profiles(path: str | None = None) -> WorkloadProfiles:
    file = Path(path) if path else PROFILES_FILE
    return WorkloadProfiles(yaml.safe_load(file.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------


class SizingCalculator:
    """Builds one `ResourcePlan` per candidate runtime."""

    def __init__(self, profiles: WorkloadProfiles) -> None:
        self.profiles = profiles

    # -- GPU selection -------------------------------------------------------

    def select_gpu(
        self,
        intent: WorkloadIntent,
        inventory: GlobalInventory,
        platform: Platform,
        required_memory_gb: int,
        count: int,
    ) -> GPUDevice | None:
        """Pick a GPU model that exists, fits and matches the optimisation goal."""
        available = inventory.available_gpus(platform)
        if not available:
            return None

        by_model: dict[str, list[GPUDevice]] = {}
        for device in available:
            by_model.setdefault(device.model, []).append(device)

        def enough(devices: list[GPUDevice]) -> bool:
            return len(devices) >= max(count, 1)

        fitting = [
            devices[0]
            for devices in by_model.values()
            if enough(devices) and devices[0].memory_gb >= required_memory_gb
        ]
        pool = fitting or [devices[0] for devices in by_model.values() if enough(devices)]
        if not pool:
            # Nothing has enough free cards; still return the best model so the
            # policy engine can explain *why* it is not viable.
            pool = [devices[0] for devices in by_model.values()]
        if not pool:
            return None

        if intent.preferred_gpu:
            preferred = next((d for d in pool if d.model == intent.preferred_gpu), None)
            if preferred is not None:
                return preferred

        goal = intent.optimization_goal
        if goal is OptimizationGoal.PERFORMANCE:
            return max(pool, key=lambda d: (d.perf_tier, len(by_model[d.model])))
        # cost and balanced both prefer the cheapest card that fits -- which also
        # preserves scarce high-end capacity for workloads that need it.
        return min(pool, key=lambda d: (d.relative_cost, -len(by_model[d.model])))

    # -- plans ---------------------------------------------------------------

    def candidates(
        self, intent: WorkloadIntent, inventory: GlobalInventory
    ) -> dict[RuntimeType, ResourcePlan]:
        return {
            runtime: self.plan_for(intent, inventory, runtime) for runtime in ENABLED_RUNTIMES
        }

    def plan_for(
        self, intent: WorkloadIntent, inventory: GlobalInventory, runtime: RuntimeType
    ) -> ResourcePlan:
        profile = self.profiles.get(intent.workload_type)
        base = profile.base
        scaling = profile.scaling

        storage_gb = int(intent.storage_gb or base.get("storage_gb", 20))
        gpu_total, gpu_memory_gb = self._gpu_requirements(intent, profile)

        if runtime.uses_gpu:
            replicas, gpu_per_replica = self._gpu_shape(profile, runtime, gpu_total, intent)
            vcpu = int(
                intent.estimated_vcpu
                or scaling.get("vcpu_per_gpu", base.get("vcpu", 8)) * gpu_per_replica
            )
            ram_gb = int(
                intent.estimated_ram_gb
                or scaling.get("ram_gb_per_gpu", base.get("ram_gb", 32)) * gpu_per_replica
            )
            device = self.select_gpu(
                intent, inventory, runtime.platform, gpu_memory_gb, gpu_per_replica
            )
            plan = ResourcePlan(
                runtime=runtime,
                replicas=replicas,
                vcpu_per_replica=vcpu,
                ram_gb_per_replica=ram_gb,
                gpu_per_replica=gpu_per_replica,
                gpu_model=device.model if device else None,
                gpu_memory_gb=gpu_memory_gb,
                gpu_allocation_type=(
                    GPUAllocationType.PASSTHROUGH
                    if runtime.platform is Platform.OPENSTACK
                    else GPUAllocationType.TIMESLICE
                ),
                storage_gb=storage_gb,
                storage_backend=profile.default_storage_backend,
                network_exposure=intent.network_exposure,
            )
            if device is not None and device.memory_gb < gpu_memory_gb:
                plan.notes.append(
                    f"{device.model} has {device.memory_gb} GB VRAM, "
                    f"{gpu_memory_gb} GB requested"
                )
        else:
            replicas, vcpu, ram_gb = self._cpu_shape(intent, profile, runtime)
            plan = ResourcePlan(
                runtime=runtime,
                replicas=replicas,
                vcpu_per_replica=vcpu,
                ram_gb_per_replica=ram_gb,
                gpu_per_replica=0,
                storage_gb=storage_gb,
                storage_backend=profile.default_storage_backend,
                network_exposure=intent.network_exposure,
            )

        self._decorate(plan, intent, inventory, runtime, profile)
        return plan

    # -- helpers -------------------------------------------------------------

    def _gpu_requirements(
        self, intent: WorkloadIntent, profile: WorkloadProfile
    ) -> tuple[int, int]:
        base = profile.base
        scaling = profile.scaling
        gpu_memory_gb = int(intent.gpu_memory_gb or base.get("gpu_memory_gb", 24))

        if intent.gpu_count:
            return max(int(intent.gpu_count), 1), gpu_memory_gb

        per_gpu = scaling.get("concurrent_users_per_gpu")
        if per_gpu and intent.concurrent_users:
            return max(1, math.ceil(intent.concurrent_users / int(per_gpu))), gpu_memory_gb
        if per_gpu and intent.expected_users:
            # Rule of thumb when only a headcount is known: ~20% are concurrent.
            concurrent = max(1, math.ceil(intent.expected_users * 0.2))
            return max(1, math.ceil(concurrent / int(per_gpu))), gpu_memory_gb
        return max(int(base.get("gpu_count", 1)), 1), gpu_memory_gb

    def _gpu_shape(
        self,
        profile: WorkloadProfile,
        runtime: RuntimeType,
        gpu_total: int,
        intent: WorkloadIntent,
    ) -> tuple[int, int]:
        """Decide scale-out (many 1-GPU replicas) versus scale-up (one big node)."""
        if profile.gpu_scaling == "scale-out" and runtime.platform is Platform.KUBERNETES:
            replicas = max(gpu_total, 1)
            per_replica = 1
        else:
            replicas = 1
            per_replica = max(gpu_total, 1)
        if intent.replicas:
            replicas = max(int(intent.replicas), 1)
        if intent.high_availability and replicas < 2:
            replicas = 2
        return replicas, per_replica

    def _cpu_shape(
        self, intent: WorkloadIntent, profile: WorkloadProfile, runtime: RuntimeType
    ) -> tuple[int, int, int]:
        base = profile.base
        scaling = profile.scaling
        vcpu = int(intent.estimated_vcpu or base.get("vcpu", 2))

        per_vcpu = scaling.get("concurrent_users_per_vcpu")
        if not intent.estimated_vcpu and per_vcpu and intent.concurrent_users:
            vcpu = max(vcpu, math.ceil(intent.concurrent_users / int(per_vcpu)) * 2)

        ram_gb = int(intent.estimated_ram_gb or 0)
        if not ram_gb:
            ram_per_vcpu = scaling.get("ram_gb_per_vcpu")
            ram_gb = int(ram_per_vcpu) * vcpu if ram_per_vcpu else int(base.get("ram_gb", 4))
            per_100gb = scaling.get("ram_gb_per_100gb_data")
            if per_100gb and intent.storage_gb:
                ram_gb = max(ram_gb, int(intent.storage_gb / 100 * float(per_100gb)))

        replicas = int(intent.replicas or 1)
        if intent.high_availability:
            if runtime is RuntimeType.DATABASE_SERVICE:
                replicas = max(replicas, 3)
            else:
                replicas = max(replicas, 2)
        return replicas, max(vcpu, 1), max(ram_gb, 1)

    def _decorate(
        self,
        plan: ResourcePlan,
        intent: WorkloadIntent,
        inventory: GlobalInventory,
        runtime: RuntimeType,
        profile: WorkloadProfile,
    ) -> None:
        """Fill in platform-specific fields: flavor, image, namespace, engine."""
        if runtime.platform is Platform.OPENSTACK:
            details = inventory.platform_of(Platform.OPENSTACK).details
            plan.flavor = self._match_flavor(plan, details.get("flavors", []))
            plan.image = self._match_image(intent, details.get("images", []), plan)
            plan.storage_backend = "ceph-rbd"
            plan.notes.append("Cinder volume backed by Ceph RBD")
            if plan.flavor is None and plan.gpu_per_replica:
                plan.notes.append("A dedicated GPU flavor will be created with a PCI alias")
        else:
            plan.namespace = f"ai-{intent.name or profile.key}"
            if plan.image is None and runtime is not RuntimeType.DATABASE_SERVICE:
                from ..config import get_settings

                plan.image = get_settings().default_workload_image
                plan.notes.append(
                    "Container image is a placeholder that runs; replace it with the "
                    "workload's real image"
                )
            if runtime is RuntimeType.DATABASE_SERVICE:
                plan.database_engine = "postgresql-16 (CloudNativePG operator)"
                plan.notes.append("Operator-managed failover, PITR and scheduled backups")

        if intent.workload_isolation is IsolationLevel.HIGH and runtime.is_container:
            plan.notes.append("Requires a dedicated node to approach VM-level isolation")
        if intent.environment is Environment.PROD and plan.replicas == 1 and runtime.is_container:
            plan.notes.append("Single replica: a rolling update briefly interrupts service")
        if plan.network_exposure is NetworkExposure.PUBLIC:
            plan.notes.append("Public exposure requires a load balancer and WAF review")

    @staticmethod
    def _match_flavor(plan: ResourcePlan, flavors: list[dict[str, Any]]) -> str | None:
        """Smallest published flavor that satisfies the plan, else None (custom)."""
        fitting = [
            flavor
            for flavor in flavors
            if int(flavor.get("vcpu", 0)) >= plan.vcpu_per_replica
            and int(flavor.get("ram_gb", 0)) >= plan.ram_gb_per_replica
            and int(flavor.get("gpu", 0)) >= plan.gpu_per_replica
        ]
        if not fitting:
            return None
        best = min(fitting, key=lambda f: (int(f["vcpu"]), int(f["ram_gb"])))
        return str(best["name"])

    @staticmethod
    def _match_image(
        intent: WorkloadIntent, images: list[dict[str, Any]], plan: ResourcePlan
    ) -> str | None:
        wanted = (intent.operating_system or "ubuntu").lower()
        gpu_ready = bool(plan.gpu_per_replica)
        scored: list[tuple[int, str]] = []
        for image in images:
            name = str(image.get("name", ""))
            score = 0
            haystack = f"{name} {image.get('distro', '')} {image.get('version', '')}".lower()
            for token in wanted.replace("-", " ").split():
                if token and token in haystack:
                    score += 2
            if gpu_ready and image.get("gpu_ready"):
                score += 3
            if not gpu_ready and image.get("gpu_ready"):
                score -= 1
            if score:
                scored.append((score, name))
        if not scored:
            return images[0]["name"] if images else None
        scored.sort(reverse=True)
        return scored[0][1]
