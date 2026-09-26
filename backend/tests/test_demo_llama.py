"""The full demo, end to end, through the HTTP API.

    create request
      -> classify as LLM inference
      -> GPU required
      -> recommend Kubernetes GPU
      -> human approval
      -> DeploymentSpec
      -> mock openCenter
      -> GitOps / Flux
      -> workload RUNNING
      -> metrics
      -> post-deployment recommendation
"""

from __future__ import annotations

import asyncio


async def _wait_until_running(client, deployment_id: str, timeout: float = 30.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    last: dict = {}
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/deployments/{deployment_id}")
        last = response.json()
        if last["state"] in ("RUNNING", "FAILED"):
            return last
        await asyncio.sleep(0.1)
    return last


async def test_llama_demo_end_to_end(client):
    # 1. The operator describes the need in plain language.
    response = await client.post(
        "/api/copilot/messages",
        json={"message": "Deploy a private Llama inference service for 100 employees."},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    conversation_id = body["conversation"]["id"]

    # 2. The Copilot asks what it needs, and nothing more.
    assert body["recommendation"] is None
    questions = body["conversation"]["pending_questions"]
    assert questions and len(questions) <= 2
    assert body["conversation"]["intent"]["workload_type"] == "llm-inference"
    assert body["conversation"]["intent"]["gpu_required"] is True

    # 3. The operator answers.
    response = await client.post(
        f"/api/copilot/conversations/{conversation_id}/answers",
        json={"answers": {"concurrent_users": 25, "environment": "prod"}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    recommendation = body["recommendation"]
    assert recommendation is not None

    # 4. The Governor recommends Kubernetes GPU and keeps the alternatives.
    top = recommendation["options"][0]
    assert top["runtime"] == "KUBERNETES_GPU"
    assert top["resources"]["gpu_per_replica"] >= 1
    assert top["reason"]

    # 25 concurrent users need 2 GPUs, and only one card with enough VRAM is
    # free on OpenStack -- so the VM option is not silently dropped, it is
    # listed as rejected with the capacity reason.
    considered = {
        option["runtime"]: option
        for option in recommendation["options"] + recommendation["rejected_options"]
    }
    assert "OPENSTACK_GPU_VM" in considered
    vm_option = considered["OPENSTACK_GPU_VM"]
    assert vm_option in recommendation["options"] or vm_option["blocked_by"]
    for rejected in recommendation["rejected_options"]:
        assert rejected["blocked_by"], f"{rejected['option']} rejected without a reason"

    # 5. The human selects an option -- still nothing deployed.
    response = await client.post(
        "/api/deployments",
        json={"recommendation_id": recommendation["id"], "option": top["option"]},
    )
    assert response.status_code == 201, response.text
    deployment = response.json()
    assert deployment["state"] == "WAITING_FOR_HUMAN"
    deployment_id = deployment["id"]

    # 6. Gate 1: approval produces the spec and the openCenter plan.
    response = await client.post(f"/api/deployments/{deployment_id}/approve", json={})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "WAITING_FOR_FINAL_APPROVAL"

    spec = (await client.get(f"/api/deployments/{deployment_id}/spec")).json()
    assert spec["spec"]["apiVersion"] == "aiinfra/v1alpha1"
    assert spec["spec"]["gpu"]["vendor"] == "nvidia"

    plan = (await client.get(f"/api/deployments/{deployment_id}/plan")).json()
    assert plan["plan"]["valid"] is True
    assert any("namespace" in change for change in plan["changes"])
    assert any("GPU allocation" in change for change in plan["changes"])

    artifacts = (await client.get(f"/api/deployments/{deployment_id}/artifacts")).json()
    assert any(a["path"].endswith("deployment.yaml") for a in artifacts)

    # 7. Gate 2: the human authorises execution.
    response = await client.post(f"/api/deployments/{deployment_id}/apply", json={})
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "DEPLOYING"

    # 8. openCenter -> GitOps -> Flux -> Kubernetes -> kube-scheduler.
    final = await _wait_until_running(client, deployment_id)
    assert final["state"] == "RUNNING", final.get("failure_reason")
    assert final["placement"]["scheduler"] == "kube-scheduler"
    assert final["placement"]["node"].startswith("worker-")
    assert final["placement"]["gpu_ids"]

    steps = [event["message"] for event in final["events"]]
    assert any("GitOps" in step for step in steps)
    assert any("kube-scheduler" in step for step in steps)

    gitops = (await client.get("/api/gitops")).json()
    assert any("deployment.yaml" in path for path in gitops["files"])

    # 9. Monitoring reflects the deployment.
    metrics = (await client.get("/api/metrics/summary")).json()
    assert metrics["gpu_allocated"] >= 1
    assert metrics["running_deployments"] >= 1

    workload = (await client.get(f"/api/deployments/{deployment_id}/metrics")).json()
    assert workload["ram_total_gb"] > 0

    # 10. The Copilot keeps advising -- without touching anything.
    advice = (await client.post("/api/advisor/analyse")).json()
    assert isinstance(advice, list)
    if advice:
        simulated = (await client.post(f"/api/advisor/{advice[0]['id']}/simulate")).json()
        assert simulated["status"] == "simulated"
        assert "Simulation only" in simulated["simulation"]["note"]
        unchanged = (await client.get(f"/api/deployments/{deployment_id}")).json()
        assert unchanged["state"] == "RUNNING"

    # 11. The audit trail proves a human authorised it.
    audit = (await client.get(f"/api/deployments/{deployment_id}/audit")).json()
    approvals = [e for e in audit if e["action"] == "deployment_final_approved"]
    assert approvals and approvals[0]["actor_kind"] == "human"
