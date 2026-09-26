# openCenter integration

openCenter is the deterministic deployment system. The Copilot never runs
commands against infrastructure; it produces a `DeploymentSpec`, a human
approves it, and openCenter turns it into configuration and applies it.

```text
AI recommendation
      ↓
approved DeploymentSpec
      ↓
OpenCenterAdapter
      ↓
generated configuration / GitOps
      ↓
openCenter workflow
```

## Three modes

| `OPEN_CENTER_MODE` | Backend | What it does | Labelled in the UI as |
|---|---|---|---|
| `mock` | `MockOpenCenterAdapter` | the complete workflow against the simulated datacenter | `MOCK` |
| `compat` | `KubernetesDeploymentBackend` | the same contract, driven against real Kubernetes + Flux | `COMPAT` |
| `real` | `RealOpenCenterAdapter` | HTTP against a real openCenter API | `REAL` |

Compatibility mode is **never** labelled as real openCenter. That distinction is
surfaced by `GET /api/system/capabilities` and shown on every page.

## The interface

```python
class DeploymentBackend:
    async def get_clusters()
    async def get_cluster_resources(cluster_id)
    async def generate_deployment(spec)   -> [DeploymentArtifact]
    async def validate_deployment(spec)   -> [ValidationIssue]
    async def plan_deployment(spec, id)   -> DeploymentPlan
    def       apply_deployment(spec, dep) -> AsyncIterator[RunStep]
    async def deployment_status(id)       -> DeploymentStatusReport
    def       rollback_deployment(dep)    -> AsyncIterator[RunStep]
```

`apply` and `rollback` are async generators: the backend streams pipeline steps,
and the platform records each one as a deployment event and publishes it to the
UI. Section 26's shorter aliases (`validate`, `plan`, `deploy`, `status`,
`destroy`) are provided on the base class.

## The HTTP contract (real mode)

`RealOpenCenterAdapter` calls exactly these endpoints:

```text
GET  /api/v1/health
GET  /api/v1/clusters
GET  /api/v1/clusters/{id}/resources
POST /api/v1/deployments/validate   {spec}
POST /api/v1/deployments/plan       {spec, cluster}
POST /api/v1/deployments/apply      {spec, plan_id, approved_by}   -> {run_id}
GET  /api/v1/runs/{run_id}                                          -> {state, steps[]}
GET  /api/v1/deployments/{id}/status
POST /api/v1/deployments/{id}/rollback
```

`approved_by` is mandatory: the deployment system is told *who* authorised the
change, and the audit trail on both sides can be reconciled.

### Trying the real code path locally

The bundled `mock-opencenter` container implements that contract. It is a
separate codebase from the platform on purpose, so the contract stays honest:

```env
OPEN_CENTER_MODE=real
OPEN_CENTER_URL=http://mock-opencenter:8080
```

```bash
./restart.sh
curl http://localhost:8080/api/v1/health
```

The UI will show openCenter as `REAL` because it really is talking to an
openCenter-shaped API over HTTP — the service behind it is a mock, which is
exactly what the "Mock openCenter" title in its OpenAPI docs says.

## Connecting a real installation

1. **Credentials.** `OPEN_CENTER_URL` and `OPEN_CENTER_TOKEN` from the
   environment or a Docker secret. Never in a file under version control; see
   `config/opencenter.yaml.example`.
2. **Clusters.** `GET /api/v1/clusters` must return objects with at least
   `id`, `name`, `type` (`kubernetes` | `openstack`), and ideally the GitOps
   `repo`, `branch` and `path`. The adapter picks the cluster whose `type`
   matches the runtime.
3. **Plan.** If your openCenter returns its own change list, it is used; if not,
   the platform's generated plan is shown instead, so the "what will be created"
   screen is never empty.
4. **Run polling.** Steps are read from `GET /api/v1/runs/{run_id}`; each step
   needs `id`, `name`, `progress` and `state`. Terminal states are
   `succeeded`, `failed`, `cancelled`.
5. **Configuration generation stays local.** The platform generates the
   manifests and hands them over; openCenter decides how to apply them. That
   keeps the generated artefacts reviewable in the approval screen.

## What openCenter receives

The `DeploymentSpec` (section 15) plus the generated artefacts:

| Runtime | Artefacts |
|---|---|
| Kubernetes workload | Namespace, PVC, Deployment, Service, NetworkPolicy, ServiceMonitor, Kustomization, Flux Kustomization |
| Managed database | Namespace, CloudNativePG `Cluster`, `ScheduledBackup`, NetworkPolicy, Kustomization |
| OpenStack VM | OpenTofu module (flavor with `pci_passthrough:alias` for GPUs, volume, security group, instance, port), cloud-init, host prerequisites note |

Everything is inspectable in the UI before the second approval, and readable
afterwards at `GET /api/gitops`.

## What openCenter must never receive from the AI

Shell commands, kubectl invocations, credentials, or anything the LLM produced
directly. The only thing that crosses the boundary is a validated
`DeploymentSpec` that a human approved.
