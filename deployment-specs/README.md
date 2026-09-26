# DeploymentSpec

`aiinfra/v1alpha1 WorkloadDeployment` is the contract between the AI Governor
and openCenter. It is deliberately **vendor neutral**: no kubectl, no nova, no
Helm, no shell. Turning it into platform artefacts is the adapter's job, which
is why the same spec can become Kubernetes manifests, a CloudNativePG cluster
or an OpenTofu module.

A spec is generated **after the first human approval** and shown in full
**before the second**. Fetch one from a running deployment:

```bash
curl -s localhost:8000/api/deployments/<id>/spec.yaml -H "Authorization: Bearer $TOKEN"
```

## Structure

| Section | Holds |
|---|---|
| `metadata` | name, labels, annotations (score, scheduler, generator) |
| `intent` | workload class and environment — the *why* |
| `runtime` | `type` (kubernetes / openstack), `accelerator`, `mode` (container / vm / managed-database) |
| `compute` | replicas, cpu, memory, flavor, image |
| `gpu` | vendor, model, count per replica, allocation type, VRAM, PCI alias |
| `storage` | type, backend, size |
| `network` | exposure, network, NetworkPolicy |
| `security` | classification, encryption at rest, isolation |
| `availability` | HA, anti-affinity, minimum replicas |
| `observability` | ServiceMonitor, GPU metrics, dashboards |

Policy **obligations** are applied here, so a spec cannot silently drop a
control the policy engine demanded — restricted data forces
`encryptionAtRest: true` and `exposure: internal` whatever the operator asked
for.

## Examples

| File | Path |
|---|---|
| Kubernetes GPU inference (the §32 acceptance demo) | [examples/llama-internal.yaml](examples/llama-internal.yaml) |
| OpenStack GPU VM with PCI passthrough | [examples/pytorch-dev-gpu-vm.yaml](examples/pytorch-dev-gpu-vm.yaml) |
| Highly available managed PostgreSQL | [examples/postgres-ha.yaml](examples/postgres-ha.yaml) |

## What is generated from it

| Runtime | Artefacts |
|---|---|
| `kubernetes` + container | Namespace, PVC, Deployment, Service, NetworkPolicy, ServiceMonitor, Kustomization, Flux Kustomization |
| `kubernetes` + managed-database | Namespace, CNPG `Cluster`, `ScheduledBackup`, NetworkPolicy, Kustomization |
| `openstack` + vm | OpenTofu module (flavor with `pci_passthrough:alias`, volume, security group, instance, port), cloud-init, host prerequisites note |
