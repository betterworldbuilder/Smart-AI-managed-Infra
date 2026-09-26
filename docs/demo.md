# The demo

```bash
./demo.sh
```

Then open **http://localhost:3000** and sign in with `admin` / `admin`.

The first-run dialog offers **Start guided demo**, which types the Llama
request for you. What follows is the scripted version, with the output you
should expect.

---

## The POC success test (specification §32)

### 1. The request

> I need a private AI server for 50 employees running Llama with confidential
> company data.

The Copilot extracts, deterministically:

```text
workload_type    llm-inference
gpu_required     true
expected_users   50
sensitive_data   confidential
name             llama-internal
```

### 2. The question

It asks only what changes the answer:

```text
How many concurrent users should it handle at peak?
  Why I ask: concurrency drives GPU count and replica count far more than
  total headcount.

Is this for development, test or production?
```

Answer **20** and **prod**.

### 3. The options

```text
OPTION A — Kubernetes GPU workload                     score 91
1 x L40S, 8 vCPU, 64 GB RAM, 500 GB ceph-rbd
+ LLM inference service maps naturally onto a Kubernetes GPU workload
+ 2 x L40S available on Kubernetes
+ L40S is the lowest-cost GPU that fits
+ Preserves H100 capacity for workloads that need it
+ No strict VM isolation requirement
Recommended

OPTION B — OpenStack GPU VM                            score 76
1 x H100, 8 vCPU, 64 GB RAM, 500 GB ceph-rbd
+ Provides stronger isolation than a shared-kernel container
+ H100 (80 GB) exceeds the 48 GB VRAM requirement
! consumes the last free H100
```

Both remain available. Rejected runtimes are listed separately with the rule
that rejected them, e.g.

```text
kubernetes_cpu     POL-020: The workload requires a GPU and this runtime provides none.
database_service   POL-013: This workload is not a database.
```

Expand **Score breakdown and policy checks** on any option to see every
number and every rule that fired.

### 4. Select Option A

State becomes `WAITING_FOR_HUMAN`. Nothing has been deployed.

### 5. Approve — gate 1 of 2

The Governor generates the DeploymentSpec:

```yaml
apiVersion: aiinfra/v1alpha1
kind: WorkloadDeployment
metadata:
  name: llama-internal
intent:
  workload: llm-inference
  environment: prod
runtime:
  type: kubernetes
  accelerator: gpu
  mode: container
compute:
  replicas: 1
  cpu: 8
  memory: 64Gi
gpu:
  vendor: nvidia
  model: L40S
  countPerReplica: 1
  allocationType: timeslice
  memoryGb: 48
storage:
  type: persistent
  backend: ceph-rbd
  size: 500Gi
network:
  exposure: internal
  networkPolicy: true
security:
  classification: confidential
availability:
  highAvailability: false
observability:
  serviceMonitor: true
  gpuMetrics: true
```

and openCenter returns a plan:

```text
+ namespace ai-llama-internal
+ GPU workloads 1 x llama-internal (8 vCPU / 64Gi each)
+ GPU allocations 1 x L40S (nvidia.com/gpu)
+ PersistentVolumeClaim llama-internal-data (500Gi ceph-rbd)
+ Service llama-internal (ClusterIP)
+ NetworkPolicy llama-internal-default-deny
+ ServiceMonitor llama-internal (Prometheus)
```

Every generated file is expandable. Still nothing deployed.

### 6. Approve deployment — gate 2 of 2

```text
accepted        openCenter run created
gitops_commit   7 files committed to the GitOps repository
reconcile       FluxCD reconciliation
schedule        kube-scheduler selected worker-01 — GPU gpu-001 (L40S)
resources       8 vCPU, 64 GB RAM, 500 GB ceph-rbd allocated
starting        1 replica
healthy         workload is READY
```

Streamed live. The **Architecture** page highlights the exact path taken.

### 7. Running

```text
DEPLOYED

Status:  HEALTHY
GPU:     L40S on worker-01
GPU utilisation:  ~40%
RAM:     38 GB / 64 GB
Storage: 124 GB / 500 GB
```

The dashboard's free-GPU count drops from 5 to 4.

### 8. The Copilot keeps working

On **Observability**, press **Analyse now**:

```text
llama-internal: GPU is mostly idle
  · GPU utilisation: 18%
  · GPU memory: 24%
Consider a smaller GPU class or time-slicing this workload with another tenant.

[SIMULATE]  [IGNORE]
```

**SIMULATE** shows the projection and changes nothing:

```text
gpu_released: 0
projected_gpu_utilization_pct: 18.0
note: Simulation only -- nothing was changed. Applying this requires a new
      recommendation and the usual two approvals.
```

### 9. Remediation

**Simulate a GPU failure**:

```text
GPUWorkerUnhealthy on worker-01
GPU worker worker-01 reports an unhealthy L40S (gpu-001); DCGM health check failed.

A. Drain the node and reschedule its workloads   ★ recommended
B. Restart the GPU operator on the node
C. Put the node into maintenance mode

[Approve A]  [Investigate]  [Ignore]
```

### 10. The audit trail

Every step, with the actor:

```text
user_request               admin   (human)
ai_interpretation          ai-copilot (ai)
ai_question                ai-copilot (ai)
ai_recommendation          ai-copilot (ai)
policy_evaluation          system
deployment_option_selected admin   (human)
deployment_approved        admin   (human)   ← gate 1
deployment_spec_generated  system
deployment_plan_generated  system
deployment_final_approved  admin   (human)   ← gate 2
deployment_execution       system
state_transition           system
deployment_result          system
```

Filter by actor to see exactly what the AI did and did not do.

---

## The five scenarios

One click each in the Copilot sidebar. They run through the real engine.

| | Request | Recommends | Why |
|---|---|---|---|
| A | internal web API | Kubernetes CPU | stateless, container friendly, no GPU |
| B | Ubuntu VM for legacy software | OpenStack CPU VM | cannot be containerised |
| C | isolated PyTorch workstation | OpenStack GPU VM | strict isolation → VM, cheapest GPU for dev |
| D | Llama for 100 employees | Kubernetes GPU | container compatible, no isolation requirement |
| E | production PostgreSQL | Managed database service | operator gives failover, backups, PITR — VM alternative shown |

---

## Things worth pointing out during a demo

**The AI cannot deploy.** From the API:

```bash
# there is no way to ask the platform to approve on your behalf
curl -X POST localhost:8000/api/deployments/<id>/approve   # 401 without a token
```

The state machine refuses both gates for any non-human actor, and the tests in
`backend/tests/test_human_in_the_loop.py` assert it.

**The AI does not choose the node.** The Governor recommends a *runtime*;
`kube-scheduler` or Nova picks the host, and the UI reports what they chose.

**The AI never sees a secret.** `GET /api/system/capabilities` shows the
provider; `inventory/sanitize.py` is the only path to a prompt, and it strips
anything credential-shaped. Try adding a fake kubeconfig to the inventory — the
`assert_clean()` test fails the build.

**Nothing here is real.** The header says `POC — SIMULATION`, the reality
matrix lists every component, and `./startmvp.sh` is how you make the
Kubernetes half real.

---

## Resetting between demos

```bash
./reset.sh          # fresh datacenter, empty audit trail
```

Or leave it running: allocated GPUs and deployment history make the second
demo more interesting, because the Governor has to reason about scarcity.
