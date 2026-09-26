# GPU Native Infrastructure — AI Infra Copilot

# Quick Start

```bash
git clone <repository>
cd gpu-native-infra

./install.sh
./start.sh
```

Then:

```text
Open:

http://localhost:3000
```

Sign in with `admin` / `admin` (**POC ONLY — do not use these credentials in production**).

Even shorter — prerequisites, install, start, health check and demo seed in one command:

```bash
./demo.sh
```

You need **Docker** and nothing else. No GPU, OpenStack, Kubernetes, Ceph,
openCenter, OpenTofu or external LLM is required for the default demo.

---

## Two local modes, one application

| | Mock POC | Local MVP |
|---|---|---|
| Command | `./startpoc.sh` | `./startmvp.sh` |
| Copilot, Governor, policy, approval, audit, UI | **real** | **real** (same code) |
| Kubernetes | simulated | **real kind cluster** |
| FluxCD | simulated | **real** |
| Workloads | simulated | **real pods, services, PVCs** |
| Inventory | `simulation/inventory.yaml` | **read from the cluster API** |
| openCenter | mock | compat (or real) |
| OpenStack / Genestack / Nova / Ceph | simulated | simulated |
| GPU | simulated | real only if the cluster advertises `nvidia.com/gpu` |

The header of the UI always says which one you are looking at, and the
**reality matrix** on the dashboard lists every component with its current
status. The platform never presents something simulated as real.

```bash
./status.sh        # which mode is running, and what is real in it
```

---

## What this is

An AI-assisted control plane for a GPU-native private cloud. An operator
describes what they need in plain language; the platform sizes it, checks live
capacity and policy, ranks the runtimes, and **stops** for a human.

```text
AI COPILOT          understands and explains
      ↓
AI GOVERNOR         scores runtimes deterministically, recommends
      ↓
HUMAN               decides  (two mandatory approval gates)
      ↓
openCenter          generates configuration and deploys
      ↓
NATIVE SCHEDULER    kube-scheduler / Nova place the workload
      ↓
AI COPILOT          monitors and proposes improvements
```

Those responsibilities are never collapsed. The AI cannot deploy: the state
machine refuses `WAITING_FOR_HUMAN → APPROVED` and
`WAITING_FOR_FINAL_APPROVAL → DEPLOYING` for any non-human actor, and every
decision lands in the audit trail with the actor that made it.

### The workflow

```text
user request → intent extraction → inventory → policy → ranked options
   → HUMAN APPROVAL → DeploymentSpec → openCenter → GitOps/Flux
   → Kubernetes / OpenStack → kube-scheduler / Nova → running workload
   → monitoring → post-deployment recommendation
```

### Try it

Open the Copilot and type:

```text
I need a private AI server for 50 employees running Llama with confidential company data.
```

It asks how many concurrent users, checks what is actually free, and comes back
with ranked options — a Kubernetes GPU workload on an L40S and an OpenStack GPU
VM on an H100 — each with its reasons, risks, score breakdown and policy
verdicts. You pick one, review exactly what will be created, and approve twice.

Five scripted scenarios (A–E) are one click away in the Copilot sidebar.

---

## Commands

| Script | What it does |
|---|---|
| `./install.sh` | check Docker, create `.env`, build images, start, verify |
| `./start.sh` | start and wait until genuinely healthy |
| `./stop.sh` | stop, keep data |
| `./restart.sh` | stop + start |
| `./reset.sh` | delete all data and reset the simulation (asks first) |
| `./clean.sh` | remove containers, volumes and generated config (asks first) |
| `./update.sh` | `git pull`, rebuild, restart |
| `./health.sh` | one-screen health report |
| `./logs.sh [service]` | tail logs (`backend`, `frontend`, `postgres`, `opa`, …) |
| `./selftest.sh` | 10 end-to-end checks against the running POC |
| `./demo.sh` | everything above, in order |
| `./setup.sh` | interactive configuration wizard |
| `./enable-ollama.sh` | switch the Copilot to a local Ollama model |
| `./startpoc.sh` / `./stoppoc.sh` | the simulated POC |
| `./startmvp.sh` / `./stopmvp.sh` | the real kind MVP |
| `./resetmvp.sh` | reset MVP data (keeps the cluster) |
| `./selftest-mvp.sh` | 16 end-to-end checks against the MVP |
| `./status.sh` | what is running and what is real |

`make help` lists the equivalent Make targets (`make install`, `make start`,
`make test`, `make health`, …).

---

## URLs

| | |
|---|---|
| UI | http://localhost:3000 |
| API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| Grafana | http://localhost:3001 (`admin` / `admin`) |
| Prometheus | http://localhost:9090 |
| OPA | http://localhost:8181 |
| Mock openCenter | http://localhost:8080 |

---

## The simulated datacenter

Defined entirely in [`simulation/inventory.yaml`](simulation/inventory.yaml) —
edit it to reshape the estate; nothing is hard-coded in the application.

```text
Kubernetes                      OpenStack (Genestack)
  worker-01  32 CPU 128 GB 1×L40S   compute-01      64 vCPU 256 GB
  worker-02  32 CPU 128 GB 1×L40S   compute-gpu-01  64 vCPU 256 GB 2×A10
  worker-03  64 CPU 256 GB 2×H100   compute-gpu-02  64 vCPU 512 GB 2×H100

Ceph: 40 TB total, 25 TB free
```

---

## Architecture at a glance

```text
frontend (React + TypeScript + Vite + Tailwind + React Flow)
backend  (FastAPI + Pydantic)
  ├── copilot/      intent extraction, wizard, LLM providers (mock | Ollama | OpenAI-compatible)
  ├── governor/     sizing, scoring, policy, DeploymentSpec, state machine
  ├── policies/     deterministic rules + OPA/rego
  ├── inventory/    unified resource model + the LLM safety boundary
  ├── deployments/  manifest generation, lifecycle orchestration
  └── adapters/     kubernetes, openstack, ceph, genestack, opencenter, flux, simulation
postgres · redis · opa · mock-opencenter · prometheus · grafana
```

Documentation:

| Document | |
|---|---|
| [docs/architecture.md](docs/architecture.md) | how the pieces fit, and why the boundaries are where they are |
| [docs/poc.md](docs/poc.md) | the simulated POC in detail |
| [docs/mvp.md](docs/mvp.md) | the kind-based MVP: what becomes real |
| [docs/deployment-flow.md](docs/deployment-flow.md) | request → running workload, step by step |
| [docs/opencenter-integration.md](docs/opencenter-integration.md) | mock → compat → real openCenter |
| [docs/genestack-integration.md](docs/genestack-integration.md) | OpenStack on Kubernetes |
| [docs/gpu-passthrough.md](docs/gpu-passthrough.md) | IOMMU → VFIO → Nova → Placement → guest GPU |
| [docs/migration-to-real-openstack.md](docs/migration-to-real-openstack.md) | phase plan from simulation to real Nova GPU VMs |
| [docs/demo.md](docs/demo.md) | the scripted demo, with expected output |
| [docs/troubleshooting.md](docs/troubleshooting.md) | ports, Docker Desktop, WSL, OPA, reset |

---

## Configuration

Everything is environment driven; `.env` is created from
[`.env.example`](.env.example) on first install. The defaults are:

```env
INFRA_MODE=simulation
SIMULATION_MODE=true
LLM_PROVIDER=mock
OPEN_CENTER_MODE=mock
POLICY_ENGINE=opa
AUTH_ENABLED=true          # admin / admin, POC ONLY
```

Switching an integration to real infrastructure is an environment change, not a
code change. `config/*.example` documents each one, and the
**Integrations** page in the UI shows the live status of every adapter.

No secret is required for the default demo, and none is ever sent to the model:
the Copilot receives sanitised capacity facts only (`gpu_type=L40S available=2`),
never kubeconfigs, credentials, tokens or keys — enforced in code and covered by
tests.

---

## Development

```bash
# Backend tests (82 of them: policy, scoring, state machine, spec, API, demo)
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt
cd backend && ../.venv/bin/python -m pytest

# Frontend
cd frontend && npm install && npm run dev      # http://localhost:3000, proxies /api
```

`make test` runs the backend suite (in the container if you have no local
Python).
