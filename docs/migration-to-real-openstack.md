# From simulation to real OpenStack GPU VMs

Build in phases, and do not start one until the previous is stable. Each phase
changes **configuration and infrastructure** — none of them changes the
Copilot, the Governor, the policy engine, the approval workflow, the
DeploymentSpec or the UI.

```text
Phase 1  simulated POC                        ✅ done -- ./startpoc.sh
Phase 2  real Kubernetes (kind) + Flux        ✅ done -- ./startmvp.sh
Phase 3  real openCenter                      swap OPEN_CENTER_MODE
Phase 4  real Genestack / OpenStack           inventory becomes real
Phase 5  real Nova CPU VM                     first real VM
Phase 6  real Nova GPU VM (VFIO)              nvidia-smi inside the guest
Phase 7  real Prometheus + DCGM               real post-deployment advice
```

## What changes, per phase

### Phase 3 — real openCenter

```env
OPEN_CENTER_MODE=real
OPEN_CENTER_URL=https://opencenter.example.com
OPEN_CENTER_TOKEN=...        # environment or Docker secret, never a file
```

Everything else stays. `RealOpenCenterAdapter` speaks the contract in
[opencenter-integration.md](opencenter-integration.md); the bundled
`mock-opencenter` container implements it, so you can rehearse the switch
locally first.

**Done when:** a deployment approved in the UI produces an openCenter run id,
and the run's steps stream back into the deployment timeline.

### Phase 4 — real Genestack / OpenStack

```env
OPENSTACK_MODE=real
GENESTACK_MODE=real
CEPH_MODE=real
OS_AUTH_URL=https://keystone.example.com/v3
OS_USERNAME=aiinfra
OS_PROJECT_NAME=aiinfra
# OS_PASSWORD from the environment or a Docker secret
CEPH_DASHBOARD_URL=https://ceph.example.com:8443
```

```bash
docker compose build --build-arg INSTALL_OPENSTACK=true backend
./restart.sh
```

A **read-only** OpenStack user is enough: the platform reads Nova hypervisors,
Placement providers, flavors, images, networks and Cinder pools. Creating
things is openCenter's job, with openCenter's credentials.

**Done when:** the Infrastructure page lists your real compute hosts and the
Integrations page shows OpenStack as `CONNECTED`. The reality matrix flips
OpenStack from `SIMULATED` to `REAL` on its own — you never edit it.

### Phase 5 — a real Nova CPU VM

Nothing to configure: once Phase 4 is real, approving an
`OPENSTACK_CPU_VM` option generates an OpenTofu module and hands it to
openCenter.

```text
Copilot → Governor → human approval → openCenter → OpenStack deployment module
  → Nova API → Nova scheduler → KVM host → CPU VM
```

Start with the documented example: 4 vCPU, 16 GB RAM, 100 GB Cinder, Ubuntu.

**Done when:** `openstack server list` shows the instance, and the placement
recorded in the UI matches the host Nova actually chose.

### Phase 6 — a real Nova GPU VM

The hard one. Follow [gpu-passthrough.md](gpu-passthrough.md) exactly:

1. IOMMU on the kernel command line, host rebooted;
2. the GPU bound to `vfio-pci` (and **not** to the NVIDIA driver on the host);
3. `[pci] device_spec` and `alias` in `nova.conf` with ids discovered from
   `lspci -nn` — see `deployments/mvp/genestack/overrides/nova-gpu-passthrough.yaml`;
4. `PciPassthroughFilter` enabled on the scheduler;
5. Placement reporting the custom GPU resource class;
6. a flavor with `pci_passthrough:alias = <alias>:<count>` — the platform
   generates this for you.

**Done when:** `nvidia-smi` works **inside the guest**. That is the acceptance
test; nothing short of it counts.

The platform helps before you get there: `validate_deployment()` warns when a
compute host does not report IOMMU as enabled, and denies a GPU model whose
VRAM is below the workload's requirement.

### Phase 7 — real metrics

```env
PROMETHEUS_URL=http://prometheus.example.com:9090
```

With DCGM Exporter scraping GPUs and node-exporter on the hosts, the
post-deployment advisor stops working from simulated drift and starts working
from measurements. Its behaviour does not change: it proposes, a human decides.

## What never changes

* the `WorkloadIntent` schema;
* the policy rules (and their rego mirror);
* the scoring weights in `scoring.yaml`;
* the `aiinfra/v1alpha1 WorkloadDeployment` contract;
* the two human approval gates;
* the audit trail;
* the UI.

That is the point of the adapter seam: every phase above is an adapter swap and
an environment change.

## Order of risk

| Phase | Risk | Why |
|---|---|---|
| 3 openCenter | low | HTTP contract, rehearsable against the bundled mock |
| 4 Genestack inventory | low | read-only |
| 5 Nova CPU VM | medium | first real mutation, but a well-trodden path |
| 6 Nova GPU VM | **high** | host BIOS, kernel, driver binding, Nova config, Placement |
| 7 metrics | low | additive |

Do 6 last, on one host, with one card, and verify `nvidia-smi` in the guest
before touching a second machine.
