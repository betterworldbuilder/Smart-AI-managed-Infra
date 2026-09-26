"""Turn a vendor-neutral `DeploymentSpec` into platform configuration.

This is the "configuration generation" box in section 16. It produces files
only -- nothing here talks to a cluster. openCenter is what applies them, via
GitOps.

Three target shapes are supported today:

* Kubernetes workload  -> Namespace/Deployment/PVC/Service/NetworkPolicy/
                          ServiceMonitor + Flux Kustomization
* Managed database     -> CloudNativePG `Cluster` + ScheduledBackup
* OpenStack VM         -> OpenTofu module (flavor, volume, port, instance)
"""

from __future__ import annotations

import yaml

from ..models import DeploymentArtifact, PlanChange, RuntimeType


def _yaml(*docs: dict) -> str:
    return "---\n".join(yaml.safe_dump(doc, sort_keys=False, default_flow_style=False) for doc in docs)


def _labels(spec) -> dict[str, str]:
    return {
        "app.kubernetes.io/name": spec.metadata.name,
        "app.kubernetes.io/managed-by": "opencenter",
        "aiinfra.io/workload": spec.intent.workload,
        "aiinfra.io/environment": spec.intent.environment,
        "aiinfra.io/classification": spec.security.classification,
    }


def _namespace(spec) -> str:
    return f"ai-{spec.metadata.name}"


# ---------------------------------------------------------------------------
# Kubernetes
# ---------------------------------------------------------------------------


def _k8s_artifacts(spec, cluster: dict) -> list[DeploymentArtifact]:
    ns = _namespace(spec)
    labels = _labels(spec)
    name = spec.metadata.name
    gpu = spec.gpu
    base = f"clusters/{cluster.get('name', 'k8s-core')}/workloads/{name}"

    namespace_doc = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": ns,
            "labels": {**labels, "pod-security.kubernetes.io/enforce": "restricted"},
        },
    }

    container: dict = {
        "name": name,
        "image": spec.compute.image or "registry.lab.internal/aiinfra/placeholder:latest",
        "ports": [{"name": "http", "containerPort": 8080}],
        "resources": {
            "requests": {
                "cpu": str(spec.compute.cpu),
                "memory": spec.compute.memory,
            },
            "limits": {
                "cpu": str(spec.compute.cpu),
                "memory": spec.compute.memory,
            },
        },
        "volumeMounts": [{"name": "data", "mountPath": "/data"}],
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "runAsNonRoot": True,
            "capabilities": {"drop": ["ALL"]},
        },
    }
    pod_spec: dict = {
        "containers": [container],
        "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": f"{name}-data"}}],
    }
    if gpu is not None:
        # The device plugin advertises whole GPUs; the scheduler does placement.
        container["resources"]["limits"]["nvidia.com/gpu"] = gpu.count_per_replica
        container["resources"]["requests"].pop("cpu", None)
        container["resources"]["requests"]["cpu"] = str(spec.compute.cpu)
        pod_spec["nodeSelector"] = {"nvidia.com/gpu.product": f"NVIDIA-{gpu.model}"}
        pod_spec["tolerations"] = [
            {"key": "nvidia.com/gpu", "operator": "Exists", "effect": "NoSchedule"}
        ]
        container["env"] = [{"name": "NVIDIA_VISIBLE_DEVICES", "value": "all"}]
    if spec.availability.anti_affinity:
        pod_spec["affinity"] = {
            "podAntiAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": [
                    {
                        "labelSelector": {"matchLabels": {"app.kubernetes.io/name": name}},
                        "topologyKey": "kubernetes.io/hostname",
                    }
                ]
            }
        }

    deployment_doc = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": ns, "labels": labels},
        "spec": {
            "replicas": spec.compute.replicas,
            "selector": {"matchLabels": {"app.kubernetes.io/name": name}},
            "template": {"metadata": {"labels": labels}, "spec": pod_spec},
        },
    }

    pvc_doc = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": f"{name}-data", "namespace": ns, "labels": labels},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "storageClassName": spec.storage.backend,
            "resources": {"requests": {"storage": spec.storage.size}},
        },
    }

    service_doc = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": ns, "labels": labels},
        "spec": {
            "type": "LoadBalancer" if spec.network.exposure == "public" else "ClusterIP",
            "selector": {"app.kubernetes.io/name": name},
            "ports": [{"name": "http", "port": 80, "targetPort": "http"}],
        },
    }

    netpol_doc = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": f"{name}-default-deny", "namespace": ns, "labels": labels},
        "spec": {
            "podSelector": {"matchLabels": {"app.kubernetes.io/name": name}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [
                {
                    "from": [
                        {"namespaceSelector": {"matchLabels": {"aiinfra.io/trusted": "true"}}}
                    ]
                }
            ],
            "egress": [
                {"to": [{"namespaceSelector": {}}]}
                if spec.security.classification in ("public", "internal")
                else {
                    "to": [
                        {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": ns}}}
                    ]
                }
            ],
        },
    }

    monitor_doc = {
        "apiVersion": "monitoring.coreos.com/v1",
        "kind": "ServiceMonitor",
        "metadata": {"name": name, "namespace": ns, "labels": labels},
        "spec": {
            "selector": {"matchLabels": {"app.kubernetes.io/name": name}},
            "endpoints": [{"port": "http", "path": "/metrics", "interval": "30s"}],
        },
    }

    kustomization_doc = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "namespace": ns,
        "resources": [
            "namespace.yaml",
            "pvc.yaml",
            "deployment.yaml",
            "service.yaml",
            "networkpolicy.yaml",
            "servicemonitor.yaml",
        ],
    }

    flux_doc = {
        "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
        "kind": "Kustomization",
        "metadata": {"name": name, "namespace": "flux-system"},
        "spec": {
            "interval": "5m",
            "path": f"./{base}",
            "prune": True,
            "wait": True,
            "timeout": "10m",
            "sourceRef": {"kind": "GitRepository", "name": "infra-gitops"},
            "healthChecks": [
                {"apiVersion": "apps/v1", "kind": "Deployment", "name": name, "namespace": ns}
            ],
        },
    }

    return [
        DeploymentArtifact(
            path=f"{base}/namespace.yaml",
            content=_yaml(namespace_doc),
            description="Dedicated namespace with restricted pod security",
        ),
        DeploymentArtifact(
            path=f"{base}/pvc.yaml",
            content=_yaml(pvc_doc),
            description=f"{spec.storage.size} {spec.storage.backend} volume",
        ),
        DeploymentArtifact(
            path=f"{base}/deployment.yaml",
            content=_yaml(deployment_doc),
            description="Workload with GPU limits" if gpu else "Workload",
        ),
        DeploymentArtifact(
            path=f"{base}/service.yaml", content=_yaml(service_doc), description="Service"
        ),
        DeploymentArtifact(
            path=f"{base}/networkpolicy.yaml",
            content=_yaml(netpol_doc),
            description="Network isolation",
        ),
        DeploymentArtifact(
            path=f"{base}/servicemonitor.yaml",
            content=_yaml(monitor_doc),
            description="Prometheus scrape config",
        ),
        DeploymentArtifact(
            path=f"{base}/kustomization.yaml",
            content=_yaml(kustomization_doc),
            description="Kustomize entry point",
        ),
        DeploymentArtifact(
            path=f"clusters/{cluster.get('name', 'k8s-core')}/flux/{name}-kustomization.yaml",
            content=_yaml(flux_doc),
            description="FluxCD reconciliation object",
        ),
    ]


def _database_artifacts(spec, cluster: dict) -> list[DeploymentArtifact]:
    """CloudNativePG cluster -- the "Kubernetes PostgreSQL operator" path."""
    ns = _namespace(spec)
    name = spec.metadata.name
    labels = _labels(spec)
    base = f"clusters/{cluster.get('name', 'k8s-core')}/databases/{name}"

    namespace_doc = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": ns, "labels": labels},
    }
    cluster_doc = {
        "apiVersion": "postgresql.cnpg.io/v1",
        "kind": "Cluster",
        "metadata": {"name": name, "namespace": ns, "labels": labels},
        "spec": {
            "instances": spec.compute.replicas,
            "imageName": "ghcr.io/cloudnative-pg/postgresql:16.4",
            "primaryUpdateStrategy": "unsupervised",
            "storage": {"size": spec.storage.size, "storageClass": spec.storage.backend},
            "resources": {
                "requests": {"cpu": str(spec.compute.cpu), "memory": spec.compute.memory},
                "limits": {"cpu": str(spec.compute.cpu), "memory": spec.compute.memory},
            },
            "monitoring": {"enablePodMonitor": spec.observability.service_monitor},
            "affinity": {"enablePodAntiAffinity": spec.availability.anti_affinity},
            "backup": {
                "retentionPolicy": "30d",
                "barmanObjectStore": {
                    "destinationPath": f"s3://backups/{name}",
                    "s3Credentials": {
                        "accessKeyId": {"name": f"{name}-backup", "key": "ACCESS_KEY_ID"},
                        "secretAccessKey": {"name": f"{name}-backup", "key": "SECRET_ACCESS_KEY"},
                    },
                },
            },
        },
    }
    backup_doc = {
        "apiVersion": "postgresql.cnpg.io/v1",
        "kind": "ScheduledBackup",
        "metadata": {"name": f"{name}-nightly", "namespace": ns},
        "spec": {"schedule": "0 0 2 * * *", "cluster": {"name": name}},
    }
    netpol_doc = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": f"{name}-db-access", "namespace": ns},
        "spec": {
            "podSelector": {"matchLabels": {"cnpg.io/cluster": name}},
            "policyTypes": ["Ingress"],
            "ingress": [
                {
                    "from": [
                        {"namespaceSelector": {"matchLabels": {"aiinfra.io/trusted": "true"}}}
                    ],
                    "ports": [{"protocol": "TCP", "port": 5432}],
                }
            ],
        },
    }
    kustomization_doc = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "namespace": ns,
        "resources": ["namespace.yaml", "cluster.yaml", "backup.yaml", "networkpolicy.yaml"],
    }
    return [
        DeploymentArtifact(path=f"{base}/namespace.yaml", content=_yaml(namespace_doc)),
        DeploymentArtifact(
            path=f"{base}/cluster.yaml",
            content=_yaml(cluster_doc),
            description=f"CloudNativePG cluster, {spec.compute.replicas} instances",
        ),
        DeploymentArtifact(
            path=f"{base}/backup.yaml", content=_yaml(backup_doc), description="Nightly backup"
        ),
        DeploymentArtifact(path=f"{base}/networkpolicy.yaml", content=_yaml(netpol_doc)),
        DeploymentArtifact(path=f"{base}/kustomization.yaml", content=_yaml(kustomization_doc)),
    ]


# ---------------------------------------------------------------------------
# OpenStack (OpenTofu)
# ---------------------------------------------------------------------------


def _openstack_artifacts(spec, cluster: dict) -> list[DeploymentArtifact]:
    name = spec.metadata.name
    gpu = spec.gpu
    base = f"clusters/{cluster.get('name', 'genestack')}/tofu/{name}"
    memory_mb = _memory_to_mb(spec.compute.memory)
    disk_gb = _storage_to_gb(spec.storage.size)
    network = spec.network.network or (
        "public-net" if spec.network.exposure == "public" else "internal-net"
    )

    flavor_ref = "data.openstack_compute_flavor_v2.selected.id"
    if gpu is not None:
        # A GPU flavor carries the PCI alias Nova matches against pci.device_spec.
        flavor_block = f'''
# GPU flavor -- the alias is resolved by Nova against `pci.device_spec` on the
# compute host and tracked in Placement. The alias name is configuration, never
# a hard-coded PCI id.
resource "openstack_compute_flavor_v2" "gpu" {{
  name      = "{name}-gpu"
  ram       = {memory_mb}
  vcpus     = {spec.compute.cpu}
  disk      = {disk_gb}
  is_public = false

  extra_specs = {{
    "pci_passthrough:alias" = "{gpu.pci_alias or 'nvidia_gpu'}:{gpu.count_per_replica}"
    "hw:cpu_policy"         = "dedicated"
    "hw:mem_page_size"      = "large"
  }}
}}
'''
        flavor_ref = "openstack_compute_flavor_v2.gpu.id"
    else:
        flavor_block = f'''
data "openstack_compute_flavor_v2" "selected" {{
  name = "{spec.compute.flavor or 'm1.medium'}"
}}
'''

    main_tf = f'''terraform {{
  required_version = ">= 1.6"
  required_providers {{
    openstack = {{
      source  = "terraform-provider-openstack/openstack"
      version = "~> 2.1"
    }}
  }}
}}

# Credentials come from the openCenter runner's environment (clouds.yaml /
# OS_* variables). They are never rendered into this file and never reach the
# LLM.
provider "openstack" {{
  cloud = var.os_cloud
}}

variable "os_cloud" {{
  type    = string
  default = "genestack"
}}

variable "key_pair" {{
  type    = string
  default = "platform-admin"
}}
{flavor_block}
data "openstack_images_image_v2" "base" {{
  name        = "{spec.compute.image or 'ubuntu-22.04-server'}"
  most_recent = true
}}

data "openstack_networking_network_v2" "net" {{
  name = "{network}"
}}

resource "openstack_networking_secgroup_v2" "sg" {{
  name        = "{name}-sg"
  description = "Managed by openCenter for workload {name}"
}}

resource "openstack_networking_secgroup_rule_v2" "ssh_internal" {{
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 22
  port_range_max    = 22
  remote_ip_prefix  = "10.0.0.0/8"
  security_group_id = openstack_networking_secgroup_v2.sg.id
}}

resource "openstack_blockstorage_volume_v3" "data" {{
  name        = "{name}-data"
  size        = {disk_gb}
  volume_type = "{spec.storage.backend}"
}}

resource "openstack_compute_instance_v2" "vm" {{
  count           = {spec.compute.replicas}
  name            = "{name}-${{count.index + 1}}"
  image_id        = data.openstack_images_image_v2.base.id
  flavor_id       = {flavor_ref}
  key_pair        = var.key_pair
  security_groups = [openstack_networking_secgroup_v2.sg.name]

  metadata = {{
    workload       = "{spec.intent.workload}"
    environment    = "{spec.intent.environment}"
    classification = "{spec.security.classification}"
    managed_by     = "opencenter"
  }}

  network {{
    uuid = data.openstack_networking_network_v2.net.id
  }}

  # Nova's scheduler picks the physical host. We only state the requirement.
  lifecycle {{
    ignore_changes = [image_id]
  }}
}}

resource "openstack_compute_volume_attach_v2" "data" {{
  count       = {spec.compute.replicas}
  instance_id = openstack_compute_instance_v2.vm[count.index].id
  volume_id   = openstack_blockstorage_volume_v3.data.id
}}

output "instance_ids" {{
  value = openstack_compute_instance_v2.vm[*].id
}}
'''

    cloud_init = f'''#cloud-config
hostname: {name}
package_update: true
packages:
  - qemu-guest-agent
  - prometheus-node-exporter
{"  - nvidia-driver-550" if gpu else ""}
runcmd:
  - systemctl enable --now qemu-guest-agent
  - systemctl enable --now prometheus-node-exporter
{"  - nvidia-smi" if gpu else ""}
'''

    artifacts = [
        DeploymentArtifact(
            path=f"{base}/main.tf",
            content=main_tf,
            language="hcl",
            description="OpenTofu module applied by the openCenter runner",
        ),
        DeploymentArtifact(
            path=f"{base}/cloud-init.yaml",
            content=cloud_init,
            description="Guest bootstrap" + (" incl. NVIDIA driver" if gpu else ""),
        ),
    ]
    if gpu is not None:
        artifacts.append(
            DeploymentArtifact(
                path=f"{base}/nova-pci-reference.md",
                language="markdown",
                description="Host-side prerequisites for PCI passthrough",
                content=f"""# Host prerequisites for `{name}`

This workload needs {gpu.count_per_replica} x {gpu.model} via VFIO passthrough.
The compute host must already be prepared (see `docs/gpu-passthrough.md`):

```ini
# /etc/nova/nova.conf on the GPU compute host
[pci]
device_spec = {{"vendor_id": "<discovered>", "device_id": "<discovered>", "address": "*:*:*.*"}}
alias = {{"name": "{gpu.pci_alias or 'nvidia_gpu'}", "device_type": "type-PCI", "vendor_id": "<discovered>", "device_id": "<discovered>"}}
```

Vendor and device ids are discovered from `lspci -nn` / the GPU catalog -- this
platform never assumes a product id.
""",
            )
        )
    return artifacts


def _memory_to_mb(memory: str) -> int:
    text = memory.strip()
    if text.endswith("Gi"):
        return int(float(text[:-2]) * 1024)
    if text.endswith("Mi"):
        return int(float(text[:-2]))
    return int(float(text))


def _storage_to_gb(size: str) -> int:
    text = size.strip()
    if text.endswith("Gi"):
        return int(float(text[:-2]))
    if text.endswith("Ti"):
        return int(float(text[:-2]) * 1024)
    return int(float(text))


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def generate_artifacts(spec, cluster: dict, runtime: RuntimeType) -> list[DeploymentArtifact]:
    if runtime is RuntimeType.DATABASE_SERVICE:
        return _database_artifacts(spec, cluster)
    if runtime.platform.value == "openstack":
        return _openstack_artifacts(spec, cluster)
    return _k8s_artifacts(spec, cluster)


def plan_changes(spec, runtime: RuntimeType) -> list[PlanChange]:
    """The "WHAT WILL BE CREATED" list shown before any execution."""
    ns = _namespace(spec)
    name = spec.metadata.name
    changes: list[PlanChange] = []

    if runtime is RuntimeType.DATABASE_SERVICE:
        changes += [
            PlanChange(kind="namespace", name=ns),
            PlanChange(
                kind="postgresql.cnpg.io/Cluster",
                name=name,
                detail=f"{spec.compute.replicas} instances, {spec.compute.cpu} vCPU / {spec.compute.memory} each",
            ),
            PlanChange(
                kind="PersistentVolumeClaim",
                name=f"{name}-data",
                detail=f"{spec.storage.size} {spec.storage.backend} per instance",
            ),
            PlanChange(kind="Service", name=f"{name}-rw", detail="primary endpoint"),
            PlanChange(kind="Service", name=f"{name}-ro", detail="replica endpoint"),
            PlanChange(kind="ScheduledBackup", name=f"{name}-nightly", detail="02:00 daily"),
            PlanChange(kind="NetworkPolicy", name=f"{name}-db-access"),
        ]
        if spec.observability.service_monitor:
            changes.append(PlanChange(kind="PodMonitor", name=name))
        return changes

    if runtime.platform.value == "openstack":
        changes += [
            PlanChange(kind="SecurityGroup", name=f"{name}-sg"),
            PlanChange(
                kind="Volume",
                name=f"{name}-data",
                detail=f"{spec.storage.size} {spec.storage.backend}",
            ),
        ]
        if spec.gpu is not None:
            changes.append(
                PlanChange(
                    kind="Flavor",
                    name=f"{name}-gpu",
                    detail=f"{spec.compute.cpu} vCPU / {spec.compute.memory} / "
                    f"pci_passthrough:alias={spec.gpu.pci_alias or 'nvidia_gpu'}:{spec.gpu.count_per_replica}",
                )
            )
        changes.append(
            PlanChange(
                kind="Instance",
                name=name,
                detail=f"{spec.compute.replicas} x {spec.compute.image or 'ubuntu-22.04-server'}",
            )
        )
        if spec.gpu is not None:
            changes.append(
                PlanChange(
                    kind="GPU allocation",
                    name=f"{spec.gpu.count_per_replica * spec.compute.replicas} x {spec.gpu.model}",
                    detail=f"VFIO {spec.gpu.allocation_type}",
                )
            )
        changes.append(PlanChange(kind="Port", name=f"{name}-port", detail=spec.network.exposure))
        return changes

    changes += [PlanChange(kind="namespace", name=ns)]
    if spec.gpu is not None:
        changes.append(
            PlanChange(
                kind="GPU workloads",
                name=f"{spec.compute.replicas} x {name}",
                detail=f"{spec.compute.cpu} vCPU / {spec.compute.memory} each",
            )
        )
        changes.append(
            PlanChange(
                kind="GPU allocations",
                name=f"{spec.gpu.count_per_replica * spec.compute.replicas} x {spec.gpu.model}",
                detail="nvidia.com/gpu",
            )
        )
    else:
        changes.append(
            PlanChange(
                kind="workloads",
                name=f"{spec.compute.replicas} x {name}",
                detail=f"{spec.compute.cpu} vCPU / {spec.compute.memory} each",
            )
        )
    changes += [
        PlanChange(
            kind="PersistentVolumeClaim",
            name=f"{name}-data",
            detail=f"{spec.storage.size} {spec.storage.backend}",
        ),
        PlanChange(
            kind="Service",
            name=name,
            detail="LoadBalancer" if spec.network.exposure == "public" else "ClusterIP",
        ),
    ]
    if spec.network.network_policy:
        changes.append(PlanChange(kind="NetworkPolicy", name=f"{name}-default-deny"))
    if spec.observability.service_monitor:
        changes.append(PlanChange(kind="ServiceMonitor", name=name, detail="Prometheus"))
    return changes
