"""Deterministic policy rules (section 12).

These are *not* suggestions the LLM can argue with. A DENY removes a candidate
runtime outright; PREFER/DISCOURAGE move its score by a fixed amount; REQUIRE
attaches an obligation that the DeploymentSpec generator must honour.

The rego mirror of these rules lives in `policies/` for deployments that prefer
OPA -- `POLICY_ENGINE=opa` uses that instead, with these as the fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..models import (
    ENABLED_RUNTIMES,
    Environment,
    GlobalInventory,
    IsolationLevel,
    NetworkExposure,
    PolicyDecision,
    PolicyEffect,
    ResourcePlan,
    RuntimeType,
    Sensitivity,
    WorkloadIntent,
)

AI_WORKLOADS = {"llm-inference", "ai-inference", "ml-inference", "ml-training", "ai-training"}
DATABASE_WORKLOADS = {"database", "postgresql", "postgres", "mysql", "mariadb"}


@dataclass
class PolicyContext:
    intent: WorkloadIntent
    inventory: GlobalInventory
    runtime: RuntimeType | None = None
    plan: ResourcePlan | None = None


GlobalRule = Callable[[PolicyContext], list[PolicyDecision]]
RuntimeRule = Callable[[PolicyContext], list[PolicyDecision]]

GLOBAL_RULES: list[GlobalRule] = []
RUNTIME_RULES: list[RuntimeRule] = []


def global_rule(func: GlobalRule) -> GlobalRule:
    GLOBAL_RULES.append(func)
    return func


def runtime_rule(func: RuntimeRule) -> RuntimeRule:
    RUNTIME_RULES.append(func)
    return func


# ---------------------------------------------------------------------------
# Global rules -- constraints on the request itself
# ---------------------------------------------------------------------------


@global_rule
def rule_restricted_data(ctx: PolicyContext) -> list[PolicyDecision]:
    """RULE 1 -- restricted data never leaves the private cloud."""
    if ctx.intent.sensitive_data is not Sensitivity.RESTRICTED:
        return []
    return [
        PolicyDecision(
            rule_id="POL-001",
            title="Restricted data stays on-premises",
            effect=PolicyEffect.REQUIRE,
            message="Data classification is 'restricted': public cloud is not allowed, "
            "egress is denied and storage must be encrypted at rest.",
            obligations={
                "public_cloud_allowed": False,
                "internet_access": False,
                "network_exposure": str(NetworkExposure.INTERNAL),
                "encryption_at_rest": True,
                "network_policy": True,
            },
        )
    ]


@global_rule
def rule_confidential_network(ctx: PolicyContext) -> list[PolicyDecision]:
    if ctx.intent.sensitive_data not in (Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED):
        return []
    return [
        PolicyDecision(
            rule_id="POL-011",
            title="Classified workloads are internal only",
            effect=PolicyEffect.REQUIRE,
            message=f"'{ctx.intent.sensitive_data}' data must not be exposed publicly and "
            "requires a NetworkPolicy.",
            obligations={
                "network_exposure": str(NetworkExposure.INTERNAL),
                "network_policy": True,
            },
        )
    ]


@global_rule
def rule_no_gpu_unless_required(ctx: PolicyContext) -> list[PolicyDecision]:
    """RULE 2 -- never allocate a GPU that was not asked for."""
    if ctx.intent.gpu_required:
        return []
    return [
        PolicyDecision(
            rule_id="POL-002",
            title="No GPU allocation",
            effect=PolicyEffect.INFO,
            message="The workload does not require a GPU, so GPU runtimes are excluded.",
            obligations={"gpu_allowed": False},
        )
    ]


@global_rule
def rule_public_exposure_conflict(ctx: PolicyContext) -> list[PolicyDecision]:
    if (
        ctx.intent.network_exposure is NetworkExposure.PUBLIC
        and ctx.intent.sensitive_data in (Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED)
    ):
        return [
            PolicyDecision(
                rule_id="POL-015",
                title="Public exposure denied for classified data",
                effect=PolicyEffect.DENY,
                message="Public network exposure conflicts with the data classification. "
                "Lower the classification or keep the workload internal.",
            )
        ]
    return []


@global_rule
def rule_prod_database_ha(ctx: PolicyContext) -> list[PolicyDecision]:
    if (
        ctx.intent.workload_type in DATABASE_WORKLOADS
        and ctx.intent.environment is Environment.PROD
        and not ctx.intent.high_availability
    ):
        return [
            PolicyDecision(
                rule_id="POL-010",
                title="Production databases should be highly available",
                effect=PolicyEffect.WARN,
                message="A production database without HA is a single point of failure. "
                "Consider enabling high availability.",
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Runtime rules -- constraints on each candidate
# ---------------------------------------------------------------------------


@runtime_rule
def rule_runtime_enabled(ctx: PolicyContext) -> list[PolicyDecision]:
    if ctx.runtime in ENABLED_RUNTIMES:
        return []
    return [
        PolicyDecision(
            rule_id="POL-019",
            title="Runtime not enabled",
            effect=PolicyEffect.DENY,
            runtime=ctx.runtime,
            message=f"{ctx.runtime} is declared but not enabled in this deployment.",
        )
    ]


@runtime_rule
def rule_gpu_only_when_required(ctx: PolicyContext) -> list[PolicyDecision]:
    """RULE 2, applied per candidate."""
    assert ctx.runtime is not None
    if ctx.intent.gpu_required or not ctx.runtime.uses_gpu:
        return []
    return [
        PolicyDecision(
            rule_id="POL-002",
            title="GPU not required",
            effect=PolicyEffect.DENY,
            runtime=ctx.runtime,
            message="The workload does not need a GPU; allocating one would waste scarce capacity.",
        )
    ]


@runtime_rule
def rule_gpu_required_but_absent(ctx: PolicyContext) -> list[PolicyDecision]:
    assert ctx.runtime is not None
    if not ctx.intent.gpu_required or ctx.runtime.uses_gpu:
        return []
    if ctx.runtime is RuntimeType.DATABASE_SERVICE:
        return []
    return [
        PolicyDecision(
            rule_id="POL-020",
            title="GPU required",
            effect=PolicyEffect.DENY,
            runtime=ctx.runtime,
            message="The workload requires a GPU and this runtime provides none.",
        )
    ]


@runtime_rule
def rule_dev_prefers_cheapest_gpu(ctx: PolicyContext) -> list[PolicyDecision]:
    """RULE 3 -- development GPU workloads take the cheapest compatible card."""
    assert ctx.runtime is not None
    if ctx.intent.environment is not Environment.DEV or not ctx.runtime.uses_gpu:
        return []
    if ctx.plan is None or ctx.plan.gpu_model is None:
        return []
    compatible = _compatible_gpu_models(ctx)
    if not compatible:
        return []
    cheapest = min(compatible, key=lambda d: d.relative_cost)
    if ctx.plan.gpu_model == cheapest.model:
        return [
            PolicyDecision(
                rule_id="POL-003",
                title="Development uses the lowest-cost compatible GPU",
                effect=PolicyEffect.PREFER,
                runtime=ctx.runtime,
                score_delta=3.0,
                message=f"{cheapest.model} is the cheapest GPU that satisfies the requirement.",
            )
        ]
    return [
        PolicyDecision(
            rule_id="POL-003",
            title="Development should use the lowest-cost compatible GPU",
            effect=PolicyEffect.DISCOURAGE,
            runtime=ctx.runtime,
            score_delta=-6.0,
            message=f"{ctx.plan.gpu_model} is more expensive than {cheapest.model} for a "
            "development workload.",
        )
    ]


@runtime_rule
def rule_isolation_prefers_vm(ctx: PolicyContext) -> list[PolicyDecision]:
    """RULE 4 -- high isolation prefers a VM over a container."""
    assert ctx.runtime is not None
    if ctx.intent.workload_isolation is not IsolationLevel.HIGH:
        return []
    if ctx.runtime.is_vm:
        return [
            PolicyDecision(
                rule_id="POL-004",
                title="High isolation prefers a virtual machine",
                effect=PolicyEffect.PREFER,
                runtime=ctx.runtime,
                score_delta=10.0,
                message="A dedicated VM provides kernel-level isolation and a private device.",
                obligations={"isolation": "high"},
            )
        ]
    return [
        PolicyDecision(
            rule_id="POL-004",
            title="High isolation discourages shared kernels",
            effect=PolicyEffect.DISCOURAGE,
            runtime=ctx.runtime,
            score_delta=-22.0,
            message="Containers share the host kernel; the request asked for strict isolation.",
            obligations={"dedicated_node": True},
        )
    ]


@runtime_rule
def rule_ai_inference_container(ctx: PolicyContext) -> list[PolicyDecision]:
    """RULE 5 -- container-friendly AI inference belongs on Kubernetes GPU."""
    assert ctx.runtime is not None
    if ctx.runtime is not RuntimeType.KUBERNETES_GPU:
        return []
    if ctx.intent.workload_type not in AI_WORKLOADS:
        return []
    if ctx.intent.container_compatible is False:
        return []
    if ctx.intent.workload_isolation is IsolationLevel.HIGH:
        return []
    return [
        PolicyDecision(
            rule_id="POL-005",
            title="Container-compatible AI workload",
            effect=PolicyEffect.PREFER,
            runtime=ctx.runtime,
            score_delta=2.0,
            message="AI inference is container compatible and no strict VM isolation was "
            "requested, so a Kubernetes GPU workload is appropriate.",
        )
    ]


@runtime_rule
def rule_container_incompatible(ctx: PolicyContext) -> list[PolicyDecision]:
    assert ctx.runtime is not None
    if ctx.intent.container_compatible is not False or not ctx.runtime.is_container:
        return []
    return [
        PolicyDecision(
            rule_id="POL-007",
            title="Workload cannot be containerised",
            effect=PolicyEffect.DENY,
            runtime=ctx.runtime,
            message="The workload needs a full operating system (legacy software, kernel "
            "modules or root-level access), so a container runtime is not viable.",
        )
    ]


@runtime_rule
def rule_windows_needs_vm(ctx: PolicyContext) -> list[PolicyDecision]:
    assert ctx.runtime is not None
    os_name = (ctx.intent.operating_system or "").lower()
    if "windows" not in os_name or not ctx.runtime.is_container:
        return []
    return [
        PolicyDecision(
            rule_id="POL-008",
            title="Windows guests require a virtual machine",
            effect=PolicyEffect.DENY,
            runtime=ctx.runtime,
            message="The Kubernetes foundation here runs Linux nodes only.",
        )
    ]


@runtime_rule
def rule_restricted_prefers_vm(ctx: PolicyContext) -> list[PolicyDecision]:
    assert ctx.runtime is not None
    if ctx.intent.sensitive_data is not Sensitivity.RESTRICTED:
        return []
    if ctx.runtime.is_vm:
        return [
            PolicyDecision(
                rule_id="POL-009",
                title="Restricted data prefers a dedicated VM",
                effect=PolicyEffect.PREFER,
                runtime=ctx.runtime,
                score_delta=6.0,
                message="Restricted data benefits from hardware-level tenant separation.",
            )
        ]
    return [
        PolicyDecision(
            rule_id="POL-009",
            title="Restricted data on a shared kernel needs compensating controls",
            effect=PolicyEffect.DISCOURAGE,
            runtime=ctx.runtime,
            score_delta=-8.0,
            message="A container is acceptable only with a dedicated node, NetworkPolicy and "
            "encryption at rest.",
            obligations={"dedicated_node": True, "encryption_at_rest": True},
        )
    ]


@runtime_rule
def rule_database_service(ctx: PolicyContext) -> list[PolicyDecision]:
    assert ctx.runtime is not None
    is_database = ctx.intent.workload_type in DATABASE_WORKLOADS
    if ctx.runtime is RuntimeType.DATABASE_SERVICE:
        if not is_database:
            return [
                PolicyDecision(
                    rule_id="POL-013",
                    title="Managed database service is for databases",
                    effect=PolicyEffect.DENY,
                    runtime=ctx.runtime,
                    message="This workload is not a database.",
                )
            ]
        return [
            PolicyDecision(
                rule_id="POL-012",
                title="Managed PostgreSQL operator",
                effect=PolicyEffect.PREFER,
                runtime=ctx.runtime,
                score_delta=8.0,
                message="An operator gives automated failover, backups and point-in-time "
                "recovery that a hand-built database VM does not.",
            )
        ]
    if is_database and ctx.runtime is RuntimeType.KUBERNETES_CPU:
        return [
            PolicyDecision(
                rule_id="POL-012",
                title="Unmanaged database on Kubernetes",
                effect=PolicyEffect.DENY,
                runtime=ctx.runtime,
                message="Databases must use the managed database service, not a plain "
                "Kubernetes workload.",
            )
        ]
    return []


@runtime_rule
def rule_ha_on_single_vm(ctx: PolicyContext) -> list[PolicyDecision]:
    assert ctx.runtime is not None
    if not ctx.intent.high_availability or not ctx.runtime.is_vm:
        return []
    return [
        PolicyDecision(
            rule_id="POL-014",
            title="HA on virtual machines is manual",
            effect=PolicyEffect.DISCOURAGE,
            runtime=ctx.runtime,
            score_delta=-6.0,
            message="VM-based HA needs anti-affinity plus an external load balancer; the "
            "container platform provides this natively.",
            obligations={"anti_affinity": True, "min_replicas": 2},
        )
    ]


@runtime_rule
def rule_capacity(ctx: PolicyContext) -> list[PolicyDecision]:
    """Hard capacity gate -- a plan that cannot fit is not a plan."""
    assert ctx.runtime is not None
    if ctx.plan is None:
        return []
    platform_inventory = ctx.inventory.platform_of(ctx.runtime.platform)
    if not platform_inventory.available:
        return [
            PolicyDecision(
                rule_id="POL-006",
                title="Platform unavailable",
                effect=PolicyEffect.DENY,
                runtime=ctx.runtime,
                message=f"The {ctx.runtime.platform} platform is not reachable.",
            )
        ]

    decisions: list[PolicyDecision] = []
    if ctx.plan.total_vcpu > platform_inventory.cpu.available:
        decisions.append(
            PolicyDecision(
                rule_id="POL-006",
                title="Insufficient CPU capacity",
                effect=PolicyEffect.DENY,
                runtime=ctx.runtime,
                message=f"{ctx.plan.total_vcpu} vCPU required, "
                f"{int(platform_inventory.cpu.available)} available.",
            )
        )
    if ctx.plan.total_ram_gb > platform_inventory.ram_gb.available:
        decisions.append(
            PolicyDecision(
                rule_id="POL-006",
                title="Insufficient memory capacity",
                effect=PolicyEffect.DENY,
                runtime=ctx.runtime,
                message=f"{ctx.plan.total_ram_gb} GB RAM required, "
                f"{int(platform_inventory.ram_gb.available)} GB available.",
            )
        )
    if ctx.plan.total_gpu:
        matching = [
            d
            for d in ctx.inventory.available_gpus(ctx.runtime.platform)
            if ctx.plan.gpu_model is None or d.model == ctx.plan.gpu_model
        ]
        if len(matching) < ctx.plan.total_gpu:
            decisions.append(
                PolicyDecision(
                    rule_id="POL-006",
                    title="Insufficient GPU capacity",
                    effect=PolicyEffect.DENY,
                    runtime=ctx.runtime,
                    message=f"{ctx.plan.total_gpu}x {ctx.plan.gpu_model or 'GPU'} required, "
                    f"{len(matching)} free on {ctx.runtime.platform}.",
                )
            )
    storage_tb = ctx.plan.storage_gb / 1024
    if storage_tb > ctx.inventory.storage.available_tb:
        decisions.append(
            PolicyDecision(
                rule_id="POL-006",
                title="Insufficient storage",
                effect=PolicyEffect.DENY,
                runtime=ctx.runtime,
                message=f"{ctx.plan.storage_gb} GB required, "
                f"{round(ctx.inventory.storage.available_tb * 1024)} GB free.",
            )
        )
    return decisions


@runtime_rule
def rule_gpu_memory(ctx: PolicyContext) -> list[PolicyDecision]:
    """A card with too little VRAM cannot hold the model -- that is a hard no."""
    assert ctx.runtime is not None
    if ctx.plan is None or not ctx.plan.total_gpu:
        return []
    required = ctx.plan.gpu_memory_gb or 0
    if not required or ctx.plan.gpu_model is None:
        return []
    available = [
        d
        for d in ctx.inventory.available_gpus(ctx.runtime.platform)
        if d.model == ctx.plan.gpu_model
    ]
    if not available:
        return []
    if available[0].memory_gb >= required:
        return []
    return [
        PolicyDecision(
            rule_id="POL-021",
            title="GPU memory too small",
            effect=PolicyEffect.DENY,
            runtime=ctx.runtime,
            message=f"{ctx.plan.gpu_model} has {available[0].memory_gb} GB VRAM; the workload "
            f"needs at least {required} GB. No larger card is free on "
            f"{ctx.runtime.platform}.",
        )
    ]


@runtime_rule
def rule_latency_sensitive(ctx: PolicyContext) -> list[PolicyDecision]:
    assert ctx.runtime is not None
    if not ctx.intent.latency_sensitive or not ctx.runtime.is_container:
        return []
    return [
        PolicyDecision(
            rule_id="POL-018",
            title="Latency-sensitive workloads scale faster in containers",
            effect=PolicyEffect.PREFER,
            runtime=ctx.runtime,
            score_delta=2.0,
            message="Container start-up is seconds versus minutes for a VM.",
        )
    ]


def _compatible_gpu_models(ctx: PolicyContext):
    """GPUs on this platform that satisfy the memory requirement."""
    assert ctx.runtime is not None
    required_memory = (ctx.plan.gpu_memory_gb if ctx.plan else None) or 0
    seen: dict[str, object] = {}
    for device in ctx.inventory.available_gpus(ctx.runtime.platform):
        if device.memory_gb >= required_memory:
            seen.setdefault(device.model, device)
    return list(seen.values())
