# GPU passthrough (VFIO → Nova → Placement → guest)

The first real GPU VM implementation targets **full PCI passthrough**, because
Nova's documented path is unambiguous:

```text
IOMMU  →  vfio-pci  →  nova.conf [pci] device_spec  →  Placement  →  flavor alias  →  guest GPU
```

vGPU, MIG, SR-IOV and AMD are accommodated by the data model
(`GPUAllocationType`) but are **not** implemented in this POC.

> Nothing in this platform assumes an NVIDIA product id. The vendor and device
> ids are discovered from the host with `lspci -nn`; the *alias name* is
> configuration. The GPU catalog in `simulation/inventory.yaml` carries example
> ids for the simulation only.

## 1. Host: enable IOMMU

Intel:

```bash
# /etc/default/grub
GRUB_CMDLINE_LINUX_DEFAULT="intel_iommu=on iommu=pt"
```

AMD:

```bash
GRUB_CMDLINE_LINUX_DEFAULT="amd_iommu=on iommu=pt"
```

```bash
update-grub && reboot
dmesg | grep -e DMAR -e IOMMU        # confirm it is on
```

## 2. Host: bind the GPU to vfio-pci

```bash
lspci -nn | grep -i nvidia
# 65:00.0 3D controller [0302]: NVIDIA Corporation AD102GL [L40S] [10de:26b9]
```

`10de` is the vendor id, `26b9` the device id — **discover them, do not copy
them**.

```bash
# /etc/modprobe.d/vfio.conf
options vfio-pci ids=10de:26b9
softdep nvidia pre: vfio-pci

# /etc/modules-load.d/vfio.conf
vfio
vfio_iommu_type1
vfio_pci
```

```bash
update-initramfs -u && reboot
lspci -nnk -s 65:00.0 | grep -i 'kernel driver'   # -> vfio-pci
```

The host must **not** load the NVIDIA driver for a card it is passing through.

## 3. Nova: declare the device

```ini
# /etc/nova/nova.conf on the GPU compute host
[pci]
device_spec = {"vendor_id": "10de", "device_id": "26b9", "address": "*:*:*.*"}
alias = {"name": "nvidia_l40s", "device_type": "type-PCI", "vendor_id": "10de", "device_id": "26b9"}
```

On the controller (scheduler):

```ini
[filter_scheduler]
enabled_filters = ...,PciPassthroughFilter
available_filters = nova.scheduler.filters.all_filters
```

Restart `nova-compute` on the host and `nova-scheduler` on the controller.

In Genestack these values live in the Nova Helm overrides — see
`deployments/mvp/genestack/overrides/`.

## 4. Placement: confirm the inventory

```bash
openstack resource provider list
openstack resource provider inventory list <compute-gpu-01-uuid>
```

A PCI device pool appears as a custom resource class (conventionally
`CUSTOM_PGPU`; the adapter discovers whatever is there). The platform reads
this to know how many GPUs are free — it never counts cards itself.

## 5. Flavor: ask for the device

```bash
openstack flavor create gpu.small \
  --vcpus 8 --ram 32768 --disk 100 \
  --property "pci_passthrough:alias"="nvidia_l40s:1" \
  --property "hw:cpu_policy"="dedicated" \
  --property "hw:mem_page_size"="large"
```

The platform generates exactly this, as OpenTofu, from the approved
`DeploymentSpec`:

```hcl
resource "openstack_compute_flavor_v2" "gpu" {
  name  = "pytorch-dev-gpu"
  ram   = 32768
  vcpus = 8
  disk  = 100
  extra_specs = {
    "pci_passthrough:alias" = "nvidia_l40s:1"
    "hw:cpu_policy"         = "dedicated"
    "hw:mem_page_size"      = "large"
  }
}
```

`hw:cpu_policy=dedicated` and huge pages matter for GPU workloads: without CPU
pinning the guest's PCIe throughput and latency are unpredictable.

## 6. Boot and verify

```bash
openstack server create --flavor gpu.small --image ubuntu-22.04-cuda12 \
  --network internal-net pytorch-dev-1
```

Inside the guest:

```bash
lspci -nn | grep -i nvidia     # the card is present
nvidia-smi                     # the driver sees it
```

`nvidia-smi` inside the VM is the acceptance test for Phase 6.

## 7. What the platform checks before it lets you try

`validate_deployment()` fails or warns on:

* no host with enough free cards of the requested model,
* a GPU model whose VRAM is below the workload's requirement (hard deny),
* a compute host that does not report `iommu: enabled` (warning),
* storage or CPU/RAM that does not fit.

And the generated OpenTofu module ships a `nova-pci-reference.md` next to it
restating the host prerequisites, so the person applying it has them in hand.

## Guest driver

The generated cloud-init installs the NVIDIA driver for GPU VMs. Pair it with a
CUDA-ready image (`ubuntu-22.04-cuda12` in the simulated Glance catalog) to skip
the compile step.

## Later: vGPU, MIG, SR-IOV

The data model already distinguishes them (`GPUAllocationType`:
`passthrough`, `timeslice`, `vgpu`, `mig`, `sriov`) and the catalog records
which types each model supports. Implementing them means:

* **vGPU** — `[devices] enabled_vgpu_types` in nova.conf, `resources:VGPU=1` in
  the flavor, and a licensed vGPU driver on the host;
* **MIG** — partition on the host, expose profiles as distinct resource classes;
* **Kubernetes time-slicing** — device plugin config, already the default
  `allocation_type` for Kubernetes GPU workloads in this platform.

None of these change the Copilot, the Governor or the approval flow.
