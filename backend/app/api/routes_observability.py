"""Metrics, post-deployment advice, remediation, audit, scenarios and events."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..models import ClusterMetrics, RemediationAlert
from .deps import ContainerDep, UserDep

metrics_router = APIRouter(prefix="/metrics", tags=["observability"])


@metrics_router.get("/summary", response_model=ClusterMetrics)
async def summary(container: ContainerDep, user: UserDep):
    return await container.metrics.cluster_metrics()


@metrics_router.get("/workloads")
async def workloads(container: ContainerDep, user: UserDep) -> list[dict]:
    items = await container.metrics.all_workload_metrics()
    return [item.model_dump(mode="json") for item in items]


advisor_router = APIRouter(prefix="/advisor", tags=["advisor"])


@advisor_router.get("")
async def list_advice(container: ContainerDep, user: UserDep) -> list[dict]:
    return [item.model_dump(mode="json") for item in await container.advisor.list()]


@advisor_router.post("/analyse")
async def analyse(container: ContainerDep, user: UserDep) -> list[dict]:
    """Ask the post-deployment Copilot to look at everything running."""
    produced = await container.advisor.analyse()
    return [item.model_dump(mode="json") for item in produced]


@advisor_router.post("/{advice_id}/simulate")
async def simulate(advice_id: str, container: ContainerDep, user: UserDep) -> dict:
    try:
        advice = await container.advisor.simulate(advice_id, user=user.username)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return advice.model_dump(mode="json")


@advisor_router.post("/{advice_id}/ignore")
async def ignore(advice_id: str, container: ContainerDep, user: UserDep) -> dict:
    try:
        advice = await container.advisor.ignore(advice_id, user=user.username)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return advice.model_dump(mode="json")


remediation_router = APIRouter(prefix="/remediation", tags=["remediation"])


class RemediationDecision(BaseModel):
    option: str


@remediation_router.get("/alerts")
async def alerts(container: ContainerDep, user: UserDep) -> list[dict]:
    return [alert.model_dump(mode="json") for alert in await container.remediation.list()]


@remediation_router.post("/alerts")
async def ingest_alert(
    alert: RemediationAlert, container: ContainerDep, user: UserDep
) -> dict:
    """Webhook for Alertmanager (or the demo button)."""
    stored = await container.remediation.ingest(alert)
    return stored.model_dump(mode="json")


@remediation_router.post("/simulate-gpu-failure")
async def simulate_gpu_failure(container: ContainerDep, user: UserDep) -> dict:
    try:
        alert = await container.remediation.simulate_gpu_failure()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return alert.model_dump(mode="json")


@remediation_router.post("/alerts/{alert_id}/decide")
async def decide(
    alert_id: str, payload: RemediationDecision, container: ContainerDep, user: UserDep
) -> dict:
    try:
        alert = await container.remediation.decide(alert_id, payload.option, user=user.username)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return alert.model_dump(mode="json")


audit_router = APIRouter(prefix="/audit", tags=["audit"])


@audit_router.get("")
async def audit(container: ContainerDep, user: UserDep, limit: int = 200) -> list[dict]:
    return [event.model_dump(mode="json") for event in await container.audit.list(limit)]


scenarios_router = APIRouter(prefix="/scenarios", tags=["scenarios"])


@scenarios_router.get("")
async def list_scenarios(container: ContainerDep, user: UserDep) -> list[dict]:
    return [scenario.model_dump() for scenario in container.scenarios.list()]


@scenarios_router.post("/{key}/run")
async def run_scenario(key: str, container: ContainerDep, user: UserDep) -> dict:
    try:
        result = await container.scenarios.run(key, user=user.username)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result.model_dump(mode="json")


events_router = APIRouter(tags=["events"])


@events_router.get("/events/recent")
async def recent_events(container: ContainerDep, user: UserDep, limit: int = 50) -> list[dict]:
    return container.bus.recent(limit)


@events_router.get("/events/stream")
async def stream(container: ContainerDep) -> StreamingResponse:
    """Server-Sent Events. Unauthenticated so `EventSource` can connect without
    custom headers; it carries no data that is not already on the dashboard."""
    return StreamingResponse(
        container.bus.stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@events_router.websocket("/ws")
async def websocket(socket: WebSocket) -> None:
    from ..container import get_container

    container = get_container()
    await socket.accept()
    with container.bus.subscribe() as queue:
        for event in container.bus.recent(10):
            await socket.send_json(event)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    await socket.send_json({"topic": "ping", "data": {}})
                    continue
                await socket.send_json(event)
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001 - client went away mid-send
            return
