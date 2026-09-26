# Architecture

## The one rule

```text
AI COPILOT      understands + explains
      ↓
AI GOVERNOR     evaluates + recommends
      ↓
HUMAN           decides
      ↓
openCenter      deploys
      ↓
NATIVE SCHEDULER  places the workload
      ↓
AI COPILOT      monitors + recommends improvements
```

These responsibilities are never collapsed. Each one is a separate module with
a separate interface, and the boundaries are enforced by code, not convention:

| Boundary | Enforced by |
|---|---|
| The Copilot cannot decide | it only produces a `WorkloadIntent`; runtime selection lives in the Governor |
| The Governor cannot deploy | it only produces a ranked list and a `DeploymentSpec` |
| The AI cannot approve | `StateMachine.check()` raises `HumanApprovalRequired` for non-human actors on both gates |
| The platform cannot choose a node | placement comes back *from* kube-scheduler / Nova and is recorded, never requested |
| The model cannot see secrets | `inventory.sanitize_inventory()` + `assert_clean()` on every prompt |

## Layers

```text
LAYER 8 — AI INFRA COPILOT + GOVERNOR
                │
                ▼
         HUMAN APPROVAL
                │
                ▼
          openCenter                 (deployment / GitOps)
                │
                ▼
      Kubernetes foundation
                │
                ▼
          Genestack                  (OpenStack on Kubernetes)
                │
        ┌───────┴────────┐
        ▼                ▼
     CPU VM           GPU VM
```

plus Kubernetes CPU workloads, Kubernetes GPU workloads, database services and
Ceph-backed storage.

## Backend modules

```text
backend/app/
├── main.py            FastAPI application
├── container.py       composition root -- the ONLY place that knows the mode
├── config.py          settings (INFRA_MODE drives everything)
│
├── copilot/           understands the request
│   ├── extraction.py    deterministic NL -> IntentPatch (works with no LLM)
│   ├── wizard.py        which questions actually change the answer
│   ├── service.py       conversation, audit, hand-off to the Governor
│   └── llm/             LLMProvider: mock | ollama | openai-compatible
│
├── governor/          evaluates and recommends
│   ├── profiles.yaml    workload classes, sizing rules, runtime affinity
│   ├── sizing.py        intent -> ResourcePlan per candidate runtime
│   ├── scoring.yaml     the weights (tune here, not in code)
│   ├── scoring.py       deterministic score + human-readable reasons
│   ├── recommendation.py  ranks every candidate, keeps the rejects
│   ├── specgen.py       DeploymentSpec (aiinfra/v1alpha1)
│   └── state_machine.py the two human gates
│
├── policies/          deterministic governance
│   ├── rules.py         the Python rules (authoritative)
│   └── engine.py        internal | OPA (rego adds to the Python verdict)
│
├── inventory/         one resource model for every platform
│   ├── service.py       GlobalResourceInventoryService
│   └── sanitize.py      the LLM safety boundary
│
├── deployments/       what happens after approval
│   ├── artifacts.py     DeploymentSpec -> Kubernetes / CNPG / OpenTofu
│   └── service.py       lifecycle, events, audit, rollback
│
├── adapters/          every external system
│   ├── simulation.py    the simulated datacenter (POC)
│   ├── kubernetes.py    inventory: simulated | real
│   ├── openstack.py     inventory: simulated | real (Nova, Placement, Cinder)
│   ├── ceph.py          capacity: simulated | real
│   ├── genestack.py     OpenStack-on-Kubernetes lens (read-only)
│   ├── gpu_host.py      nvidia-smi on the host (never confused with cluster GPUs)
│   ├── opencenter.py    DeploymentBackend: mock | real
│   ├── flux.py          DeploymentBackend: real Kubernetes + Flux (MVP)
│   └── deployment_backend.py   the interface all of them implement
│
├── observability/     metrics, post-deployment advice, remediation
├── capabilities.py    the reality matrix
├── topology.py        the architecture graph the UI draws
├── audit.py           every action, every actor
└── api/               HTTP surface
```

## Execution modes

`INFRA_MODE` selects the adapters in `container.py`. Nothing else in the
codebase branches on the mode.

```python
if settings.open_center_mode == "real":
    backend = RealOpenCenterAdapter(settings)      # any real openCenter
elif settings.is_mvp:
    backend = KubernetesDeploymentBackend(...)     # kind + Flux + real apply
else:
    backend = MockOpenCenterAdapter(settings, infra)   # simulated pipeline
```

The same is true for inventory: `build_kubernetes_adapter`,
`build_openstack_adapter` and `build_ceph_adapter` each return a simulated or a
real implementation of one interface.

## Request lifecycle

```text
POST /api/copilot/messages
  → extraction.extract_intent()          deterministic
  → (optional) LLMProvider.generate()    enrichment only, never authoritative
  → Wizard.next_questions()              at most two, only if they matter
  → RecommendationEngine.recommend()
        SizingCalculator.candidates()        one ResourcePlan per runtime
        PolicyEngine.evaluate()              DENY / REQUIRE / PREFER / DISCOURAGE
        Scorer.score()                       compatibility + capacity + performance
                                             + isolation + reliability + efficiency
                                             - cost - scarcity + policy
  → ranked options + rejected options with reasons
```

Then, only on explicit human action:

```text
POST /api/deployments                    select an option  (WAITING_FOR_HUMAN)
POST /api/deployments/{id}/approve       GATE 1 -> spec + openCenter plan
POST /api/deployments/{id}/apply         GATE 2 -> pipeline runs
```

## Scoring

```text
score = compatibility + capacity + performance + isolation + reliability
      + resource_efficiency - cost_penalty - scarcity_penalty + policy_delta
```

Every weight lives in [`backend/app/governor/scoring.yaml`](../backend/app/governor/scoring.yaml).
Every component also emits the sentence that explains it, so the "why" shown to
the operator is generated from the same numbers that produced the ranking — it
cannot drift from the decision.

## Data model highlights

* `WorkloadIntent` — requirements only, never infrastructure.
* `ResourcePlan` — sizing for one candidate runtime.
* `RecommendationOption` — plan + score + breakdown + reasons + risks + policy.
* `DeploymentSpec` — `aiinfra/v1alpha1 WorkloadDeployment`, the contract with
  openCenter; vendor neutral, no kubectl and no nova in it.
* `Deployment` — lifecycle, history (who moved it), events, placement, audit.

## Storage

A small document store with two backends: in-memory (default, zero
dependencies) and PostgreSQL via SQLAlchemy when `DATABASE_URL` is set. Redis is
optional and used for the shared event channel; the in-process bus is always
authoritative, so Redis being down changes nothing functionally.
