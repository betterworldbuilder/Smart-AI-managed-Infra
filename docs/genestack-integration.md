# Genestack integration

Genestack runs OpenStack on Kubernetes. This platform **does not reimplement
any part of it** — `GenestackAdapter` is a read-only lens that answers "is the
Genestack-managed control plane healthy, and what does it offer?" by combining
the Kubernetes view (where the control-plane pods live) with the OpenStack view
(what the cloud exposes).

```text
Kubernetes foundation
        ↓
    Genestack
        ↓
    OpenStack
  ┌─────┴─────┐
CPU VM     GPU VM
```

## Services the platform expects

Required: `keystone`, `nova`, `placement`, `neutron`, `ovn`, `glance`,
`cinder`, `horizon`, `mariadb`, `rabbitmq`.
Optional: `manila`, `swift`.

`GET /api/inventory/genestack` reports which are present, which are down, and
whether the control plane is ready. The Infrastructure page renders it.

## Modes

```env
GENESTACK_MODE=disabled      # not part of this deployment
GENESTACK_MODE=mock          # default: simulated control plane
GENESTACK_MODE=experimental  # a real but unsupported installation
GENESTACK_MODE=real          # a supported installation
```

The kind MVP deliberately defaults to `mock`. Running the full OpenStack control
plane inside kind needs nested virtualisation, privileged workloads, network and
storage configuration, a lot of RAM and KVM device access — none of which should
be a prerequisite for the first MVP.

## What the adapter reads

| Question | Source |
|---|---|
| Is OpenStack available? | `nova`/`keystone` service list |
| Nova compute nodes | hypervisor list → vCPU/RAM/disk totals and usage |
| Nova services | `binary`, `host`, `state` |
| Placement resources | resource providers + inventories + usages |
| GPU resource classes | custom resource classes on provider inventories |
| Flavors | including `extra_specs` such as `pci_passthrough:alias` |
| Images | Glance, with a `gpu_ready` hint |
| Networks | Neutron, external flag |
| Storage | Cinder pools / Ceph capacity |

GPU models are **discovered**, never assumed: in real mode they come from the
Placement custom resource classes and the provider traits; in simulation from
`simulation/inventory.yaml`.

## Connecting a real Genestack

1. Install Genestack. This platform expects nothing special beyond the standard
   services above.
2. Point the adapters at it:

   ```env
   OPENSTACK_MODE=real
   OS_AUTH_URL=https://keystone.example.com/v3
   OS_USERNAME=aiinfra
   OS_PROJECT_NAME=aiinfra
   OS_REGION_NAME=RegionOne
   # OS_PASSWORD comes from the environment or a Docker secret
   GENESTACK_MODE=real
   ```

3. Build the backend image with the OpenStack client:

   ```bash
   docker compose build --build-arg INSTALL_OPENSTACK=true backend
   ```

   (`openstacksdk` is not in the default image because it needs a C toolchain.)

4. Restart and check the Integrations page — OpenStack should move from
   `SIMULATED` to `CONNECTED`, and the Infrastructure page should list your real
   compute hosts.

## Permissions

A read-only OpenStack user is enough for inventory. Creating VMs is done by
openCenter with its own credentials, through the OpenTofu module the platform
generates — the Copilot never holds cloud credentials.

## GPU flavors

The platform generates a flavor per GPU workload with the PCI alias in
`extra_specs`, rather than depending on one existing:

```hcl
resource "openstack_compute_flavor_v2" "gpu" {
  name  = "llama-internal-gpu"
  ram   = 65536
  vcpus = 8
  disk  = 500
  extra_specs = {
    "pci_passthrough:alias" = "nvidia_h100:1"
    "hw:cpu_policy"         = "dedicated"
    "hw:mem_page_size"      = "large"
  }
}
```

A sample `gpu.small` flavor (8 vCPU, 32 GB, 100 GB disk, 1 GPU) is defined in
the simulated inventory as a starting point.

The alias name is configuration; the PCI vendor and device ids are discovered
from the host — see [gpu-passthrough.md](gpu-passthrough.md).
