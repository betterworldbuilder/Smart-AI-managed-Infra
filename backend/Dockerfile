# syntax=docker/dockerfile:1
#
# Build context is the repository root, so the image can carry the simulated
# datacenter (simulation/) and the rego policies alongside the application.
FROM python:3.12-slim

# openstacksdk needs a C toolchain (netifaces); it is only required for
# OPENSTACK_MODE=real, which is a later phase than the kind MVP. Off by default
# so the image stays small and the build stays fast:
#   docker compose build --build-arg INSTALL_OPENSTACK=true backend
ARG INSTALL_OPENSTACK=false

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements-k8s.txt backend/requirements-openstack.txt /tmp/
# Core + the real Kubernetes client: one image runs the simulated POC and the
# kind-based MVP, selected at runtime by INFRA_MODE.
RUN pip install --no-cache-dir -r /tmp/requirements.txt -r /tmp/requirements-k8s.txt
RUN if [ "$INSTALL_OPENSTACK" = "true" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends gcc python3-dev \
        && pip install --no-cache-dir -r /tmp/requirements-openstack.txt \
        && apt-get purge -y gcc python3-dev \
        && apt-get autoremove -y \
        && rm -rf /var/lib/apt/lists/*; \
    fi

COPY backend /app/backend
COPY simulation /app/simulation
COPY policies /app/policies

ENV PYTHONPATH=/app/backend \
    SIMULATION_DIR=/app/simulation

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=12 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--app-dir", "/app/backend", "--host", "0.0.0.0", "--port", "8000"]
