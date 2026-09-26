"""The deterministic policy rules from section 12."""

from __future__ import annotations

import pytest

from app.models import (
    Environment,
    IsolationLevel,
    NetworkExposure,
    PolicyEffect,
    RuntimeType,
    Sensitivity,
    WorkloadIntent,
)
from app.policies import InternalPolicyEngine

ENGINE = InternalPolicyEngine()


async def evaluate(container, intent: WorkloadIntent):
    inventory = await container.inventory.get()
    candidates = container.sizing.candidates(intent, inventory)
    return await ENGINE.evaluate(intent, inventory, candidates), candidates


async def test_rule1_restricted_data_stays_on_premises(container):
    intent = WorkloadIntent(
        workload_type="llm-inference", gpu_required=True, sensitive_data=Sensitivity.RESTRICTED
    )
    report, _ = await evaluate(container, intent)
    decision = next(d for d in report.global_decisions if d.rule_id == "POL-001")
    assert decision.effect is PolicyEffect.REQUIRE
    assert decision.obligations["public_cloud_allowed"] is False
    assert decision.obligations["encryption_at_rest"] is True


async def test_rule2_no_gpu_when_not_required(container):
    intent = WorkloadIntent(workload_type="web-api", gpu_required=False)
    report, _ = await evaluate(container, intent)
    for runtime in (RuntimeType.KUBERNETES_GPU, RuntimeType.OPENSTACK_GPU_VM):
        evaluation = report.for_runtime(runtime)
        assert not evaluation.allowed
        assert any(d.rule_id == "POL-002" for d in evaluation.denials)


async def test_rule3_development_prefers_the_cheapest_compatible_gpu(container):
    intent = WorkloadIntent(
        workload_type="ai-dev-workstation",
        gpu_required=True,
        environment=Environment.DEV,
        gpu_memory_gb=24,
    )
    report, candidates = await evaluate(container, intent)
    plan = candidates[RuntimeType.OPENSTACK_GPU_VM]
    # A10 (24 GB, cheapest) satisfies a 24 GB requirement; H100 must not be chosen.
    assert plan.gpu_model == "A10"
    decisions = report.for_runtime(RuntimeType.OPENSTACK_GPU_VM).decisions
    assert any(
        d.rule_id == "POL-003" and d.effect is PolicyEffect.PREFER for d in decisions
    )


async def test_rule4_high_isolation_prefers_a_vm(container):
    intent = WorkloadIntent(
        workload_type="llm-inference",
        gpu_required=True,
        concurrent_users=10,
        workload_isolation=IsolationLevel.HIGH,
    )
    report, _ = await evaluate(container, intent)
    vm = report.for_runtime(RuntimeType.OPENSTACK_GPU_VM)
    container_runtime = report.for_runtime(RuntimeType.KUBERNETES_GPU)
    assert vm.score_delta > 0
    assert container_runtime.score_delta < 0


async def test_rule5_container_friendly_inference_prefers_kubernetes(container):
    intent = WorkloadIntent(
        workload_type="llm-inference",
        gpu_required=True,
        concurrent_users=20,
        container_compatible=True,
        workload_isolation=IsolationLevel.MEDIUM,
    )
    report, _ = await evaluate(container, intent)
    decisions = report.for_runtime(RuntimeType.KUBERNETES_GPU).decisions
    assert any(d.rule_id == "POL-005" for d in decisions)


async def test_non_containerisable_workloads_cannot_use_kubernetes(container):
    intent = WorkloadIntent(workload_type="legacy-app", container_compatible=False)
    report, _ = await evaluate(container, intent)
    evaluation = report.for_runtime(RuntimeType.KUBERNETES_CPU)
    assert not evaluation.allowed
    assert any(d.rule_id == "POL-007" for d in evaluation.denials)


async def test_windows_cannot_run_on_the_container_platform(container):
    intent = WorkloadIntent(workload_type="generic", operating_system="windows server 2022")
    report, _ = await evaluate(container, intent)
    assert not report.for_runtime(RuntimeType.KUBERNETES_CPU).allowed


async def test_capacity_is_a_hard_gate(container):
    intent = WorkloadIntent(
        workload_type="ml-training", gpu_required=True, gpu_count=64, gpu_memory_gb=80
    )
    report, _ = await evaluate(container, intent)
    evaluation = report.for_runtime(RuntimeType.KUBERNETES_GPU)
    assert not evaluation.allowed
    assert any(d.rule_id == "POL-006" for d in evaluation.denials)


async def test_public_exposure_of_classified_data_is_denied(container):
    intent = WorkloadIntent(
        workload_type="web-api",
        sensitive_data=Sensitivity.RESTRICTED,
        network_exposure=NetworkExposure.PUBLIC,
    )
    report, _ = await evaluate(container, intent)
    assert any(d.rule_id == "POL-015" for d in report.global_decisions)
    assert not report.for_runtime(RuntimeType.KUBERNETES_CPU).allowed


async def test_database_workloads_must_use_the_managed_service(container):
    intent = WorkloadIntent(workload_type="database", storage_gb=500)
    report, _ = await evaluate(container, intent)
    assert report.for_runtime(RuntimeType.DATABASE_SERVICE).allowed
    assert not report.for_runtime(RuntimeType.KUBERNETES_CPU).allowed
    # An OpenStack VM stays available as the documented alternative.
    assert report.for_runtime(RuntimeType.OPENSTACK_CPU_VM).allowed


@pytest.mark.parametrize("runtime", [RuntimeType.VMWARE_VM, RuntimeType.BARE_METAL])
def test_future_runtimes_are_declared_but_disabled(runtime):
    from app.models import ENABLED_RUNTIMES

    assert runtime not in ENABLED_RUNTIMES
