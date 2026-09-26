"""Infrastructure adapters.

Every external system the platform touches sits behind one of these. Each has a
simulated implementation (POC mode) and a real implementation (MVP / production
mode); the factory in `app.container` decides which to instantiate based on
`INFRA_MODE`.
"""

from .base import AdapterError, AdapterNotConfigured, InfrastructureAdapter
from .ceph import CephAdapter, build_ceph_adapter
from .deployment_backend import DeploymentBackend, RunStep
from .flux import FluxAdapter, KubernetesDeploymentBackend
from .genestack import GenestackAdapter
from .gpu_host import HostGPUDetector
from .kubernetes import KubernetesInventoryAdapter, build_kubernetes_adapter
from .opencenter import (
    MockOpenCenterAdapter,
    OpenCenterAdapter,
    RealOpenCenterAdapter,
    build_opencenter_adapter,
)
from .openstack import OpenStackInventoryAdapter, build_openstack_adapter
from .simulation import GPUModelCatalog, SimulatedInfrastructure

__all__ = [
    "AdapterError",
    "AdapterNotConfigured",
    "CephAdapter",
    "DeploymentBackend",
    "FluxAdapter",
    "GPUModelCatalog",
    "GenestackAdapter",
    "HostGPUDetector",
    "InfrastructureAdapter",
    "KubernetesDeploymentBackend",
    "KubernetesInventoryAdapter",
    "MockOpenCenterAdapter",
    "OpenCenterAdapter",
    "OpenStackInventoryAdapter",
    "RealOpenCenterAdapter",
    "RunStep",
    "SimulatedInfrastructure",
    "build_ceph_adapter",
    "build_kubernetes_adapter",
    "build_opencenter_adapter",
    "build_openstack_adapter",
]
