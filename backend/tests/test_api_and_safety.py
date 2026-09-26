"""API surface, authentication and the LLM safety boundary (section 28)."""

from __future__ import annotations

import pytest

from app.inventory import SanitizationError, assert_clean, sanitize_inventory


async def test_health_and_capabilities_are_unauthenticated(client):
    # `client` carries a token, but these must work without one too.
    del client.headers["Authorization"]
    assert (await client.get("/api/health")).status_code == 200
    capabilities = (await client.get("/api/system/capabilities")).json()
    assert capabilities["mode"] == "simulation"
    assert capabilities["mode_label"] == "POC - SIMULATION"
    assert capabilities["kubernetes"] == "simulated"
    assert capabilities["opencenter"] == "mock"
    assert capabilities["copilot"] == "real"


async def test_protected_endpoints_require_a_token(client):
    del client.headers["Authorization"]
    response = await client.get("/api/inventory/global")
    assert response.status_code == 401


async def test_bad_credentials_are_rejected(client):
    response = await client.post(
        "/api/auth/login", json={"username": "admin", "password": "wrong"}
    )
    assert response.status_code == 401


async def test_reality_matrix_is_honest(client):
    matrix = (await client.get("/api/system/reality-matrix")).json()
    by_component = {row["component"]: row for row in matrix}
    assert by_component["AI Copilot"]["current"] == "real"
    assert by_component["Kubernetes"]["current"] == "simulated"
    assert by_component["Nova GPU VM"]["current"] == "simulated"


async def test_integrations_page_data(client):
    integrations = (await client.get("/api/settings/integrations")).json()
    keys = {row["key"] for row in integrations}
    assert {"llm", "opencenter", "kubernetes", "openstack", "ceph", "policy"} <= keys
    for row in integrations:
        assert row["status"] in ("CONNECTED", "SIMULATED", "DISCONNECTED", "ERROR")


async def test_topology_endpoint_highlights_nothing_without_a_deployment(client):
    topology = (await client.get("/api/topology")).json()
    ids = {node["id"] for node in topology["nodes"]}
    assert {"copilot", "governor", "approval", "opencenter", "kubernetes"} <= ids
    assert topology["highlight"] == []


async def test_gpu_inventory_endpoint(client):
    payload = (await client.get("/api/inventory/gpus")).json()
    assert payload["total"] == 8
    assert {"L40S", "H100", "A10"} == set(payload["by_model"])
    device = payload["devices"][0]
    assert {"id", "model", "host", "memory_gb", "status", "allocation_type"} <= set(device)


async def test_scenarios_endpoint_lists_all_demos(client):
    scenarios = (await client.get("/api/scenarios")).json()
    keys = {scenario["key"] for scenario in scenarios}
    assert {"A", "B", "C", "D", "E"} <= keys


async def test_lifecycle_endpoint_exposes_the_gates(client):
    graph = (await client.get("/api/system/lifecycle")).json()
    gates = [edge for edge in graph if edge["gate"]]
    assert len(gates) == 2


# --- section 28: what the model is allowed to see --------------------------


async def test_sanitised_inventory_contains_only_capacity_facts(container):
    inventory = await container.inventory.get()
    payload = sanitize_inventory(inventory)
    text = str(payload).lower()
    for forbidden in ("password", "token", "kubeconfig", "secret", "auth_url", "ssh"):
        assert forbidden not in text
    assert payload["gpu"]["models"][0]["gpu_type"]
    assert "available" in payload["gpu"]["models"][0]


def test_assert_clean_blocks_credentials():
    with pytest.raises(SanitizationError):
        assert_clean({"kubeconfig": "apiVersion: v1"})
    with pytest.raises(SanitizationError):
        assert_clean({"cloud": {"os_password": "hunter2"}})
    with pytest.raises(SanitizationError):
        assert_clean({"note": "-----BEGIN RSA PRIVATE KEY-----"})
    assert_clean({"gpu_type": "L40S", "available": 2})


async def test_prometheus_metrics_endpoint(client):
    response = await client.get("/api/metrics")
    assert response.status_code == 200
    assert "aiinfra_gpu_total" in response.text


async def test_remediation_flow(client):
    alert = (await client.post("/api/remediation/simulate-gpu-failure")).json()
    assert alert["alertname"] == "GPUWorkerUnhealthy"
    assert alert["recommended_option"] == "A"
    assert len(alert["options"]) == 3

    decided = (
        await client.post(
            f"/api/remediation/alerts/{alert['id']}/decide", json={"option": "A"}
        )
    ).json()
    assert decided["status"] == "approved"
    assert decided["decision"] == "A"

    audit = (await client.get("/api/audit")).json()
    assert any(event["action"] == "remediation_decision" for event in audit)
