"""Deployment lifecycle endpoints -- including the two human approval gates."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from ..governor.state_machine import HumanApprovalRequired, TransitionError
from ..models import Deployment, WorkloadIntent
from .deps import ContainerDep, UserDep, human_actor

router = APIRouter(prefix="/deployments", tags=["deployments"])


class CreateDeployment(BaseModel):
    recommendation_id: str
    option: str
    name: str | None = None


class Decision(BaseModel):
    note: str = ""


def _handle(exc: Exception) -> HTTPException:
    if isinstance(exc, HumanApprovalRequired):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, TransitionError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


@router.get("", response_model=list[Deployment])
async def list_deployments(container: ContainerDep, user: UserDep):
    return await container.deployments.list()


@router.post("", response_model=Deployment, status_code=201)
async def create_deployment(
    payload: CreateDeployment, container: ContainerDep, user: UserDep
):
    try:
        return await container.deployments.create_from_recommendation(
            payload.recommendation_id, payload.option, human_actor(user), payload.name
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/{deployment_id}", response_model=Deployment)
async def get_deployment(deployment_id: str, container: ContainerDep, user: UserDep):
    try:
        return await container.deployments.get(deployment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{deployment_id}/approve", response_model=Deployment)
async def approve(
    deployment_id: str, payload: Decision, container: ContainerDep, user: UserDep
):
    """HUMAN APPROVAL GATE 1: approve the recommended option."""
    try:
        return await container.deployments.approve(deployment_id, human_actor(user), payload.note)
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/{deployment_id}/apply", response_model=Deployment)
async def apply(
    deployment_id: str, payload: Decision, container: ContainerDep, user: UserDep
):
    """HUMAN APPROVAL GATE 2: authorise execution of the plan."""
    try:
        return await container.deployments.apply(deployment_id, human_actor(user), payload.note)
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/{deployment_id}/reject", response_model=Deployment)
async def reject(
    deployment_id: str, payload: Decision, container: ContainerDep, user: UserDep
):
    try:
        return await container.deployments.reject(deployment_id, human_actor(user), payload.note)
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.post("/{deployment_id}/modify")
async def modify(
    deployment_id: str, intent: WorkloadIntent, container: ContainerDep, user: UserDep
) -> dict:
    try:
        deployment, recommendation = await container.deployments.modify(
            deployment_id, intent, human_actor(user)
        )
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc
    return {
        "deployment": deployment.model_dump(mode="json"),
        "recommendation": recommendation.model_dump(mode="json"),
    }


@router.post("/{deployment_id}/rollback", response_model=Deployment)
async def rollback(
    deployment_id: str, payload: Decision, container: ContainerDep, user: UserDep
):
    try:
        return await container.deployments.rollback(deployment_id, human_actor(user), payload.note)
    except Exception as exc:  # noqa: BLE001
        raise _handle(exc) from exc


@router.get("/{deployment_id}/spec")
async def spec(deployment_id: str, container: ContainerDep, user: UserDep) -> dict:
    deployment = await _require(container, deployment_id)
    if deployment.spec is None:
        raise HTTPException(status_code=404, detail="the spec is generated on approval")
    return {"spec": deployment.spec.to_wire(), "yaml": deployment.spec.to_yaml()}


@router.get("/{deployment_id}/spec.yaml", response_class=Response)
async def spec_yaml(deployment_id: str, container: ContainerDep, user: UserDep) -> Response:
    deployment = await _require(container, deployment_id)
    if deployment.spec is None:
        raise HTTPException(status_code=404, detail="the spec is generated on approval")
    return Response(content=deployment.spec.to_yaml(), media_type="text/yaml")


@router.get("/{deployment_id}/plan")
async def plan(deployment_id: str, container: ContainerDep, user: UserDep) -> dict:
    deployment = await _require(container, deployment_id)
    if deployment.plan is None:
        raise HTTPException(status_code=404, detail="no plan yet")
    return {
        "plan": deployment.plan.model_dump(mode="json"),
        "changes": deployment.plan.rendered_changes(),
    }


@router.get("/{deployment_id}/artifacts")
async def artifacts(deployment_id: str, container: ContainerDep, user: UserDep) -> list[dict]:
    deployment = await _require(container, deployment_id)
    if deployment.plan is None:
        return []
    return [artifact.model_dump() for artifact in deployment.plan.artifacts]


@router.get("/{deployment_id}/events")
async def events(deployment_id: str, container: ContainerDep, user: UserDep) -> list[dict]:
    deployment = await _require(container, deployment_id)
    return [event.model_dump(mode="json") for event in deployment.events]


@router.get("/{deployment_id}/status")
async def status(deployment_id: str, container: ContainerDep, user: UserDep) -> dict:
    deployment = await _require(container, deployment_id)
    report = await container.backend.deployment_status(deployment_id)
    payload = {
        "deployment": deployment.model_dump(mode="json"),
        "backend": report.model_dump(mode="json"),
    }
    live = getattr(container.backend, "workload_status", None)
    if live is not None and deployment.spec is not None:
        payload["live"] = await live(
            f"ai-{deployment.spec.metadata.name}", deployment.spec.metadata.name
        )
    return payload


@router.get("/{deployment_id}/audit")
async def audit(deployment_id: str, container: ContainerDep, user: UserDep) -> list[dict]:
    events = await container.audit.for_deployment(deployment_id)
    return [event.model_dump(mode="json") for event in events]


@router.get("/{deployment_id}/metrics")
async def metrics(deployment_id: str, container: ContainerDep, user: UserDep) -> dict:
    deployment = await _require(container, deployment_id)
    return (await container.metrics.workload_metrics(deployment)).model_dump(mode="json")


gitops_router = APIRouter(prefix="/gitops", tags=["gitops"])


@gitops_router.get("")
async def gitops(container: ContainerDep, user: UserDep) -> dict:
    """What the deployment backend has written to the GitOps repository."""
    repository = getattr(container.backend, "gitops_repository", None)
    if repository is not None:
        return repository()
    gitops_repo = getattr(container.backend, "gitops", None)
    if gitops_repo is not None:
        return {
            "repo": str(gitops_repo.root),
            "branch": gitops_repo.branch,
            "head": gitops_repo.head(),
            "files": gitops_repo.files(),
            "commits": [],
        }
    return {"repo": None, "files": [], "commits": []}


async def _require(container, deployment_id: str):
    try:
        return await container.deployments.get(deployment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
