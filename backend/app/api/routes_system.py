"""Health, capabilities, integrations and authentication."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from ..auth import WARNING, LoginRequest, TokenResponse, authenticate, issue_token
from ..governor import StateMachine
from .deps import ContainerDep, SettingsDep, UserDep

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(container: ContainerDep) -> dict:
    """Unauthenticated: used by Docker health checks and `health.sh`."""
    return await container.health()


@router.get("/system/capabilities")
async def capabilities(container: ContainerDep) -> dict:
    """What is real right now (POC vs MVP). Unauthenticated on purpose: the UI
    needs it before login so the header can never show the wrong mode."""
    return await container.capabilities.capabilities()


@router.get("/system/reality-matrix")
async def reality_matrix(container: ContainerDep) -> list[dict]:
    return await container.capabilities.reality_matrix()


@router.get("/system/lifecycle")
async def lifecycle() -> list[dict]:
    """The deployment state machine, including which edges require a human."""
    return StateMachine.graph()


@router.get("/settings/integrations")
async def integrations(container: ContainerDep, user: UserDep) -> list[dict]:
    return await container.capabilities.integrations()


@router.post("/auth/login", response_model=TokenResponse)
async def login(payload: LoginRequest, settings: SettingsDep) -> TokenResponse:
    if not settings.auth_enabled:
        return issue_token(settings, payload.username or "demo")
    if not authenticate(settings, payload.username, payload.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
        )
    return issue_token(settings, payload.username)


@router.get("/auth/me")
async def me(user: UserDep, settings: SettingsDep) -> dict:
    return {
        "username": user.username,
        "roles": user.roles,
        "auth_enabled": settings.auth_enabled,
        "warning": WARNING if settings.auth_enabled else None,
    }


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(container: ContainerDep) -> Response:
    body = await container.metrics.prometheus_text()
    return Response(content=body, media_type="text/plain; version=0.0.4")
