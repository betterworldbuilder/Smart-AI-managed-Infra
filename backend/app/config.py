"""Application settings.

One image, two modes. `INFRA_MODE` is the master switch:

    INFRA_MODE=simulation   the POC -- every adapter is backed by
                            simulation/inventory.yaml
    INFRA_MODE=mvp          real kind/Kubernetes + Flux, with OpenStack /
                            Genestack still simulated
    INFRA_MODE=production   reserved for the real datacenter

Nothing outside `app.container` branches on the mode; everything else receives
adapters through dependency injection.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: backend/app/config.py -> backend/app -> backend -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[2]

SIMULATION = "simulation"
MVP = "mvp"
PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- general ------------------------------------------------------------
    app_name: str = "GPU Native Infra POC"
    api_prefix: str = "/api"
    log_level: str = "INFO"
    cors_origins: str = "*"

    # --- execution mode -----------------------------------------------------
    infra_mode: str = Field(
        default=SIMULATION, description="simulation | mvp | production"
    )
    #: Legacy switch kept for compatibility; INFRA_MODE wins.
    simulation_mode: bool = True
    simulation_dir: Path = Field(default=REPO_ROOT / "simulation")

    openstack_mode: str = Field(default="mock", description="mock | real")
    ceph_mode: str = Field(default="mock", description="mock | real")
    genestack_mode: str = Field(
        default="mock", description="disabled | mock | experimental | real"
    )

    # --- openCenter ---------------------------------------------------------
    open_center_mode: str = Field(default="mock", description="mock | compat | real")
    open_center_url: str = "http://mock-opencenter:8080"
    open_center_token: str = ""
    open_center_gitops_repo: str = "git@git.lab.internal:infra/gitops.git"
    open_center_gitops_branch: str = "main"

    #: Image used for generated container workloads when the request does not
    #: name one. It is a *placeholder* that actually runs, so the MVP can prove
    #: the pipeline end to end; real workloads name their own image.
    default_workload_image: str = "nginxinc/nginx-unprivileged:1.27-alpine"

    # --- MVP / GitOps -------------------------------------------------------
    kind_cluster_name: str = "gpu-native-mvp"
    gitops_working_tree: str = str(REPO_ROOT / "deployments" / "mvp" / "flux" / "workloads" / "generated")
    gitops_branch: str = "main"
    gitops_remote_url: str = ""

    # --- LLM ----------------------------------------------------------------
    llm_provider: str = Field(default="mock", description="mock | openai | ollama")
    llm_base_url: str = "http://localhost:11434"
    llm_api_key: str = ""
    llm_model: str = "qwen3:8b"
    llm_timeout_seconds: float = 30.0
    llm_fallback_to_mock: bool = True

    # --- policy -------------------------------------------------------------
    policy_engine: str = Field(default="internal", description="internal | opa")
    opa_url: str = "http://opa:8181"
    opa_package: str = "aiinfra/authz"

    # --- persistence --------------------------------------------------------
    #: Empty => in-memory store (no PostgreSQL needed).
    database_url: str = ""
    redis_url: str = ""

    # --- auth (POC only) ----------------------------------------------------
    # Off by default: the POC and MVP have no login. AUTH_ENABLED=true brings
    # back the single-account sign-in below.
    auth_enabled: bool = False
    auth_username: str = "admin"
    auth_password: str = "admin"
    auth_secret: str = "poc-insecure-signing-key"
    auth_token_ttl_seconds: int = 60 * 60 * 12

    # --- deployment simulation ----------------------------------------------
    deploy_step_seconds: float = Field(
        default=1.2, description="Delay between simulated openCenter pipeline steps."
    )
    metrics_interval_seconds: float = 5.0

    # --- observability ------------------------------------------------------
    prometheus_enabled: bool = True
    grafana_enabled: bool = True
    prometheus_url: str = ""

    # --- real-mode credentials (never sent to the LLM) ----------------------
    kubeconfig: str = ""
    kubernetes_context: str = ""
    os_auth_url: str = ""
    os_username: str = ""
    os_password: str = ""
    os_project_name: str = ""
    os_user_domain_name: str = "Default"
    os_project_domain_name: str = "Default"
    os_region_name: str = "RegionOne"
    ceph_dashboard_url: str = ""

    # -- validation ----------------------------------------------------------

    @field_validator("infra_mode")
    @classmethod
    def _check_infra_mode(cls, value: str) -> str:
        value = value.lower()
        if value not in {SIMULATION, MVP, PRODUCTION}:
            raise ValueError("INFRA_MODE must be 'simulation', 'mvp' or 'production'")
        return value

    @field_validator("open_center_mode")
    @classmethod
    def _check_oc_mode(cls, value: str) -> str:
        value = value.lower()
        if value not in {"mock", "compat", "real"}:
            raise ValueError("OPEN_CENTER_MODE must be 'mock', 'compat' or 'real'")
        return value

    @field_validator("llm_provider")
    @classmethod
    def _check_llm(cls, value: str) -> str:
        value = value.lower()
        allowed = {"mock", "openai", "openai-compatible", "ollama"}
        if value not in allowed:
            raise ValueError(f"LLM_PROVIDER must be one of {sorted(allowed)}")
        return value

    @field_validator("policy_engine")
    @classmethod
    def _check_policy(cls, value: str) -> str:
        value = value.lower()
        if value not in {"internal", "opa"}:
            raise ValueError("POLICY_ENGINE must be 'internal' or 'opa'")
        return value

    # -- derived -------------------------------------------------------------

    @property
    def is_simulation(self) -> bool:
        return self.infra_mode == SIMULATION

    @property
    def is_mvp(self) -> bool:
        return self.infra_mode == MVP

    @property
    def kubernetes_simulated(self) -> bool:
        return self.infra_mode == SIMULATION

    @property
    def openstack_simulated(self) -> bool:
        return self.openstack_mode != "real"

    @property
    def ceph_simulated(self) -> bool:
        return self.ceph_mode != "real"

    @property
    def needs_simulated_infrastructure(self) -> bool:
        """True while any adapter still reads simulation/inventory.yaml."""
        return (
            self.kubernetes_simulated
            or self.openstack_simulated
            or self.ceph_simulated
            or self.open_center_mode == "mock"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def inventory_file(self) -> Path:
        candidate = self.simulation_dir / "inventory.yaml"
        if candidate.exists():
            return candidate
        return self.simulation_dir / "resources.yaml"

    #: Backwards-compatible alias.
    @property
    def resources_file(self) -> Path:
        return self.inventory_file

    @property
    def scenarios_file(self) -> Path:
        return self.simulation_dir / "scenarios.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()
