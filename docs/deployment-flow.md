# Deployment flow

From a sentence to a running workload, with every decision point named.

```text
User request
     ↓
AI Copilot            deterministic extraction (+ optional LLM enrichment)
     ↓
Requirement extraction   WorkloadIntent -- requirements only, no infrastructure
     ↓
Infrastructure inventory  merged Kubernetes + OpenStack + Ceph + GPU telemetry
     ↓
Policy validation      DENY / REQUIRE / PREFER / DISCOURAGE per candidate
     ↓
Recommendation         every viable runtime, scored, with reasons and risks
     ↓
HUMAN APPROVAL  ①      the AI physically cannot pass this point
     ↓
Deployment specification   aiinfra/v1alpha1 WorkloadDeployment
     ↓
openCenter             generate -> validate -> plan
     ↓
HUMAN APPROVAL  ②      "what will be created", shown before anything is created
     ↓
GitOps / FluxCD / OpenTofu / Kustomize
     ↓
Kubernetes / Genestack / OpenStack
     ↓
kube-scheduler / Nova  choose the physical host
     ↓
CPU VM / GPU VM / GPU pod / database
     ↓
Monitoring
     ↓
AI recommendation      right-sizing, scaling, reliability -- never applied alone
```

## The state machine

```text
DRAFT
  ↓
RECOMMENDED
  ↓
WAITING_FOR_HUMAN ───────── APPROVED requires a human ─────────┐
  ↓ (human)                                                    │
APPROVED                                                       │
  ↓ (system: spec + plan)                                      │
PLANNED                                                        │
  ↓                                                            │
WAITING_FOR_FINAL_APPROVAL ─ DEPLOYING requires a human ───────┤
  ↓ (human)                                                    │
DEPLOYING                                                      │
  ↓                                                            │
RUNNING                                                        │
                                                               │
Other states: REJECTED, FAILED, ROLLING_BACK, ROLLED_BACK ─────┘
```

`GET /api/system/lifecycle` returns this graph with `requires_human` and `gate`
flags; the Architecture page renders it.

Attempting either gate as a non-human raises `HumanApprovalRequired`, the API
returns **403**, and the deployment does not move. There is no configuration
that relaxes this.

## Step by step

### 1. The request

```http
POST /api/copilot/messages
{"message": "I need a private AI server for 50 employees running Llama with confidential company data."}
```

The deterministic extractor recognises the workload class, the headcount, the
classification and the GPU requirement. If an LLM is configured it may add
detail the rules missed, but it can never override them, and it never sees
anything but sanitised capacity facts.

### 2. The questions

The wizard asks only what changes the answer — here, concurrency (it drives GPU
count) and environment. Anything with a safe default is offered later on the
Deploy page instead of interrogating the operator.

### 3. Sizing

`20 concurrent / 20 per GPU → 1 GPU`, `8 vCPU`, `64 GB`, `500 GB`. The GPU
*model* is chosen from what is actually free on each platform, filtered by the
VRAM requirement and ordered by the optimisation goal: on Kubernetes the L40S
(48 GB, cheapest that fits), on OpenStack the H100 (the only free card with
≥48 GB).

### 4. Policy

```text
POL-011  classified data -> internal only, NetworkPolicy required
POL-005  container-compatible AI inference -> prefer Kubernetes GPU
POL-006  capacity gate -> deny anything that does not fit
POL-002  no GPU requested -> deny GPU runtimes
```

### 5. Scoring

```text
kubernetes_gpu    91   compatibility 30 · capacity 20 · performance 13.5 · isolation 12
                       reliability 9 · efficiency 10 − cost 3.2 − scarcity 2.5 + policy 2
openstack_gpu_vm  76   stronger isolation, but consumes the last free H100
```

Both are returned. The rejected ones are returned too, each with its reason.

### 6. Human approval ①

`POST /api/deployments` selects an option (state `WAITING_FOR_HUMAN`), then
`POST /api/deployments/{id}/approve` crosses the first gate. That generates the
`DeploymentSpec` and asks openCenter to plan.

### 7. The plan

```text
+ namespace ai-llama-internal
+ GPU workloads 1 x llama-internal (8 vCPU / 64Gi each)
+ GPU allocations 1 x L40S (nvidia.com/gpu)
+ PersistentVolumeClaim llama-internal-data (500Gi ceph-rbd)
+ Service llama-internal (ClusterIP)
+ NetworkPolicy llama-internal-default-deny
+ ServiceMonitor llama-internal (Prometheus)
```

Every generated file is available for inspection before anything runs.

### 8. Human approval ②

`POST /api/deployments/{id}/apply`. Only now does anything happen.

### 9. Execution

```text
accepted          openCenter run created
gitops_commit     7 files committed
reconcile         Flux Kustomization applied
schedule          kube-scheduler selected worker-01, GPU gpu-001 (L40S)
resources         8 vCPU, 64 GB RAM, 500 GB ceph-rbd allocated
starting          1 replica
healthy           workload is READY
```

Streamed to the UI over Server-Sent Events.

### 10. After

Metrics flow, and the post-deployment advisor watches for over- and
under-provisioning. It proposes; `SIMULATE` shows the projection; nothing is
applied without going through the whole approval flow again.

## Rollback

`POST /api/deployments/{id}/rollback` reverts the GitOps commit and releases
every resource the deployment took — GPUs return to the pool, capacity returns
to the nodes, storage returns to the Ceph pool. Covered by
`test_rollback_returns_the_gpu_to_the_pool`.
