"""DeploymentSpec generation (section 15).

Turns an approved option into the vendor-neutral contract handed to openCenter.
Policy obligations (encryption, NetworkPolicy, anti-affinity, internal-only
networking) are applied here, so a spec cannot silently drop a control that the
policy engine demanded.
"""

from __future__ import annotations

import re
from uuid import uuid4

from ..models import (
    DeploymentSpec,
    PolicyEvaluation,
    RecommendationOption,
    RuntimeType,
    Sensitivity,
    SpecAvailability,
    SpecCompute,
    SpecGPU,
    SpecIntent,
    SpecMetadata,
    SpecNetwork,
    SpecObservability,
    SpecRuntime,
    SpecSecurity,
    SpecStorage,
    WorkloadIntent,
)

_SAFE = re.compile(r"[^a-z0-9-]+")


def workload_name(intent: WorkloadIntent) -> str:
    if intent.name:
        return intent.name
    base = _SAFE.sub("-", (intent.workload_type or "workload").lower()).strip("-")
    return f"{base or 'workload'}-{uuid4().hex[:5]}"


def pci_alias_for(model: str) -> str:
    """Nova PCI alias name. The *alias* is configuration; the product id is not."""
    return f"nvidia_{model.lower()}"


def build_spec(
    intent: WorkloadIntent,
    option: RecommendationOption,
    policy: PolicyEvaluation | None = None,
    name: str | None = None,
) -> DeploymentSpec:
    plan = option.resources
    runtime = option.runtime
    obligations = policy.obligations if policy else {}
    final_name = name or workload_name(intent)

    exposure = str(obligations.get("network_exposure", plan.network_exposure))
    requires_netpol = bool(obligations.get("network_policy", runtime.is_container))
    encryption = bool(
        obligations.get("encryption_at_rest", intent.sensitive_data is Sensitivity.RESTRICTED)
    )
    anti_affinity = bool(obligations.get("anti_affinity", plan.replicas > 1))
    min_replicas = int(obligations.get("min_replicas", 2 if intent.high_availability else 1))

    gpu_spec = None
    if plan.total_gpu and plan.gpu_model:
        gpu_spec = SpecGPU(
            vendor="nvidia",
            model=plan.gpu_model,
            count_per_replica=plan.gpu_per_replica,
            allocation_type=str(plan.gpu_allocation_type or "passthrough"),
            memory_gb=plan.gpu_memory_gb,
            pci_alias=(
                pci_alias_for(plan.gpu_model) if runtime.platform.value == "openstack" else None
            ),
        )

    annotations = {
        "aiinfra.io/recommendation-score": f"{option.score}",
        "aiinfra.io/native-scheduler": option.native_scheduler,
        "aiinfra.io/generated-by": "ai-governor",
    }
    if obligations.get("dedicated_node"):
        annotations["aiinfra.io/dedicated-node"] = "true"

    return DeploymentSpec(
        metadata=SpecMetadata(
            name=final_name,
            labels={
                "workload": intent.workload_type,
                "environment": str(intent.environment),
                "classification": str(intent.sensitive_data),
            },
            annotations=annotations,
        ),
        intent=SpecIntent(
            workload=intent.workload_type, environment=str(intent.environment)
        ),
        runtime=SpecRuntime(
            type=runtime.platform.value,
            accelerator="gpu" if plan.total_gpu else "none",
            mode=_mode_for(runtime),
        ),
        compute=SpecCompute(
            replicas=plan.replicas,
            cpu=plan.vcpu_per_replica,
            memory=f"{plan.ram_gb_per_replica}Gi",
            flavor=plan.flavor,
            image=plan.image,
        ),
        gpu=gpu_spec,
        storage=SpecStorage(
            type="persistent", backend=plan.storage_backend, size=f"{plan.storage_gb}Gi"
        ),
        network=SpecNetwork(
            exposure=exposure,
            network=None if runtime.is_container else _network_for(exposure),
            network_policy=requires_netpol,
        ),
        security=SpecSecurity(
            classification=str(intent.sensitive_data),
            encryption_at_rest=encryption,
            isolation=str(intent.workload_isolation),
        ),
        availability=SpecAvailability(
            high_availability=intent.high_availability,
            anti_affinity=anti_affinity,
            min_replicas=min_replicas,
        ),
        observability=SpecObservability(
            service_monitor=True,
            gpu_metrics=bool(plan.total_gpu),
            dashboards=(
                ["gpu-utilisation", "workload-overview"]
                if plan.total_gpu
                else ["workload-overview"]
            ),
        ),
    )


def _mode_for(runtime: RuntimeType) -> str:
    if runtime is RuntimeType.DATABASE_SERVICE:
        return "managed-database"
    return "vm" if runtime.is_vm else "container"


def _network_for(exposure: str) -> str:
    return "public-net" if exposure == "public" else "internal-net"
