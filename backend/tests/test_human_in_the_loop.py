"""The mandatory human-in-the-loop guarantees (section 14).

If any test in this file ever needs "fixing" by relaxing an assertion, the
product has lost its central safety property.
"""

from __future__ import annotations

import pytest

from app.governor import HumanApprovalRequired, StateMachine, TransitionError
from app.models import Actor, DeploymentState, RuntimeType, WorkloadIntent


def test_the_ai_cannot_cross_either_approval_gate():
    with pytest.raises(HumanApprovalRequired):
        StateMachine.check(
            DeploymentState.WAITING_FOR_HUMAN, DeploymentState.APPROVED, Actor.ai()
        )
    with pytest.raises(HumanApprovalRequired):
        StateMachine.check(
            DeploymentState.WAITING_FOR_FINAL_APPROVAL,
            DeploymentState.DEPLOYING,
            Actor.ai(),
        )
    # Not even the system may do it.
    with pytest.raises(HumanApprovalRequired):
        StateMachine.check(
            DeploymentState.WAITING_FOR_FINAL_APPROVAL,
            DeploymentState.DEPLOYING,
            Actor.system(),
        )


def test_a_human_can_cross_the_gates():
    StateMachine.check(
        DeploymentState.WAITING_FOR_HUMAN, DeploymentState.APPROVED, Actor.human()
    )
    StateMachine.check(
        DeploymentState.WAITING_FOR_FINAL_APPROVAL,
        DeploymentState.DEPLOYING,
        Actor.human(),
    )


def test_there_is_no_shortcut_from_waiting_to_deploying():
    with pytest.raises(TransitionError):
        StateMachine.check(
            DeploymentState.WAITING_FOR_HUMAN, DeploymentState.DEPLOYING, Actor.human()
        )
    with pytest.raises(TransitionError):
        StateMachine.check(
            DeploymentState.RECOMMENDED, DeploymentState.RUNNING, Actor.human()
        )


def test_the_lifecycle_graph_marks_the_gates():
    gates = [edge for edge in StateMachine.graph() if edge["gate"]]
    assert len(gates) == 2
    assert all(edge["requires_human"] for edge in gates)


async def _recommend(container) -> tuple[str, str]:
    intent = WorkloadIntent(
        name="llama-internal",
        workload_type="llm-inference",
        gpu_required=True,
        concurrent_users=20,
    )
    recommendation = await container.engine.recommend(intent)
    await container.repositories.recommendations.save(recommendation)
    return recommendation.id, recommendation.top.option


async def test_service_refuses_ai_approval(container):
    recommendation_id, option = await _recommend(container)
    deployment = await container.deployments.create_from_recommendation(
        recommendation_id, option, Actor.human("admin")
    )
    assert deployment.state is DeploymentState.WAITING_FOR_HUMAN

    with pytest.raises(HumanApprovalRequired):
        await container.deployments.approve(deployment.id, Actor.ai())

    stored = await container.deployments.get(deployment.id)
    assert stored.state is DeploymentState.WAITING_FOR_HUMAN, "state must not have moved"


async def test_service_refuses_ai_execution(container):
    recommendation_id, option = await _recommend(container)
    deployment = await container.deployments.create_from_recommendation(
        recommendation_id, option, Actor.human("admin")
    )
    deployment = await container.deployments.approve(deployment.id, Actor.human("admin"))
    assert deployment.state is DeploymentState.WAITING_FOR_FINAL_APPROVAL

    with pytest.raises(HumanApprovalRequired):
        await container.deployments.apply(deployment.id, Actor.ai())

    stored = await container.deployments.get(deployment.id)
    assert stored.state is DeploymentState.WAITING_FOR_FINAL_APPROVAL


async def test_a_rejected_option_cannot_be_deployed(container):
    intent = WorkloadIntent(workload_type="web-api", gpu_required=False, concurrent_users=20)
    recommendation = await container.engine.recommend(intent)
    await container.repositories.recommendations.save(recommendation)
    blocked = next(
        option
        for option in recommendation.rejected_options
        if option.runtime is RuntimeType.KUBERNETES_GPU
    )
    with pytest.raises(ValueError, match="rejected by policy"):
        await container.deployments.create_from_recommendation(
            recommendation.id, blocked.option, Actor.human("admin")
        )


async def test_every_decision_is_audited(container):
    recommendation_id, option = await _recommend(container)
    deployment = await container.deployments.create_from_recommendation(
        recommendation_id, option, Actor.human("admin")
    )
    await container.deployments.approve(deployment.id, Actor.human("admin"))
    await container.deployments.apply(deployment.id, Actor.human("admin"))
    await container.deployments.wait_for_idle()

    events = await container.audit.for_deployment(deployment.id)
    actions = [event.action for event in events]
    for required in (
        "deployment_option_selected",
        "deployment_approved",
        "deployment_spec_generated",
        "deployment_plan_generated",
        "deployment_final_approved",
        "deployment_execution",
        "deployment_result",
    ):
        assert required in actions, f"missing audit action: {required}"

    approvals = [e for e in events if e.action == "deployment_final_approved"]
    assert approvals and approvals[0].actor_kind.value == "human"
