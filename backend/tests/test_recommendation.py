"""Deterministic recommendation behaviour (section 13)."""

from __future__ import annotations

from app.models import (
    Environment,
    IsolationLevel,
    OptimizationGoal,
    RuntimeType,
    Sensitivity,
    WorkloadIntent,
)


async def test_llama_inference_prefers_kubernetes_gpu_with_the_cheaper_card(container):
    intent = WorkloadIntent(
        name="llama-internal",
        workload_type="llm-inference",
        gpu_required=True,
        expected_users=50,
        concurrent_users=20,
        environment=Environment.PROD,
        sensitive_data=Sensitivity.CONFIDENTIAL,
        container_compatible=True,
    )
    recommendation = await container.engine.recommend(intent)
    top = recommendation.top

    assert top.runtime is RuntimeType.KUBERNETES_GPU
    assert top.resources.total_gpu == 1
    assert top.resources.gpu_model == "L40S"  # preserves the scarce H100s
    assert top.resources.ram_gb_per_replica == 64
    assert top.resources.storage_gb == 500
    assert top.recommended is True

    # Every viable option is retained, not just the winner.
    assert len(recommendation.options) >= 2
    alternative = recommendation.options[1]
    assert alternative.runtime is RuntimeType.OPENSTACK_GPU_VM
    assert alternative.resources.gpu_model == "H100"  # only card with >= 48 GB free
    assert top.score > alternative.score

    # Reasons are generated from the scoring, not from prose.
    assert any("L40S" in reason for reason in top.reason)
    assert any("isolation" in reason.lower() for reason in alternative.reason)


async def test_scores_are_explainable(container):
    intent = WorkloadIntent(
        workload_type="llm-inference", gpu_required=True, concurrent_users=20
    )
    recommendation = await container.engine.recommend(intent)
    top = recommendation.top
    breakdown = top.breakdown
    assert breakdown.compatibility > 0
    assert breakdown.capacity > 0
    assert abs(breakdown.total - top.score) < 0.5 or top.score in (0.0, 100.0)
    assert top.capacity_impact.fits


async def test_rejected_options_explain_themselves(container):
    intent = WorkloadIntent(workload_type="web-api", gpu_required=False, concurrent_users=30)
    recommendation = await container.engine.recommend(intent)
    assert recommendation.rejected_options
    for option in recommendation.rejected_options:
        assert option.blocked_by, f"{option.option} was rejected without a reason"


async def test_high_isolation_moves_the_workload_onto_a_vm(container):
    intent = WorkloadIntent(
        workload_type="llm-inference",
        gpu_required=True,
        concurrent_users=10,
        workload_isolation=IsolationLevel.HIGH,
    )
    recommendation = await container.engine.recommend(intent)
    assert recommendation.top.runtime is RuntimeType.OPENSTACK_GPU_VM


async def test_performance_goal_selects_the_faster_card(container):
    base = dict(
        workload_type="llm-inference",
        gpu_required=True,
        concurrent_users=20,
        gpu_memory_gb=24,
    )
    cheap = await container.engine.recommend(
        WorkloadIntent(**base, optimization_goal=OptimizationGoal.COST)
    )
    fast = await container.engine.recommend(
        WorkloadIntent(**base, optimization_goal=OptimizationGoal.PERFORMANCE)
    )
    cheap_tier = cheap.top.resources.gpu_model
    fast_tier = fast.top.resources.gpu_model
    assert cheap_tier != fast_tier or cheap.top.score != fast.top.score


async def test_sizing_scales_with_concurrency(container):
    small = await container.engine.recommend(
        WorkloadIntent(workload_type="llm-inference", gpu_required=True, concurrent_users=10)
    )
    large = await container.engine.recommend(
        WorkloadIntent(workload_type="llm-inference", gpu_required=True, concurrent_users=40)
    )
    assert large.top.resources.total_gpu > small.top.resources.total_gpu


async def test_database_recommendation_shows_the_vm_alternative(container):
    intent = WorkloadIntent(
        workload_type="database",
        storage_gb=500,
        high_availability=True,
        environment=Environment.PROD,
    )
    recommendation = await container.engine.recommend(intent)
    assert recommendation.top.runtime is RuntimeType.DATABASE_SERVICE
    assert recommendation.top.resources.replicas == 3
    runtimes = {option.runtime for option in recommendation.options}
    assert RuntimeType.OPENSTACK_CPU_VM in runtimes, "the VM alternative must stay visible"


async def test_no_viable_option_is_explained_not_invented(container):
    intent = WorkloadIntent(
        workload_type="ml-training", gpu_required=True, gpu_count=32, gpu_memory_gb=80
    )
    recommendation = await container.engine.recommend(intent)
    assert recommendation.options == []
    assert "No runtime satisfies" in recommendation.summary
    assert recommendation.rejected_options
