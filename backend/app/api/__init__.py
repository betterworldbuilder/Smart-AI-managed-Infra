"""HTTP API."""

from fastapi import APIRouter

from .routes_copilot import recommendations_router
from .routes_copilot import router as copilot_router
from .routes_deployments import gitops_router
from .routes_deployments import router as deployments_router
from .routes_inventory import router as inventory_router
from .routes_inventory import topology_router
from .routes_observability import (
    advisor_router,
    audit_router,
    events_router,
    metrics_router,
    remediation_router,
    scenarios_router,
)
from .routes_system import router as system_router


def build_api_router() -> APIRouter:
    router = APIRouter()
    for child in (
        system_router,
        inventory_router,
        topology_router,
        copilot_router,
        recommendations_router,
        deployments_router,
        gitops_router,
        metrics_router,
        advisor_router,
        remediation_router,
        audit_router,
        scenarios_router,
        events_router,
    ):
        router.include_router(child)
    return router


__all__ = ["build_api_router"]
