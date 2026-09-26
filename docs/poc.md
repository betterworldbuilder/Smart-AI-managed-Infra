# The mock POC

```bash
./startpoc.sh          # or ./install.sh && ./start.sh
```

Everything below the Governor is simulated. Everything at and above it is the
real implementation — the same code the MVP runs.

| Component | POC |
|---|---|
| AI Copilot | **real** (deterministic extractor + optional LLM) |
| AI Governor | **real** |
| Policy engine | **real** (Python rules + OPA/rego) |
| Human approval | **real** (two enforced gates) |
| Audit | **real** |
| UI | **real** |
| Kubernetes | simulated |
| OpenStack / Genestack / Nova | simulated |
| Ceph | simulated |
| GPUs | simulated |
| openCenter | mock (a real-contract mock container is also provided) |

## What runs

```text
frontend          nginx serving the React build, proxying /api
backend           FastAPI: Copilot, Governor, policies, inventory, deployments
postgres          application state, deployments, audit
redis             shared event channel (optional -- the in-process bus always works)
opa               the rego policies from policies/rego
mock-opencenter   a standalone service speaking the real openCenter API
prometheus        scrapes /api/metrics
grafana           a provisioned GPU dashboard
```

Compose project name: `gpuinfra-poc`.

## The simulated datacenter

[`simulation/inventory.yaml`](../simulation/inventory.yaml) is the single source
of truth. Nothing in the Python code hard-codes a GPU model, host name or
capacity — change the file, restart, and the whole platform sees a different
estate.

```text
Kubernetes  k8s-core                     OpenStack  RegionOne (Genestack)
  worker-01   32 CPU  128 GB  1 × L40S     compute-01       64 vCPU  256 GB
  worker-02   32 CPU  128 GB  1 × L40S     compute-gpu-01   64 vCPU  256 GB  2 × A10
  worker-03   64 CPU  256 GB  2 × H100     compute-gpu-02   64 vCPU  512 GB  2 × H100

Ceph  40 TB total, 25 TB free
GPU catalog  A10 (24 GB), L40S (48 GB), H100 (80 GB) with relative cost and perf tier
```

Some GPUs start allocated (an H100 on `worker-03`, an A10 and an H100 in
OpenStack) so the platform has to reason about scarcity from the first request.

## What the simulation actually simulates

It is not a static fixture. The simulated datacenter:

* **allocates per replica**, exactly as a scheduler would — two replicas that
  each need one L40S land on two different workers, and a request for two cards
  on one host fails if no host has two free;
* **runs a bin-packing placement** (most-allocated-that-fits), so placement
  decisions are made at apply time and read back afterwards, never predicted by
  the Governor;
* **charges storage** to the Ceph pool that backs the target platform;
* **drifts utilisation** on a timer so dashboards move and the post-deployment
  advisor has something to observe;
* **releases everything on rollback**, which is asserted by the tests.

## The pipeline

`MockOpenCenterAdapter` implements the complete workflow:

```text
generate_deployment()   the same manifest generator the MVP uses
validate_deployment()   name, capacity, GPU availability, IOMMU, storage,
                        classification vs exposure, HA replicas, image
plan_deployment()       "what will be created" + artefacts + gitops target
apply_deployment()      accepted → GitOps commit → reconcile → schedule →
                        allocate → start → healthy   (streamed to the UI)
deployment_status()
rollback_deployment()   revert commit → release capacity
```

The GitOps repository is in-memory and visible at `GET /api/gitops`, so you can
read every file the platform would have committed.

## Demo scenarios

Five scripted requests (`simulation/scenarios.yaml`) run through the real
Copilot and Governor — no shortcuts. They are also the regression fixtures in
`backend/tests/test_scenarios.py`.

| | Request | Expected |
|---|---|---|
| A | internal web API | `KUBERNETES_CPU` |
| B | Ubuntu VM for legacy software | `OPENSTACK_CPU_VM` |
| C | isolated PyTorch workstation | `OPENSTACK_GPU_VM` |
| D | Llama for 100 employees | `KUBERNETES_GPU` |
| E | production PostgreSQL | `DATABASE_SERVICE` (with the VM alternative shown) |

## Verifying

```bash
./health.sh        # services, inventory, policies, scenarios
./selftest.sh      # 10 checks, ending in a deployment that reaches RUNNING
make test          # 82 backend unit/integration tests
```

## Pacing the demo

`DEPLOY_STEP_SECONDS` (default `1.2`) controls how long each pipeline step
takes. Set it to `0` for instant deployments, or `3` if you are narrating.
