"""Shared test fixtures.

Every test runs the *real* container -- real Copilot, real Governor, real policy
engine, real state machine -- against the simulated datacenter, with the
pipeline delays turned off.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import Settings  # noqa: E402
from app.container import Container, set_container  # noqa: E402


def make_settings(**overrides) -> Settings:
    defaults = dict(
        infra_mode="simulation",
        simulation_mode=True,
        open_center_mode="mock",
        llm_provider="mock",
        policy_engine="internal",
        database_url="",
        redis_url="",
        deploy_step_seconds=0.0,
        metrics_interval_seconds=0.0,
        auth_enabled=True,
        auth_username="admin",
        auth_password="admin",
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest_asyncio.fixture
async def container():
    instance = Container(make_settings())
    set_container(instance)
    await instance.startup()
    try:
        yield instance
    finally:
        await instance.shutdown()
        set_container(None)


@pytest_asyncio.fixture
async def client(container):
    import httpx

    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.post(
            "/api/auth/login", json={"username": "admin", "password": "admin"}
        )
        assert response.status_code == 200, response.text
        http.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        yield http


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
