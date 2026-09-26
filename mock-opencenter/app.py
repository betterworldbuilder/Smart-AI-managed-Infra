"""A standalone mock openCenter API.

The POC's default (`OPEN_CENTER_MODE=mock`) runs openCenter in-process. This
service exists so you can exercise the *real* integration path -- the HTTP
client, the run polling, the plan/apply contract -- without a real openCenter
installation:

    OPEN_CENTER_MODE=real
    OPEN_CENTER_URL=http://mock-opencenter:8080

It implements exactly the endpoints `RealOpenCenterAdapter` calls, and nothing
else. It is deliberately a *separate* codebase from the platform so the
contract stays honest.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

VERSION = os.getenv("MOCK_OPENCENTER_VERSION", "1.4.0-poc")
STEP_SECONDS = float(os.getenv("MOCK_OPENCENTER_STEP_SECONDS", "1.5"))

app = FastAPI(
    title="Mock openCenter",
    version=VERSION,
    description="Simulated deployment/orchestration API for the GPU Native Infra POC.",
)

CLUSTERS: list[dict[str, Any]] = [
    {
        "id": "oc-k8s-core",
        "name": "k8s-core",
        "type": "kubernetes",
        "gitops": "fluxcd",
        "repo": os.getenv("GITOPS_REPO", "git@git.lab.internal:infra/gitops.git"),
        "branch": "main",
        "path": "clusters/k8s-core",
    },
    {
        "id": "oc-genestack",
        "name": "genestack-openstack",
        "type": "openstack",
        "gitops": "fluxcd",
        "provisioner": "opentofu",
        "repo": os.getenv("GITOPS_REPO", "git@git.lab.internal:infra/gitops.git"),
        "branch": "main",
        "path": "clusters/genestack",
    },
]

RUNS: dict[str, dict[str, Any]] = {}
DEPLOYMENTS: dict[str, dict[str, Any]] = {}


class ApplyRequest(BaseModel):
    spec: dict[str, Any]
    plan_id: str | None = None
    approved_by: str = Field(default="unknown")


class PlanRequest(BaseModel):
    spec: dict[str, Any]
    cluster: str | None = None


@app.get("/api/v1/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "version": VERSION,
        "mode": "mock",
        "clusters": len(CLUSTERS),
        "runs": len(RUNS),
    }


@app.get("/api/v1/clusters")
async def clusters() -> dict[str, Any]:
    return {"items": CLUSTERS}


@app.get("/api/v1/clusters/{cluster_id}/resources")
async def cluster_resources(cluster_id: str) -> dict[str, Any]:
    cluster = next((c for c in CLUSTERS if c["id"] == cluster_id), None)
    if cluster is None:
        raise HTTPException(status_code=404, detail="unknown cluster")
    return {
        "cluster": cluster,
        "note": "Mock openCenter does not own inventory; the platform reads it from the "
        "Kubernetes and OpenStack adapters.",
    }


def _summarise(spec: dict[str, Any]) -> dict[str, Any]:
    compute = spec.get("compute", {})
    gpu = spec.get("gpu") or {}
    return {
        "name": spec.get("metadata", {}).get("name", "workload"),
        "replicas": int(compute.get("replicas", 1)),
        "cpu": compute.get("cpu"),
        "memory": compute.get("memory"),
        "gpu": gpu.get("countPerReplica"),
        "gpu_model": gpu.get("model"),
        "runtime": spec.get("runtime", {}),
    }


@app.post("/api/v1/deployments/validate")
async def validate(payload: PlanRequest | ApplyRequest) -> dict[str, Any]:
    spec = payload.spec
    issues: list[dict[str, str]] = []
    name = spec.get("metadata", {}).get("name", "")
    if not name:
        issues.append({"severity": "error", "code": "INVALID_NAME", "message": "name is required"})
    if spec.get("apiVersion") != "aiinfra/v1alpha1":
        issues.append(
            {
                "severity": "error",
                "code": "UNSUPPORTED_API",
                "message": f"unsupported apiVersion {spec.get('apiVersion')}",
            }
        )
    availability = spec.get("availability", {})
    compute = spec.get("compute", {})
    if availability.get("highAvailability") and int(compute.get("replicas", 1)) < 2:
        issues.append(
            {
                "severity": "error",
                "code": "HA_REPLICAS",
                "message": "highAvailability requires at least 2 replicas",
            }
        )
    issues.append(
        {
            "severity": "info",
            "code": "ACCEPTED",
            "message": f"mock openCenter {VERSION} accepted the spec",
        }
    )
    return {"issues": issues}


@app.post("/api/v1/deployments/plan")
async def plan(payload: PlanRequest) -> dict[str, Any]:
    spec = payload.spec
    summary = _summarise(spec)
    cluster = next(
        (c for c in CLUSTERS if c["id"] == payload.cluster),
        CLUSTERS[0] if spec.get("runtime", {}).get("type") == "kubernetes" else CLUSTERS[1],
    )
    namespace = f"ai-{summary['name']}"
    changes = [
        {"action": "create", "kind": "namespace", "name": namespace, "detail": None},
        {
            "action": "create",
            "kind": "workloads",
            "name": f"{summary['replicas']} x {summary['name']}",
            "detail": f"{summary['cpu']} vCPU / {summary['memory']} each",
        },
    ]
    if summary["gpu"]:
        changes.append(
            {
                "action": "create",
                "kind": "GPU allocations",
                "name": f"{summary['gpu'] * summary['replicas']} x {summary['gpu_model']}",
                "detail": "nvidia.com/gpu",
            }
        )
    validation = await validate(payload)
    return {
        "plan_id": f"ocplan-{uuid4().hex[:8]}",
        "cluster": cluster["id"],
        "changes": changes,
        "issues": validation["issues"],
        "gitops": {
            "repo": cluster["repo"],
            "branch": cluster["branch"],
            "path": cluster["path"],
            "engine": cluster.get("gitops", "fluxcd"),
            "provisioner": cluster.get("provisioner", "kustomize"),
        },
    }


async def _run_pipeline(run_id: str) -> None:
    """Advance a run through the openCenter pipeline stages."""
    run = RUNS[run_id]
    stages = [
        ("gitops_commit", "Configuration committed to GitOps", 20),
        ("reconcile", "FluxCD reconciliation triggered", 40),
        ("schedule", "Platform scheduler selected a node", 65),
        ("starting", "Workload starting", 85),
        ("healthy", "Health checks passed", 100),
    ]
    for name, title, progress in stages:
        await asyncio.sleep(STEP_SECONDS)
        run["steps"].append(
            {
                "id": f"{run_id}-{name}",
                "name": name,
                "title": title,
                "message": "",
                "progress": progress,
                "state": "succeeded",
                "at": time.time(),
            }
        )
        run["progress"] = progress
    run["state"] = "succeeded"
    DEPLOYMENTS[run["deployment"]] = {
        "state": "RUNNING",
        "progress": 100,
        "health": "healthy",
        "message": "running",
    }


@app.post("/api/v1/deployments/apply")
async def apply(payload: ApplyRequest) -> dict[str, Any]:
    if not payload.approved_by:
        raise HTTPException(status_code=400, detail="approved_by is required")
    summary = _summarise(payload.spec)
    run_id = f"ocrun-{uuid4().hex[:8]}"
    RUNS[run_id] = {
        "run_id": run_id,
        "deployment": summary["name"],
        "state": "running",
        "progress": 0,
        "approved_by": payload.approved_by,
        "steps": [],
    }
    DEPLOYMENTS[summary["name"]] = {
        "state": "DEPLOYING",
        "progress": 0,
        "health": "unknown",
        "message": "pipeline started",
    }
    asyncio.create_task(_run_pipeline(run_id))
    return {"run_id": run_id, "state": "running"}


@app.get("/api/v1/runs/{run_id}")
async def run_status(run_id: str) -> dict[str, Any]:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return run


@app.get("/api/v1/deployments/{deployment_id}/status")
async def deployment_status(deployment_id: str) -> dict[str, Any]:
    return DEPLOYMENTS.get(
        deployment_id,
        {"state": "DRAFT", "progress": 0, "health": "unknown", "message": "no run"},
    )


@app.post("/api/v1/deployments/{deployment_id}/rollback")
async def rollback(deployment_id: str) -> dict[str, Any]:
    DEPLOYMENTS[deployment_id] = {
        "state": "ROLLED_BACK",
        "progress": 100,
        "health": "rolled_back",
        "message": "rollback complete",
    }
    return {"status": "accepted", "deployment": deployment_id}
