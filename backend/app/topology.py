"""Architecture topology for the React Flow visualiser (section 25).

The graph is the platform's own architecture -- Copilot, Governor, human
approval, openCenter, the two platforms and the workload shapes below them --
annotated with what is real right now and, optionally, with the path a specific
deployment took.
"""

from __future__ import annotations

from typing import Any

from .models import Deployment, GlobalInventory, Platform, RuntimeType

#: The path each runtime takes through the architecture.
RUNTIME_PATHS: dict[RuntimeType, list[str]] = {
    RuntimeType.KUBERNETES_GPU: [
        "copilot",
        "governor",
        "approval",
        "opencenter",
        "gitops",
        "kubernetes",
        "kube-scheduler",
        "gpu-pod",
    ],
    RuntimeType.KUBERNETES_CPU: [
        "copilot",
        "governor",
        "approval",
        "opencenter",
        "gitops",
        "kubernetes",
        "kube-scheduler",
        "cpu-pod",
    ],
    RuntimeType.DATABASE_SERVICE: [
        "copilot",
        "governor",
        "approval",
        "opencenter",
        "gitops",
        "kubernetes",
        "kube-scheduler",
        "database",
    ],
    RuntimeType.OPENSTACK_CPU_VM: [
        "copilot",
        "governor",
        "approval",
        "opencenter",
        "gitops",
        "genestack",
        "openstack",
        "nova",
        "cpu-vm",
    ],
    RuntimeType.OPENSTACK_GPU_VM: [
        "copilot",
        "governor",
        "approval",
        "opencenter",
        "gitops",
        "genestack",
        "openstack",
        "nova",
        "vfio",
        "gpu-vm",
    ],
}

EDGES: list[tuple[str, str, str]] = [
    ("copilot", "governor", "intent"),
    ("governor", "approval", "ranked options"),
    ("approval", "opencenter", "approved DeploymentSpec"),
    ("opencenter", "gitops", "generated config"),
    ("gitops", "kubernetes", "Flux reconciliation"),
    ("gitops", "genestack", "OpenTofu / Helm"),
    ("kubernetes", "kube-scheduler", "schedule"),
    ("kube-scheduler", "gpu-pod", "nvidia.com/gpu"),
    ("kube-scheduler", "cpu-pod", "cpu / memory"),
    ("kube-scheduler", "database", "operator"),
    ("genestack", "openstack", "hosts control plane"),
    ("openstack", "nova", "boot request"),
    ("nova", "cpu-vm", "KVM"),
    ("nova", "vfio", "PCI passthrough"),
    ("vfio", "gpu-vm", "physical GPU"),
    ("ceph", "kubernetes", "RBD / CephFS"),
    ("ceph", "openstack", "Cinder"),
    ("gpu-pod", "monitoring", "DCGM"),
    ("cpu-pod", "monitoring", "metrics"),
    ("gpu-vm", "monitoring", "metrics"),
    ("cpu-vm", "monitoring", "metrics"),
    ("database", "monitoring", "metrics"),
    ("monitoring", "copilot", "post-deployment advice"),
]


def build_topology(
    inventory: GlobalInventory,
    capabilities: dict[str, Any],
    deployment: Deployment | None = None,
) -> dict[str, Any]:
    kubernetes = inventory.platform_of(Platform.KUBERNETES)
    openstack = inventory.platform_of(Platform.OPENSTACK)
    gpu_counts = inventory.gpu_model_counts()

    def gpu_label() -> str:
        if not gpu_counts:
            return "no GPUs"
        return ", ".join(
            f"{data['available']}/{data['total']} {model}"
            for model, data in sorted(gpu_counts.items())
        )

    nodes: list[dict[str, Any]] = [
        {
            "id": "copilot",
            "label": "AI Infra Copilot",
            "group": "ai",
            "detail": f"LLM: {capabilities.get('llm_provider', 'mock')}",
            "reality": capabilities.get("copilot", "real"),
        },
        {
            "id": "governor",
            "label": "AI Governor",
            "group": "ai",
            "detail": f"policy: {capabilities.get('policy_engine', 'internal')}",
            "reality": capabilities.get("governor", "real"),
        },
        {
            "id": "approval",
            "label": "Human approval",
            "group": "human",
            "detail": "two gates, mandatory",
            "reality": "real",
        },
        {
            "id": "opencenter",
            "label": "openCenter",
            "group": "control",
            "detail": capabilities.get("deployment_engine", "mock"),
            "reality": capabilities.get("opencenter", "mock"),
        },
        {
            "id": "gitops",
            "label": "GitOps / FluxCD",
            "group": "control",
            "detail": "OpenTofu, Kustomize, Kubespray",
            "reality": capabilities.get("flux", "simulated"),
        },
        {
            "id": "kubernetes",
            "label": "Kubernetes",
            "group": "platform",
            "detail": f"{len(kubernetes.nodes)} nodes, "
            f"{int(kubernetes.cpu.available)}/{int(kubernetes.cpu.total)} vCPU free",
            "reality": capabilities.get("kubernetes", "simulated"),
        },
        {
            "id": "genestack",
            "label": "Genestack",
            "group": "platform",
            "detail": "OpenStack on Kubernetes",
            "reality": capabilities.get("genestack", "mock"),
        },
        {
            "id": "openstack",
            "label": "OpenStack",
            "group": "platform",
            "detail": f"{len(openstack.nodes)} compute hosts",
            "reality": capabilities.get("openstack", "simulated"),
        },
        {
            "id": "kube-scheduler",
            "label": "kube-scheduler",
            "group": "scheduler",
            "detail": "decides the node",
            "reality": capabilities.get("kubernetes", "simulated"),
        },
        {
            "id": "nova",
            "label": "Nova + Placement",
            "group": "scheduler",
            "detail": "decides the compute host",
            "reality": capabilities.get("openstack", "simulated"),
        },
        {
            "id": "vfio",
            "label": "libvirt / VFIO",
            "group": "scheduler",
            "detail": "PCI passthrough",
            "reality": capabilities.get("openstack", "simulated"),
        },
        {
            "id": "gpu-pod",
            "label": "GPU workload",
            "group": "workload",
            "detail": gpu_label(),
            "reality": capabilities.get("kubernetes_gpu", "simulated"),
        },
        {
            "id": "cpu-pod",
            "label": "CPU workload",
            "group": "workload",
            "detail": "containers",
            "reality": capabilities.get("kubernetes", "simulated"),
        },
        {
            "id": "database",
            "label": "Database service",
            "group": "workload",
            "detail": "PostgreSQL operator",
            "reality": capabilities.get("kubernetes", "simulated"),
        },
        {
            "id": "cpu-vm",
            "label": "CPU VM",
            "group": "workload",
            "detail": "KVM guest",
            "reality": capabilities.get("openstack", "simulated"),
        },
        {
            "id": "gpu-vm",
            "label": "GPU VM",
            "group": "workload",
            "detail": "whole-card passthrough",
            "reality": capabilities.get("openstack", "simulated"),
        },
        {
            "id": "ceph",
            "label": "Ceph",
            "group": "storage",
            "detail": f"{inventory.storage.available_tb:.1f} TB free of "
            f"{inventory.storage.total_tb:.1f} TB",
            "reality": capabilities.get("ceph", "simulated"),
        },
        {
            "id": "monitoring",
            "label": "Prometheus / DCGM",
            "group": "observability",
            "detail": "metrics feed the Copilot",
            "reality": "real" if capabilities.get("mode") == "mvp" else "simulated",
        },
    ]

    highlight: list[str] = []
    if deployment is not None:
        highlight = RUNTIME_PATHS.get(deployment.runtime, [])
        for node in nodes:
            if node["id"] == "kube-scheduler" and deployment.placement:
                node["detail"] = f"placed on {deployment.placement.node}"
            if node["id"] == "nova" and deployment.placement:
                node["detail"] = f"placed on {deployment.placement.node}"

    highlight_set = set(highlight)
    for node in nodes:
        node["active"] = node["id"] in highlight_set

    edges = [
        {
            "id": f"{source}->{target}",
            "source": source,
            "target": target,
            "label": label,
            "active": _edge_active(source, target, highlight),
        }
        for source, target, label in EDGES
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "highlight": highlight,
        "deployment": (
            {
                "id": deployment.id,
                "name": deployment.name,
                "runtime": str(deployment.runtime),
                "state": str(deployment.state),
            }
            if deployment
            else None
        ),
    }


def _edge_active(source: str, target: str, path: list[str]) -> bool:
    for index in range(len(path) - 1):
        if path[index] == source and path[index + 1] == target:
            return True
    return False
