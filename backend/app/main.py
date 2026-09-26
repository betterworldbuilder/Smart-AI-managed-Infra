"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import build_api_router
from .config import get_settings
from .container import get_container

DESCRIPTION = """
AI-assisted, GPU-native private cloud control plane.

```
AI Copilot  ->  AI Governor  ->  HUMAN APPROVAL  ->  openCenter  ->  platform
understands     scores and       decides            deploys        schedules
and explains    recommends                                         (Nova / kube-scheduler)
```

The AI never deploys anything. Two human approval gates are enforced by the
state machine, and every step is audited.
"""


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    container = get_container()
    await container.startup()
    logging.getLogger(__name__).info(
        "%s started in %s mode", container.settings.app_name, container.settings.infra_mode
    )
    try:
        yield
    finally:
        await container.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(build_api_router(), prefix=settings.api_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {
            "name": settings.app_name,
            "mode": settings.infra_mode,
            "docs": "/docs",
            "api": settings.api_prefix,
            "health": f"{settings.api_prefix}/health",
        }

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        """Liveness probe that never touches an adapter."""
        return {"status": "ok"}

    return app


app = create_app()
