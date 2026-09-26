# Smart AI-Managed Infrastructure

> ### 🚧 Status: proof of concept + early MVP — work in progress
>
> This is a **work starter**, not a finished product. It is deliberately
> published early, at the stage where the architecture is real and testable but
> most of the infrastructure underneath is still simulated.
>
> - **POC (`./startpoc.sh`)** — complete and verified. The Copilot, Governor,
>   policy engine, approval workflow, audit and UI are real; the datacenter
>   under them is simulated.
> - **MVP (`./startmvp.sh`)** — early. Real kind Kubernetes, real Flux, real
>   pods. Not yet verified on the author's machine (see
>   [status](#what-is-real-today)).
> - **VMware, real OpenStack/Genestack, Nova GPU VMs, Ceph, FinOps** — declared
>   in the model, **not implemented**. See the [roadmap](#roadmap).
>
> Expect rough edges, unimplemented adapters and breaking changes. That is the
> point of publishing it now — [help build it](#help-build-it).

**What if deploying infrastructure started with a business goal — not a VM flavor?**

> “Deploy a private AI assistant for 100 employees, keep the data confidential,
> use the resources I already have.”

That sentence is the input. The platform works out what it needs, checks what
you actually have free, applies your policies, ranks the options, explains
itself — and then **stops and waits for a human**.

```bash
git clone git@github.com:betterworldbuilder/Smart-AI-managed-Infra.git
cd Smart-AI-managed-Infra
./demo.sh
```

Open **http://localhost:3000** · sign in `admin` / `admin` · type the sentence above.

You need **Docker**. Nothing else — no GPU, no Kubernetes, no OpenStack, no
Ceph, no openCenter, no API key. The whole datacenter is simulated, and the UI
tells you so on every screen.

**What you are looking at:** a working end-to-end slice of an idea — intent →
recommendation → human approval → deployment → monitoring → optimisation
advice — with the decision layer built for real and the infrastructure layer
mostly simulated. Enough to evaluate the approach, argue with it, and extend
it. Not enough to run your datacenter.

---

## Why

Private cloud is still infrastructure-first. Someone who needs a service ends
up choosing a flavor.

```text
User
  ↓
Choose VM / Flavor          ← a human translating business need into m1.large
  ↓
OpenStack
  ↓
Nova / Placement
  ↓
CPU VM or GPU VM
```

Every step down that path is a human converting intent into infrastructure
vocabulary. It takes an engineer who simultaneously understands the workload,
the capacity, the policies, the GPU estate and the cost — and that person is
the bottleneck, on every request, forever.

Meanwhile GPUs made it worse. They are scarce, expensive, heterogeneous, and
usually bolted onto a platform that was designed for CPUs. "Which GPU, on which
platform, at what isolation level, without stranding the H100s" is a genuinely
hard question that nobody should have to answer by hand forty times a week.

**The interesting problem is not automating the deployment. It is automating
the judgement in front of it — without handing over control.**

---

## What

An AI Copilot that understands the request, a deterministic Governor that
evaluates it, a human who decides, and a deployment system that executes.

```text
Business Intent
      ↓
AI Infra Copilot            understands + explains
      ↓
AI Governor + Policies      evaluates + recommends  (deterministic, not the LLM)
      ↓
HUMAN APPROVAL              decides                 ← two mandatory gates
      ↓
openCenter                  deploys                 (GitOps / Flux / OpenTofu)
      ↓
┌────────────┬────────────┬────────────┐
│ Kubernetes │ OpenStack  │ VMware     │
│ CPU/GPU    │ CPU/GPU VM │ Legacy VM  │
│ workloads  │            │ (roadmap)  │
└────────────┴────────────┴────────────┘
      ↓
kube-scheduler / Nova       places the workload on a physical host
      ↓
Storage · Database · Network · Monitoring
      ↓
AI Copilot                  monitors + proposes improvements
```

Those roles are never collapsed into one another, and the boundaries are
enforced in code rather than by convention:

| Boundary | How it is enforced |
|---|---|
| The Copilot cannot decide | it only emits a `WorkloadIntent` — requirements, no infrastructure |
| The Governor cannot deploy | it only emits ranked options and a `DeploymentSpec` |
| **The AI cannot approve** | `StateMachine.check()` raises `HumanApprovalRequired` for any non-human actor on both gates. No flag relaxes it. |
| The platform cannot pick a node | placement is **read back** from kube-scheduler / Nova, never requested |
| The model cannot see secrets | every prompt goes through `sanitize_inventory()`; `assert_clean()` fails the build if a credential-shaped value gets near one |

### A concrete run

A user asks for a private LLM service for 50 employees with confidential data.

The Copilot asks the one question that changes the answer — *how many
concurrent users?* — then the Governor looks at what is actually free:

```text
Kubernetes    2 × L40S (48 GB) free      OpenStack   1 × A10 (24 GB) free
              1 × H100 (80 GB) free                  1 × H100 (80 GB) free
Ceph          25 TB free                 Policy      confidential-data enabled
```

and returns **every** viable option, scored, with its reasoning:

```text
OPTION A — Kubernetes GPU workload                              score 91
1 × L40S · 8 vCPU · 64 GB RAM · 500 GB ceph-rbd
  + LLM inference maps naturally onto a Kubernetes GPU workload
  + 2 × L40S available on Kubernetes
  + L40S is the lowest-cost GPU that fits the 48 GB VRAM requirement
  + Preserves H100 capacity for workloads that need it
  + No strict VM isolation requirement
                                                            ★ Recommended

OPTION B — OpenStack GPU VM                                     score 76
1 × H100 · 8 vCPU · 64 GB RAM · 500 GB ceph-rbd
  + Provides stronger isolation than a shared-kernel container
  + H100 (80 GB) exceeds the 48 GB VRAM requirement
  ! consumes the last free H100
```

Rejected runtimes stay visible with the rule that killed them
(`POL-020: The workload requires a GPU and this runtime provides none`), and
every score decomposes into compatibility, capacity, performance, isolation,
reliability, efficiency, cost and scarcity. **Nothing in that ranking comes
from the language model** — it is deterministic, configurable in
[`scoring.yaml`](backend/app/governor/scoring.yaml), and reproducible.

The human picks one. Then sees exactly what will be created:

```text
+ namespace ai-llama-internal
+ GPU workloads 1 × llama-internal (8 vCPU / 64Gi each)
+ GPU allocations 1 × L40S (nvidia.com/gpu)
+ PersistentVolumeClaim llama-internal-data (500Gi ceph-rbd)
+ Service llama-internal (ClusterIP)
+ NetworkPolicy llama-internal-default-deny
+ ServiceMonitor llama-internal (Prometheus)
```

…and approves a second time. Only then does anything happen.

After it is running, the Copilot keeps watching and keeps its hands off:

> GPU utilisation is 18%, memory 24%, traffic low.
> Consider a smaller GPU class or time-slicing with another tenant.
> **[SIMULATE]** **[IGNORE]**

`SIMULATE` shows the projection and changes nothing. Applying it means going
through both approval gates again.

### Two modes, one application

```bash
./startpoc.sh     # everything below the Governor is simulated
./startmvp.sh     # real local kind Kubernetes + real Flux + real pods
./status.sh       # which one is running, and what is real inside it
```

Same Copilot, same Governor, same policies, same approval workflow, same UI —
only the adapters differ.

### What is real today

This project takes honesty about its own status seriously enough to render it
in the product: a **reality matrix** on the dashboard, generated from
`/api/system/capabilities`, so a demo can never quietly imply something is real
when it is simulated.

| Component | POC | Local MVP | Verified |
|---|---|---|---|
| AI Copilot, Governor, policy engine | real | real | ✅ 89 tests |
| Human approval gates, audit trail | real | real | ✅ |
| Recommendation + scoring | real | real | ✅ 6/6 scenarios |
| DeploymentSpec + manifest generation | real | real | ✅ |
| Kubernetes inventory & workloads | simulated | **real (kind)** | ⚠️ see below |
| FluxCD | simulated | **real** | ⚠️ |
| openCenter | mock | compat *(never labelled “real”)* | ✅ mock path |
| OpenStack / Genestack / Nova VMs | simulated | simulated | roadmap |
| Ceph | simulated | simulated | roadmap |
| VMware | declared, not implemented | — | roadmap |
| Cost / FinOps | relative cost units only | — | roadmap |

**Verified on the author's machine:** 89 backend tests, `./selftest.sh` 10/10
end to end against the containerised stack (real PostgreSQL, Redis, OPA), all
six demo scenarios reaching their expected runtime through the live API,
frontend typechecked and built.

**Not verified:** the kind MVP would not boot here — Docker 29's `overlayfs`
storage driver cannot host kind's nested containerd snapshotter.
`startmvp.sh` detects this and prints the fix;
[docs/troubleshooting.md](docs/troubleshooting.md) documents it. The MVP code
paths that do not need a live cluster **are** covered by tests. If you run it
successfully on your machine, please open an issue and say so — that is
genuinely useful to me.

### Roadmap

Everything below is **declared in the data model and unimplemented**. The
adapter interfaces exist and are waiting for them, which is why each is a
tractable contribution rather than a rewrite.

| Phase | What it adds | Status |
|---|---|---|
| 1 · Simulated POC | the whole workflow, simulated infrastructure | ✅ done |
| 2 · Local MVP | real kind Kubernetes, real Flux, real pods and PVCs | 🚧 built, needs verification |
| 3 · Real openCenter | swap `OPEN_CENTER_MODE=real`; HTTP contract already implemented and rehearsable against the bundled mock | ⬜ planned |
| 4 · Genestack / OpenStack | real Nova, Placement, Glance, Cinder, Neutron inventory | ⬜ planned |
| 5 · Nova CPU VM | first real VM created through the workflow | ⬜ planned |
| 6 · Nova GPU VM | IOMMU → VFIO → `pci.device_spec` → Placement → flavor alias → `nvidia-smi` in the guest | ⬜ planned |
| 7 · Real observability | Prometheus + DCGM driving the post-deployment advisor | ⬜ planned |

Beyond the phase plan:

| Feature | Current state | Notes |
|---|---|---|
| **VMware** | `VMWARE_VM` declared, disabled | needs a `VMwareDeploymentBackend` + inventory adapter; the interfaces are ready |
| **Ceph** | simulated capacity | real adapter stubbed against the dashboard API |
| **vGPU / MIG / SR-IOV** | modelled in `GPUAllocationType` | needs allocation + Placement/device-plugin plumbing |
| **Mixed-GPU placement** | one model per workload | 2 replicas are refused when 1 × L40S and 1 × H100 are free — per-replica assignment needed |
| **FinOps / budgets** | relative cost units only | no pricing, no per-team budgets, no showback |
| **Multi-tenancy / RBAC** | single demo account | `admin`/`admin`, POC only |
| **Bare metal** | `BARE_METAL` declared, disabled | same shape as VMware |

Phases are ordered by risk on purpose: phase 6 touches host BIOS, kernel
parameters and driver binding, so it comes last. See
[docs/migration-to-real-openstack.md](docs/migration-to-real-openstack.md).

---

## So What

| Classic private cloud | AI-native private cloud |
|---|---|
| Infrastructure-first | **Business-intent-first** |
| VM-centric | **Workload-centric** |
| GPU bolted on afterwards | **GPU is a first-class resource** |
| An engineer picks the architecture | **The platform recommends, with reasons** |
| Capacity checked per platform, by hand | **One view across Kubernetes, OpenStack and Ceph** |
| Static provisioning | **Continuous optimisation proposals** |
| Automation executes commands | **Infrastructure understands intent** |

The shift this is reaching for:

> **Infrastructure as a Service → Infrastructure as Intent.**

But the part I care about most is the constraint, not the capability. It is
easy to build an agent that runs `kubectl`. It is harder — and far more useful
in a real organisation — to build one that is **structurally incapable** of
deploying on its own, whose reasoning is deterministic and auditable, whose
recommendations survive a compliance review, and whose every suggestion,
policy verdict and human decision is in an audit log.

That is why the LLM here does not rank anything, does not choose a node, and
never sees a credential. It understands the request and explains the result.
The judgement is deterministic policy plus configurable scoring; the authority
is human.

An AI infrastructure platform people will actually trust in production has to
be designed that way from the first commit, not retrofitted after the first
incident.

---

## What Now

### Run it

```bash
./demo.sh          # prerequisites → install → start → health → seeded demo
./selftest.sh      # 10 end-to-end checks against the running stack
```

Then read [docs/demo.md](docs/demo.md) for the guided walkthrough, or press
**Start guided demo** in the UI.

| | |
|---|---|
| [docs/architecture.md](docs/architecture.md) | how it fits together, and why the boundaries are where they are |
| [docs/deployment-flow.md](docs/deployment-flow.md) | request → running workload, step by step |
| [docs/poc.md](docs/poc.md) · [docs/mvp.md](docs/mvp.md) | the two modes in detail |
| [docs/opencenter-integration.md](docs/opencenter-integration.md) | mock → compat → real |
| [docs/genestack-integration.md](docs/genestack-integration.md) | OpenStack on Kubernetes |
| [docs/gpu-passthrough.md](docs/gpu-passthrough.md) | IOMMU → VFIO → Nova → Placement → guest GPU |
| [docs/migration-to-real-openstack.md](docs/migration-to-real-openstack.md) | the phase plan to real Nova GPU VMs |

### Help build it

This is a work-in-progress POC with real architecture underneath and a lot of
obvious next steps. I would rather build it with people than alone.

The [roadmap](#roadmap) lists what is declared but unimplemented. Below are the
places I would start — each one a real, known gap with an interface already
waiting for it, not busywork:

**Scheduling and GPUs**
- **Mixed-GPU placement.** Today a workload gets one GPU model for all
  replicas, so “2 replicas” is refused when 1 × L40S and 1 × H100 are free.
  Per-replica model assignment, with a policy on whether heterogeneity is
  acceptable for a given workload class.
- **vGPU and MIG.** `GPUAllocationType` already models them; the allocation and
  Placement/device-plugin plumbing does not exist yet.
- **NUMA and topology awareness** in the scoring (GPU ↔ NIC ↔ CPU locality).

**Platforms** *(roadmap phases 2–6)*
- **Get the kind MVP green** on Docker 29 / Podman / Docker Desktop and tell me
  what you had to change. This is the single most useful thing right now.
- **VMware adapter.** `VMWARE_VM` is declared and disabled; implement
  `VMwareDeploymentBackend` + an inventory adapter against the same interfaces
  the Kubernetes and OpenStack ones use.
- **Genestack / real OpenStack** — phases 4–6 are unclaimed.

**The Governor**
- **A real cost model.** Scoring uses relative cost units; wire in actual
  pricing, per-team budgets, showback, and “stay within budget” as a first-class
  constraint.
- **More policy rules** (data residency, maintenance windows, quota per team),
  and expanding the rego mirror in [`policies/rego/`](policies/rego/).
- **Better intent extraction** — the deterministic extractor is decent and
  beatable.

**Product**
- Deployment **diff view** between recommendation and running state.
- **Multi-tenancy** and RBAC beyond the POC's single demo account.
- Anything on the [Observability](docs/demo.md) page you find unconvincing.

Good first steps: run `./demo.sh`, break something, and open an issue. Or pick
a row from the reality matrix that says *simulated* and make it real.

```bash
make help        # every task
make test        # 89 backend tests
cd frontend && npm run dev
```

### Connect

I am building this because I think the next generation of AI/cloud engineers
will have to combine **AI, cloud architecture, Kubernetes, OpenStack, GPU
infrastructure, automation, FinOps and business understanding** — not treat
them as separate disciplines. This repository is me doing that work in public.

If you are hiring for **AI Infrastructure, AI/Cloud Architecture, GPU
Platforms, GenAI Solutions, MLOps, Private Cloud or AI Transformation**, I
would be glad to talk.

**I am not looking to just operate infrastructure. I want to help build the
next generation of it.**

---

<sub>`#AI` `#AIInfrastructure` `#CloudArchitecture` `#OpenStack` `#Kubernetes`
`#GPU` `#PrivateCloud` `#GenAI` `#MLOps` `#AIOps` `#PlatformEngineering`</sub>
