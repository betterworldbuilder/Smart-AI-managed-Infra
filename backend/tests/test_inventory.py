"""The simulated datacenter must match simulation/inventory.yaml exactly."""

from __future__ import annotations

from app.models import Platform


async def test_simulated_topology_matches_specification(container):
    inventory = await container.inventory.get()

    kubernetes = inventory.platform_of(Platform.KUBERNETES)
    openstack = inventory.platform_of(Platform.OPENSTACK)

    assert {node.name for node in kubernetes.nodes} == {
        "worker-01",
        "worker-02",
        "worker-03",
    }
    assert {node.name for node in openstack.nodes} == {
        "compute-01",
        "compute-gpu-01",
        "compute-gpu-02",
    }

    worker_03 = next(n for n in kubernetes.nodes if n.name == "worker-03")
    assert worker_03.cpu_total == 64
    assert worker_03.ram_gb_total == 256
    assert len(worker_03.gpu_ids) == 2

    counts = inventory.gpu_model_counts()
    assert counts["L40S"]["total"] == 2
    assert counts["H100"]["total"] == 4  # 2 in Kubernetes, 2 in OpenStack
    assert counts["A10"]["total"] == 2
    assert inventory.gpu.total == 8

    assert inventory.storage.total_tb == 40
    assert inventory.storage.available_tb == 25


async def test_gpu_devices_have_the_required_shape(container):
    inventory = await container.inventory.get()
    device = inventory.gpu.devices[0]
    payload = device.model_dump()
    for field in (
        "id",
        "vendor",
        "model",
        "memory_gb",
        "host",
        "pci_address",
        "status",
        "allocation_type",
    ):
        assert field in payload
    assert device.vendor == "NVIDIA"
    assert device.memory_gb > 0


async def test_openstack_placement_exposes_a_gpu_resource_class(container):
    providers = await container.openstack.get_resource_providers()
    gpu_hosts = [p for p in providers if any("GPU" in key for key in p["inventories"])]
    assert gpu_hosts, "expected at least one GPU resource provider"
    inventory = gpu_hosts[0]["inventories"]
    assert "VCPU" in inventory and "MEMORY_MB" in inventory


async def test_genestack_reports_the_openstack_control_plane(container):
    summary = await container.genestack.summary()
    assert summary["health"]["ready"] is True
    services = {s["name"] for s in summary["availability"]["services"]}
    for required in ("keystone", "nova", "placement", "neutron", "glance", "cinder"):
        assert required in services


async def test_inventory_is_cached_but_invalidatable(container):
    first = await container.inventory.get()
    second = await container.inventory.get()
    assert first.generated_at == second.generated_at
    container.inventory.invalidate()
    third = await container.inventory.get()
    assert third.generated_at != first.generated_at
