"""DeploymentSpec generation (section 15) and the mock openCenter workflow."""

from __future__ import annotations

import yaml

from app.governor import build_spec
from app.models import (
    Actor,
    DeploymentState,
    IsolationLevel,
    RuntimeType,
    Sensitivity,
    WorkloadIntent,
)


async def _option(container, intent: WorkloadIntent, runtime: RuntimeType):
    recommendation = await container.engine.recommend(intent)
    option = next(
        (o for o in recommendation.options if o.runtime is runtime),
        None,
    ) or next(o for o in recommendation.rejected_options if o.runtime is runtime)
    return recommendation, option


async def test_spec_matches_the_documented_contract(container):
    intent = WorkloadIntent(
        name="llama-internal",
        workload_type="llm-inference",
        gpu_required=True,
        concurrent_users=20,
        sensitive_data=Sensitivity.CONFIDENTIAL,
        high_availability=True,
    )
    _, option = await _option(container, intent, RuntimeType.KUBERNETES_GPU)
    spec = build_spec(intent, option, name="llama-internal")
    document = yaml.safe_load(spec.to_yaml())

    assert document["apiVersion"] == "aiinfra/v1alpha1"
    assert document["kind"] == "WorkloadDeployment"
    assert document["metadata"]["name"] == "llama-internal"
    assert document["intent"]["workload"] == "llm-inference"
    assert document["runtime"] == {"type": "kubernetes", "accelerator": "gpu", "mode": "container"}
    assert document["compute"]["memory"].endswith("Gi")
    assert document["gpu"]["vendor"] == "nvidia"
    assert document["gpu"]["countPerReplica"] >= 1
    assert document["storage"]["backend"] == "ceph-rbd"
    assert document["network"]["exposure"] == "internal"
    assert document["security"]["classification"] == "confidential"
    assert document["availability"]["highAvailability"] is True


async def test_openstack_gpu_spec_carries_a_pci_alias(container):
    intent = WorkloadIntent(
        name="pytorch-dev",
        workload_type="ai-dev-workstation",
        gpu_required=True,
        workload_isolation=IsolationLevel.HIGH,
    )
    _, option = await _option(container, intent, RuntimeType.OPENSTACK_GPU_VM)
    spec = build_spec(intent, option, name="pytorch-dev")
    assert spec.runtime.type == "openstack"
    assert spec.gpu is not None
    assert spec.gpu.allocation_type == "passthrough"
    assert spec.gpu.pci_alias.startswith("nvidia_")
    assert spec.compute.image, "an OpenStack VM needs an image"


async def test_restricted_data_forces_encryption_and_internal_networking(container):
    intent = WorkloadIntent(
        workload_type="llm-inference",
        gpu_required=True,
        concurrent_users=10,
        sensitive_data=Sensitivity.RESTRICTED,
    )
    recommendation = await container.engine.recommend(intent)
    option = recommendation.top
    from app.models import PolicyEvaluation

    policy = PolicyEvaluation(runtime=option.runtime, decisions=option.policy_decisions)
    spec = build_spec(intent, option, policy, name="secret-llm")
    assert spec.security.encryption_at_rest is True
    assert spec.network.exposure == "internal"
    assert spec.network.network_policy is True


async def test_mock_opencenter_generates_validates_and_plans(container):
    intent = WorkloadIntent(
        name="llama-internal",
        workload_type="llm-inference",
        gpu_required=True,
        concurrent_users=20,
    )
    recommendation = await container.engine.recommend(intent)
    spec = build_spec(intent, recommendation.top, name="llama-internal")

    clusters = await container.backend.get_clusters()
    assert clusters and any(c["type"] == "kubernetes" for c in clusters)

    artifacts = await container.backend.generate_deployment(spec)
    paths = [artifact.path for artifact in artifacts]
    assert any(path.endswith("deployment.yaml") for path in paths)
    assert any(path.endswith("networkpolicy.yaml") for path in paths)
    assert any(path.endswith("servicemonitor.yaml") for path in paths)
    assert any("flux" in path for path in paths)

    deployment_manifest = yaml.safe_load(
        next(a.content for a in artifacts if a.path.endswith("/deployment.yaml"))
    )
    limits = deployment_manifest["spec"]["template"]["spec"]["containers"][0]["resources"][
        "limits"
    ]
    assert limits["nvidia.com/gpu"] == 1

    issues = await container.backend.validate_deployment(spec)
    assert not [issue for issue in issues if issue.severity == "error"]

    plan = await container.backend.plan_deployment(spec, "dep-test")
    assert plan.valid
    rendered = plan.rendered_changes()
    assert any("namespace" in line for line in rendered)
    assert any("GPU allocation" in line for line in rendered)


async def test_openstack_plan_creates_a_gpu_flavor_with_the_alias(container):
    intent = WorkloadIntent(
        name="pytorch-dev",
        workload_type="ai-dev-workstation",
        gpu_required=True,
        workload_isolation=IsolationLevel.HIGH,
    )
    recommendation = await container.engine.recommend(intent)
    option = next(
        o for o in recommendation.options if o.runtime is RuntimeType.OPENSTACK_GPU_VM
    )
    spec = build_spec(intent, option, name="pytorch-dev")
    plan = await container.backend.plan_deployment(spec, "dep-tofu")

    main_tf = next(a for a in plan.artifacts if a.path.endswith("main.tf"))
    assert "pci_passthrough:alias" in main_tf.content
    assert "openstack_compute_instance_v2" in main_tf.content
    assert "nvidia_a10" in main_tf.content or "nvidia_h100" in main_tf.content
    assert any("Flavor" in line for line in plan.rendered_changes())


async def test_validation_rejects_a_plan_that_does_not_fit(container):
    intent = WorkloadIntent(
        workload_type="llm-inference", gpu_required=True, concurrent_users=20
    )
    recommendation = await container.engine.recommend(intent)
    spec = build_spec(intent, recommendation.top, name="too-big")
    spec.compute.replicas = 40
    issues = await container.backend.validate_deployment(spec)
    assert any(issue.severity == "error" for issue in issues)


async def test_rollback_returns_the_gpu_to_the_pool(container):
    intent = WorkloadIntent(
        name="llama-internal",
        workload_type="llm-inference",
        gpu_required=True,
        concurrent_users=20,
    )
    recommendation = await container.engine.recommend(intent)
    await container.repositories.recommendations.save(recommendation)
    before = (await container.inventory.get()).gpu.available

    deployment = await container.deployments.create_from_recommendation(
        recommendation.id, recommendation.top.option, Actor.human("admin")
    )
    await container.deployments.approve(deployment.id, Actor.human("admin"))
    await container.deployments.apply(deployment.id, Actor.human("admin"))
    await container.deployments.wait_for_idle()

    during = (await container.inventory.get(refresh=True)).gpu.available
    assert during == before - 1

    await container.deployments.rollback(deployment.id, Actor.human("admin"))
    await container.deployments.wait_for_idle()
    after = (await container.inventory.get(refresh=True)).gpu.available
    assert after == before

    deployment = await container.deployments.get(deployment.id)
    assert deployment.state is DeploymentState.ROLLED_BACK
