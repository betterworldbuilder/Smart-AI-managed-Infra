"""The five demo scenarios from section 27, plus the section 32 acceptance test."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("key", ["A", "B", "C", "D", "E"])
async def test_scenario_reaches_the_expected_runtime(container, key):
    result = await container.scenarios.run(key)
    assert result.recommendation is not None, f"scenario {key} produced no recommendation"
    assert result.matched_expectation is True, (
        f"scenario {key} ({result.scenario.title}) recommended "
        f"{result.recommended_runtime}, expected {result.scenario.expected_runtime}"
    )


@pytest.mark.parametrize("key", ["A", "B", "C", "D", "E"])
async def test_scenario_explains_itself(container, key):
    result = await container.scenarios.run(key)
    top = result.recommendation.top
    assert top.reason, "a recommendation without a reason is not acceptable"
    haystack = " ".join(top.reason + [top.title]).lower()
    for needle in result.scenario.expected_reason_contains:
        assert needle.lower() in haystack, f"scenario {key}: '{needle}' missing from the reasons"


async def test_scenarios_never_deploy_anything(container):
    await container.scenarios.run("D")
    assert await container.deployments.list() == []


async def test_poc_acceptance_scenario(container):
    """Section 32, end to end: two options, Kubernetes GPU recommended."""
    conversation, recommendation = await container.copilot.handle_message(
        None,
        "I need a private AI server for 50 employees running Llama with "
        "confidential company data.",
    )
    assert recommendation is None, "the Copilot must ask before it recommends"
    assert any(q.field == "concurrent_users" for q in conversation.pending_questions)

    conversation, recommendation = await container.copilot.answer(
        conversation.id, {"concurrent_users": 20, "environment": "prod"}
    )
    assert recommendation is not None

    option_a, option_b = recommendation.options[0], recommendation.options[1]
    assert option_a.runtime.value == "KUBERNETES_GPU"
    assert option_a.resources.total_gpu == 1
    assert option_a.resources.gpu_model == "L40S"
    assert option_a.resources.ram_gb_per_replica == 64
    assert option_a.resources.storage_gb == 500

    assert option_b.runtime.value == "OPENSTACK_GPU_VM"
    assert option_b.resources.gpu_model == "H100"
    assert "isolation" in " ".join(option_b.reason).lower()
