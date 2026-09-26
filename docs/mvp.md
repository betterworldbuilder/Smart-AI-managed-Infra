# The local MVP (real kind Kubernetes)

```bash
./startmvp.sh
./selftest-mvp.sh
```

Same application, same Copilot, same Governor, same approval workflow — but the
Kubernetes layer underneath is a **real cluster**, Flux is **really installed**,
and an approved DeploymentSpec produces **real pods**.

## Reality matrix

| Component | POC | MVP |
|---|---|---|
| Copilot | Real | Real |
| Governor | Real | Real |
| Policy | Real | Real |
| Human approval | Real | Real |
| Kubernetes | Simulated | **Real kind** |
| Flux | Simulated | **Real** |
| Kubernetes workloads | Simulated | **Real** |
| Resource inventory | Simulated | **Real K8s** |
| openCenter | Mock | **Compat** (or real) |
| Genestack | Mock | Optional |
| OpenStack | Simulated | Simulated |
| Nova CPU VM | Simulated | Simulated |
| Nova GPU VM | Simulated | Simulated |
| Host GPU inventory | Simulated | **Real if present** |
| GPU K8s scheduling | Simulated | Real **only if a GPU is exposed** |

The same table is rendered live in the UI (dashboard and Integrations page) from
`GET /api/system/capabilities`, so it can never go stale.

## What `startmvp.sh` does

```text
check Docker, kind, kubectl, helm       (offers to install kind/kubectl on Linux)
create the kind cluster                 1 control plane + KIND_WORKERS workers
write deployments/mvp/kubeconfig
install FluxCD                          flux CLI, or the pinned release manifest
apply namespaces + the ceph-rbd StorageClass
load policies/rego into a ConfigMap     the same files the POC uses
install CloudNativePG                   (optional, makes the database path real)
build aiinfra-backend:mvp and aiinfra-frontend:mvp
kind load docker-image                  no registry required
deploy postgres, redis, opa, backend, frontend, prometheus
wait for readiness, then report capabilities
```

Ports are mapped by the kind config, so the URLs do not change:
`http://localhost:3000` (UI), `http://localhost:8000` (API),
`http://localhost:9090` (Prometheus).

## What becomes real

**Inventory.** `RealKubernetesAdapter` reads nodes, allocatable CPU/memory, pod
requests, GPU capacity (`nvidia.com/gpu`), GPU Feature Discovery labels,
deployments, PVCs and storage classes. The dashboard numbers are the cluster's
numbers.

**Deployment.** `KubernetesDeploymentBackend`:

1. generates the manifests (the *same* generator as the POC),
2. writes them to the local GitOps working tree and commits,
3. applies them with server-side apply,
4. waits for pods and **reads back** `pod.spec.nodeName` — the placement shown
   in the UI is the decision kube-scheduler actually made,
5. on rollback: reverts the commit and deletes the namespace.

**Flux.** Really installed, and its Kustomization health is reported. Whether
Flux *drives* the workload apply depends on having a Git source it can reach:

* with a reachable Git remote → Flux reconciles the tree;
* without one → the manifests are committed locally and applied through the
  API, and the engine label says exactly that:
  `GitOps working tree + Kubernetes server-side apply`.

It is never labelled "FluxCD" unless Flux did the work.

**openCenter.** Reported as `COMPAT`, never `REAL`. The same
`DeploymentBackend` interface a real openCenter would implement — point
`OPEN_CENTER_MODE=real` and `OPEN_CENTER_URL` at an installation (or at the
bundled `mock-opencenter` container) to use the HTTP path instead.

## GPUs in kind

kind nodes are containers; they do not get GPUs by default. The platform
detects this and says so:

```text
Kubernetes:  REAL
GPU:         UNAVAILABLE
Reason:      No schedulable 'nvidia.com/gpu' resource detected in the cluster.
```

If the host has an NVIDIA GPU, `nvidia-smi` detection reports it separately:

```text
HOST GPU              RTX 4090, 24 GB
Visible to host:      YES
Visible to kind:      NO
```

Both facts are shown, and the platform never treats one as the other. A GPU
request in MVP mode is therefore *denied with the real reason* rather than
silently faked — the Copilot offers to simulate it, use the host directly, or
configure cluster GPU access.

To make cluster GPUs real you need the NVIDIA Container Toolkit configured for
the kind node runtime plus the GPU operator or device plugin inside the cluster;
once `nvidia.com/gpu` appears on a node, the same workflow deploys a real GPU
pod with no code change.

## Storage

kind ships the local-path provisioner. The MVP creates a StorageClass **named**
`ceph-rbd` backed by local-path so generated manifests apply unchanged — and
Ceph still shows as `SIMULATED` in the reality matrix, because it is.

## Examples

**Internal web API** → `KUBERNETES_CPU`, 2 replicas, real Deployment + Service +
PVC + NetworkPolicy in namespace `ai-<name>`.

**PostgreSQL for development** → `DATABASE_SERVICE`, a real CloudNativePG
`Cluster` (if the operator was installed), which creates a real StatefulSet,
Services, PVCs and Secret.

**PyTorch GPU workload** → denied with the real reason, and the alternatives
spelled out.

## Commands

```bash
./startmvp.sh              create or reuse the cluster and deploy
./startmvp.sh --recreate   delete the cluster first
./startmvp.sh --no-flux    skip Flux
./stopmvp.sh               stop the app, keep the cluster
./stopmvp.sh --destroy     delete the cluster and its volumes
./resetmvp.sh              wipe data and generated workloads, keep the cluster
./resetmvp.sh --cluster    recreate everything
./status.sh                what is running, and what is real
./selftest-mvp.sh          16 end-to-end checks
```

Use kubectl directly:

```bash
export KUBECONFIG=deployments/mvp/kubeconfig
kubectl get nodes
kubectl -n aiinfra get pods
kubectl get ns | grep ^ai-        # namespaces the Governor created
```

## Next phases

See [migration-to-real-openstack.md](migration-to-real-openstack.md): Genestack,
then a real Nova CPU VM, then a real Nova GPU VM with VFIO passthrough. Do not
start those until the kind MVP is stable.
