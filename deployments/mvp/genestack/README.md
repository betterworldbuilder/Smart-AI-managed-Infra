# Genestack in the MVP

**Not required for the kind MVP.** `GENESTACK_MODE=mock` is the default and the
UI reports OpenStack, Nova and Genestack as simulated.

Running the full OpenStack control plane inside kind needs nested
virtualisation, privileged workloads, careful network and storage
configuration, a lot of RAM and KVM device access. None of that should be a
prerequisite for proving the Copilot → Governor → approval → deployment
workflow, so it is a later phase.

## Modes

```env
GENESTACK_MODE=disabled      # not part of this deployment
GENESTACK_MODE=mock          # default -- simulated control plane
GENESTACK_MODE=experimental  # a real but unsupported installation
GENESTACK_MODE=real          # a supported installation
```

## When you are ready

Follow [../../../docs/migration-to-real-openstack.md](../../../docs/migration-to-real-openstack.md).
In short:

1. Stand up Genestack on a cluster that can actually host it (not kind).
2. Point the platform at it:

   ```env
   OPENSTACK_MODE=real
   GENESTACK_MODE=real
   OS_AUTH_URL=...
   ```

3. Rebuild the backend image with the OpenStack client:

   ```bash
   docker compose build --build-arg INSTALL_OPENSTACK=true backend
   ```

4. Nothing else changes. The Copilot, Governor, policy engine, approval flow,
   DeploymentSpec and UI are identical — only the adapter behind
   `OpenStackInventoryAdapter` differs.

## `overrides/`

Helm value overrides for the Genestack services this platform cares about.
They are **examples**, not a supported deployment:

| File | What it configures |
|---|---|
| `nova-gpu-passthrough.yaml` | `[pci] device_spec` / `alias`, `PciPassthroughFilter` |
| `placement-gpu.yaml` | the custom GPU resource class the inventory adapter reads |

See [../../../docs/gpu-passthrough.md](../../../docs/gpu-passthrough.md) for
the host-side prerequisites (IOMMU, vfio-pci) that must be in place first.
