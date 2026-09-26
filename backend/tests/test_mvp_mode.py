"""MVP mode (INFRA_MODE=mvp) -- the parts that do not need a live cluster.

These tests pin the two properties that matter most when Kubernetes is real:

1. the platform tells the truth about what is real, even when the cluster is
   unreachable (and never calls compatibility mode "real openCenter");
2. everything that can work without the cluster still works -- manifest
   generation, the GitOps working tree, the approval screen -- so an operator
   can see what *would* happen.

Applying to a live cluster is covered by `./selftest-mvp.sh`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from app.container import Container, set_container
from app.governor import build_spec
from app.models import WorkloadIntent
from conftest import make_settings


@pytest_asyncio.fixture
async def mvp(tmp_path):
    """A container in MVP mode pointed at a kubeconfig that does not exist."""
    settings = make_settings(
        infra_mode="mvp",
        simulation_mode=False,
        open_center_mode="compat",
        openstack_mode="mock",
        ceph_mode="mock",
        genestack_mode="mock",
        kubeconfig=str(tmp_path / "missing-kubeconfig"),
        gitops_working_tree=str(tmp_path / "gitops"),
    )
    instance = Container(settings)
    set_container(instance)
    await instance.startup()
    try:
        yield instance
    finally:
        await instance.shutdown()
        set_container(None)


async def test_capabilities_are_honest_without_a_cluster(mvp):
    capabilities = await mvp.capabilities.capabilities()

    assert capabilities["mode"] == "mvp"
    assert capabilities["mode_label"] == "MVP - KIND"
    # The real adapter is in use -- that is what "real" means here ...
    assert capabilities["kubernetes"] == "real"
    # ... but nothing pretends the cluster answered.
    assert capabilities["kubernetes_gpu"] == "unavailable"
    assert capabilities["flux"] == "unavailable"
    # Compatibility mode must never be labelled as a real openCenter.
    assert capabilities["opencenter"] == "compat"
    assert "openCenter" not in capabilities["deployment_engine"]
    # OpenStack stays simulated in this phase, and says so.
    assert capabilities["openstack"] == "simulated"
    assert capabilities["genestack"] == "mock"


async def test_health_reports_the_cluster_as_unreachable(mvp):
    health = await mvp.health()
    assert health["adapters"]["kubernetes"]["reachable"] is False
    assert health["adapters"]["kubernetes"]["simulated"] is False


async def test_reality_matrix_contrasts_poc_and_now(mvp):
    matrix = {row["component"]: row for row in await mvp.capabilities.reality_matrix()}
    assert matrix["Kubernetes"] == {
        "component": "Kubernetes",
        "poc": "simulated",
        "current": "real",
    }
    assert matrix["openCenter"]["current"] == "compat"
    assert matrix["Nova GPU VM"]["current"] == "simulated"


async def test_unreachable_platform_is_never_recommended(mvp):
    intent = WorkloadIntent(
        name="mvp-api", workload_type="web-api", gpu_required=False, concurrent_users=30
    )
    recommendation = await mvp.engine.recommend(intent)
    option = next(
        o
        for o in recommendation.options + recommendation.rejected_options
        if str(o.runtime) == "KUBERNETES_CPU"
    )
    assert not option.viable
    assert any("not reachable" in reason for reason in option.blocked_by)


async def test_manifests_are_generated_without_a_cluster(mvp):
    """The approval screen must work even when the API server is down."""
    intent = WorkloadIntent(
        name="mvp-api", workload_type="web-api", gpu_required=False, concurrent_users=30
    )
    recommendation = await mvp.engine.recommend(intent)
    option = next(
        o
        for o in recommendation.options + recommendation.rejected_options
        if str(o.runtime) == "KUBERNETES_CPU"
    )
    spec = build_spec(intent, option, name="mvp-api")

    clusters = await mvp.backend.get_clusters()
    assert clusters[0]["reachable"] is False, "unreachable, but still described"

    artifacts = await mvp.backend.generate_deployment(spec)
    paths = [artifact.path for artifact in artifacts]
    assert any(path.endswith("/deployment.yaml") for path in paths)
    assert any(path.endswith("/networkpolicy.yaml") for path in paths)
    assert any(path.startswith("deployment-specs/") for path in paths)

    # A container image that actually runs, so the MVP can prove the pipeline.
    assert spec.compute.image


async def test_openstack_runtimes_are_refused_by_the_mvp_backend(mvp):
    intent = WorkloadIntent(
        name="legacy-vm", workload_type="legacy-app", container_compatible=False
    )
    recommendation = await mvp.engine.recommend(intent)
    option = next(
        o
        for o in recommendation.options + recommendation.rejected_options
        if str(o.runtime) == "OPENSTACK_CPU_VM"
    )
    spec = build_spec(intent, option, name="legacy-vm")
    issues = await mvp.backend.validate_deployment(spec)
    assert any(issue.code == "RUNTIME_UNSUPPORTED" for issue in issues), (
        "the kind MVP must refuse OpenStack runtimes rather than pretend"
    )


def test_local_gitops_repo_really_commits(tmp_path):
    from app.adapters.flux import LocalGitOpsRepo
    from app.models import DeploymentArtifact

    repo = LocalGitOpsRepo(Path(tmp_path) / "tree")
    written = repo.write(
        [
            DeploymentArtifact(path="clusters/x/workloads/a/deployment.yaml", content="kind: A\n"),
            DeploymentArtifact(path="clusters/x/workloads/a/service.yaml", content="kind: S\n"),
        ]
    )
    assert len(written) == 2

    result = repo.commit("feat: add a")
    if result.get("reason") == "git unavailable":
        pytest.skip("git is not installed")
    assert result["committed"] is True
    assert result["sha"]
    assert "clusters/x/workloads/a/deployment.yaml" in repo.files()

    removed = repo.remove("/a/")
    assert len(removed) == 2
    repo.commit("revert: remove a")
    assert not [f for f in repo.files() if "/a/" in f]
