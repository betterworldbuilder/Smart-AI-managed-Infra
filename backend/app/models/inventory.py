"""Unified resource model (sections 7 and 8).

Every adapter -- Kubernetes, OpenStack, Ceph -- normalises into these types, so
the Governor never needs to know which platform a resource came from.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .enums import GPUAllocationType, GPUStatus, Platform


class GPUDevice(BaseModel):
    """A single physical GPU, wherever it lives.

    Matches the JSON shape required by section 7.
    """

    id: str
    vendor: str
    model: str
    memory_gb: int
    host: str
    pci_address: str | None = None
    status: GPUStatus = GPUStatus.AVAILABLE
    allocation_type: GPUAllocationType = GPUAllocationType.PASSTHROUGH
    platform: Platform = Platform.KUBERNETES
    workload: str | None = None
    utilization_pct: float = 0.0
    memory_used_gb: float = 0.0
    temperature_c: float | None = None
    # Catalog-derived economics, used by the scoring engine.
    relative_cost: float = 1.0
    perf_tier: int = 1

    @property
    def available(self) -> bool:
        return self.status is GPUStatus.AVAILABLE


class ComputeNode(BaseModel):
    """A Kubernetes worker or an OpenStack compute host."""

    name: str
    platform: Platform
    cpu_total: int
    cpu_available: int
    ram_gb_total: int
    ram_gb_available: int
    disk_gb_total: int = 0
    disk_gb_available: int = 0
    gpu_ids: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    availability_zone: str | None = None
    status: str = "ready"
    iommu: str | None = None


class StoragePool(BaseModel):
    name: str
    backend: str
    total_tb: float
    available_tb: float
    consumer: str = "shared"


class Capacity(BaseModel):
    total: float = 0
    available: float = 0

    @property
    def used(self) -> float:
        return max(self.total - self.available, 0)

    @property
    def utilization_pct(self) -> float:
        return round(100.0 * self.used / self.total, 1) if self.total else 0.0


class GPUCapacity(Capacity):
    devices: list[GPUDevice] = Field(default_factory=list)

    def by_model(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for device in self.devices:
            entry = out.setdefault(device.model, {"total": 0, "available": 0})
            entry["total"] += 1
            if device.available:
                entry["available"] += 1
        return out


class StorageCapacity(BaseModel):
    total_tb: float = 0
    available_tb: float = 0
    pools: list[StoragePool] = Field(default_factory=list)

    @property
    def utilization_pct(self) -> float:
        if not self.total_tb:
            return 0.0
        return round(100.0 * (self.total_tb - self.available_tb) / self.total_tb, 1)


class PlatformInventory(BaseModel):
    """Per-platform detail hanging off the global inventory."""

    platform: Platform
    available: bool = True
    name: str | None = None
    version: str | None = None
    nodes: list[ComputeNode] = Field(default_factory=list)
    cpu: Capacity = Field(default_factory=Capacity)
    ram_gb: Capacity = Field(default_factory=Capacity)
    gpu: GPUCapacity = Field(default_factory=GPUCapacity)
    storage: StorageCapacity = Field(default_factory=StorageCapacity)
    details: dict = Field(default_factory=dict)


class GlobalInventory(BaseModel):
    """The merged view produced by `GlobalResourceInventoryService`."""

    cpu: Capacity = Field(default_factory=Capacity)
    ram_gb: Capacity = Field(default_factory=Capacity)
    gpu: GPUCapacity = Field(default_factory=GPUCapacity)
    storage: StorageCapacity = Field(default_factory=StorageCapacity)
    platforms: dict[str, PlatformInventory] = Field(default_factory=dict)
    simulated: bool = True
    generated_at: str | None = None

    # -- helpers used by the recommendation engine --------------------------

    def platform_of(self, platform: Platform) -> PlatformInventory:
        return self.platforms.get(
            str(platform), PlatformInventory(platform=platform, available=False)
        )

    def available_gpus(self, platform: Platform | None = None) -> list[GPUDevice]:
        return [
            d
            for d in self.gpu.devices
            if d.available and (platform is None or d.platform is platform)
        ]

    def gpu_model_counts(self, platform: Platform | None = None) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for device in self.gpu.devices:
            if platform is not None and device.platform is not platform:
                continue
            entry = out.setdefault(device.model, {"total": 0, "available": 0})
            entry["total"] += 1
            if device.available:
                entry["available"] += 1
        return out
