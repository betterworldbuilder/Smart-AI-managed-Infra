"""Deterministic natural-language -> `IntentPatch` extraction.

This runs on *every* message, with or without an LLM. It is what makes the POC
demonstrable offline, and it also acts as a floor under the model: an LLM can
add detail it missed, but the rules below always apply.

It deliberately extracts *requirements*, never infrastructure decisions.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..models import (
    Environment,
    IntentPatch,
    IsolationLevel,
    NetworkExposure,
    OptimizationGoal,
    Sensitivity,
)
from ..governor.sizing import WorkloadProfiles

# --- small helpers ---------------------------------------------------------

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "eight": 8,
    "ten": 10,
    "a dozen": 12,
}

CONCURRENT_RE = re.compile(
    r"(\d+)\s*(?:concurrent|simultaneous|parallel|at\s+(?:the\s+)?same\s+time|at\s+peak)",
    re.I,
)
CONCURRENT_RE_ALT = re.compile(
    r"concurren(?:t|cy)[^.\d]{0,20}(\d+)|(\d+)\s*(?:users?|requests?|sessions?)\s*(?:at\s+peak|concurrently)",
    re.I,
)
USERS_RE = re.compile(
    r"(\d+)\s*(?:internal\s+|external\s+)?"
    r"(?:employees|users|people|staff|developers|engineers|seats|clients)",
    re.I,
)
GPU_COUNT_RE = re.compile(r"(\d+)\s*(?:x\s*)?(?:gpus?|graphics cards?|accelerators?)", re.I)
RAM_RE = re.compile(r"(\d+)\s*(?:gb|gib)\s*(?:of\s*)?(?:ram|memory)", re.I)
VCPU_RE = re.compile(r"(\d+)\s*(?:v?cpus?|cores?|threads?)", re.I)
# Storage needs an explicit noun: a bare "64 GB" is almost always RAM.
STORAGE_GB_RE = re.compile(
    r"(\d+)\s*(gb|gib|tb|tib)\s*(?:of\s+)?(?:storage|disk|data|volume|ssd|nvme|dataset)", re.I
)
STORAGE_GB_RE_ALT = re.compile(
    r"(?:storage|disk|dataset|database size)\D{0,12}?(\d+)\s*(gb|gib|tb|tib)", re.I
)
REPLICAS_RE = re.compile(r"(\d+)\s*(?:replicas?|instances?|copies|nodes|vms?)", re.I)
GPU_MEM_RE = re.compile(r"(\d+)\s*(?:gb|gib)\s*(?:of\s*)?(?:vram|gpu memory|video memory)", re.I)

OS_PATTERNS = (
    (re.compile(r"ubuntu\s*(\d\d\.\d\d)?", re.I), "ubuntu"),
    (re.compile(r"(rhel|red hat)\s*(\d)?", re.I), "rhel"),
    (re.compile(r"rocky\s*(\d)?", re.I), "rocky"),
    (re.compile(r"debian\s*(\d\d?)?", re.I), "debian"),
    (re.compile(r"windows(\s*server)?\s*(\d{4})?", re.I), "windows"),
)

NO_CONTAINER_PHRASES = (
    "cannot be containerised",
    "cannot be containerized",
    "can not be containerised",
    "can't be containerized",
    "not container",
    "needs a real operating system",
    "needs a real os",
    "full operating system",
    "kernel module",
    "systemd",
    "legacy",
    "root access",
    "install drivers",
    "virtual machine",
    "bare vm",
)
CONTAINER_PHRASES = (
    "container",
    "docker image",
    "helm chart",
    "kubernetes",
    "stateless",
    "microservice",
)
HIGH_ISOLATION_PHRASES = (
    "isolated",
    "isolation",
    "single tenant",
    "single-tenant",
    "dedicated",
    "air gap",
    "air-gapped",
    "root access",
    "own machine",
    "separate hardware",
)
HA_PHRASES = (
    "highly available",
    "high availability",
    "ha ",
    " ha",
    "no downtime",
    "fault tolerant",
    "failover",
    "zero downtime",
    "resilient",
)
PUBLIC_PHRASES = (
    "internet facing",
    "internet-facing",
    "publicly accessible",
    "public internet",
    "external customers",
    "exposed to the internet",
    "public endpoint",
)
SENSITIVITY_PHRASES: tuple[tuple[tuple[str, ...], Sensitivity], ...] = (
    (("restricted", "classified", "top secret", "regulated", "pci-dss", "hipaa"), Sensitivity.RESTRICTED),
    (("confidential", "sensitive", "company data", "customer data", "personal data", "gdpr", "pii"), Sensitivity.CONFIDENTIAL),
    (("internal only", "internal", "private", "staff only"), Sensitivity.INTERNAL),
    (("public data", "open data", "public"), Sensitivity.PUBLIC),
)
ENVIRONMENT_PHRASES: tuple[tuple[tuple[str, ...], Environment], ...] = (
    (("production", "prod ", " prod", "live"), Environment.PROD),
    (("staging", "test", "qa", "uat"), Environment.TEST),
    (("development", "dev ", " dev", "sandbox", "experiment", "poc", "prototype"), Environment.DEV),
)
COST_PHRASES = ("cheap", "cost", "budget", "inexpensive", "save money", "lowest cost")
PERF_PHRASES = ("fastest", "performance", "as fast as possible", "maximum throughput", "high throughput")
LATENCY_PHRASES = ("low latency", "real time", "real-time", "interactive", "responsive")

MODEL_NAME_HINTS = ("llama", "mistral", "mixtral", "falcon", "qwen", "phi", "gemma", "deepseek")


def _first_int(match: re.Match | None, group: int = 1) -> int | None:
    if not match:
        return None
    try:
        value = match.group(group)
        return int(value) if value else None
    except (IndexError, ValueError):
        return None


def _contains(text: str, phrases: Iterable[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def extract_intent(
    text: str,
    profiles: WorkloadProfiles,
    known_gpu_models: Iterable[str] = (),
) -> IntentPatch:
    """Best-effort structured reading of one natural-language request."""
    original = text or ""
    lowered = f" {original.lower()} "
    patch = IntentPatch(description=original.strip() or None)

    # --- workload class --------------------------------------------------
    profile = profiles.match_keywords(lowered)
    if profile is not None:
        patch.workload_type = profile.key
        patch.gpu_required = profile.gpu_required
        patch.container_compatible = profile.container_compatible
        patch.workload_isolation = IsolationLevel(profile.default_isolation)

    # --- user counts -----------------------------------------------------
    working = lowered
    concurrent = _first_int(CONCURRENT_RE.search(working))
    if concurrent is None:
        alt = CONCURRENT_RE_ALT.search(working)
        if alt:
            concurrent = int(next(group for group in alt.groups() if group))
    if concurrent is not None:
        patch.concurrent_users = concurrent
        working = CONCURRENT_RE.sub(" ", working)
        working = CONCURRENT_RE_ALT.sub(" ", working)

    users = _first_int(USERS_RE.search(working))
    if users is not None:
        patch.expected_users = users

    # --- explicit sizing --------------------------------------------------
    gpu_count = _first_int(GPU_COUNT_RE.search(lowered))
    if gpu_count is not None:
        patch.gpu_count = gpu_count
        patch.gpu_required = gpu_count > 0

    gpu_mem = _first_int(GPU_MEM_RE.search(lowered))
    if gpu_mem is not None:
        patch.gpu_memory_gb = gpu_mem
        patch.gpu_required = True

    ram = _first_int(RAM_RE.search(lowered))
    if ram is not None:
        patch.estimated_ram_gb = ram

    vcpu = _first_int(VCPU_RE.search(lowered))
    if vcpu is not None:
        patch.estimated_vcpu = vcpu

    storage_match = STORAGE_GB_RE.search(lowered) or STORAGE_GB_RE_ALT.search(lowered)
    if storage_match:
        amount = int(storage_match.group(1))
        unit = storage_match.group(2).lower()
        patch.storage_gb = amount * 1024 if unit.startswith("t") else amount

    replicas = _first_int(REPLICAS_RE.search(lowered))
    if replicas is not None and replicas <= 64:
        patch.replicas = replicas

    # --- GPU model preference --------------------------------------------
    for model in known_gpu_models:
        if re.search(rf"\b{re.escape(model.lower())}\b", lowered):
            patch.preferred_gpu = model
            patch.gpu_required = True
            break

    # --- operating system -------------------------------------------------
    for pattern, family in OS_PATTERNS:
        match = pattern.search(original)
        if match:
            version = next((g for g in match.groups() if g and g.strip()), None)
            patch.operating_system = f"{family} {version}".strip() if version else family
            if family == "windows":
                patch.container_compatible = False
            break

    # --- qualitative signals ---------------------------------------------
    if _contains(lowered, NO_CONTAINER_PHRASES):
        patch.container_compatible = False
    elif _contains(lowered, CONTAINER_PHRASES):
        patch.container_compatible = True

    if _contains(lowered, HIGH_ISOLATION_PHRASES):
        patch.workload_isolation = IsolationLevel.HIGH
    if _contains(lowered, HA_PHRASES):
        patch.high_availability = True
    if _contains(lowered, PUBLIC_PHRASES):
        patch.internet_access = True
        patch.network_exposure = NetworkExposure.PUBLIC

    for phrases, level in SENSITIVITY_PHRASES:
        if _contains(lowered, phrases):
            patch.sensitive_data = level
            break

    for phrases, environment in ENVIRONMENT_PHRASES:
        if _contains(lowered, phrases):
            patch.environment = environment
            break

    if _contains(lowered, COST_PHRASES):
        patch.optimization_goal = OptimizationGoal.COST
    elif _contains(lowered, PERF_PHRASES):
        patch.optimization_goal = OptimizationGoal.PERFORMANCE
    if _contains(lowered, LATENCY_PHRASES):
        patch.latency_sensitive = True

    # --- a friendly name ---------------------------------------------------
    patch.name = _suggest_name(lowered, patch)
    return patch


def _suggest_name(lowered: str, patch: IntentPatch) -> str | None:
    base: str | None = None
    for hint in MODEL_NAME_HINTS:
        if hint in lowered:
            base = hint
            break
    if base is None and patch.workload_type:
        base = patch.workload_type.replace("_", "-")
    if base is None:
        return None
    suffix = ""
    if patch.sensitive_data in (Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED):
        suffix = "-internal"
    elif patch.environment is Environment.PROD:
        suffix = "-prod"
    elif patch.environment is Environment.DEV:
        suffix = "-dev"
    return f"{base}{suffix}"


def parse_answer(field: str, raw: object) -> object | None:
    """Coerce a wizard answer (or a bare chat reply) into the right type."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    lowered = text.lower()

    integer_fields = {
        "concurrent_users",
        "expected_users",
        "estimated_vcpu",
        "estimated_ram_gb",
        "gpu_count",
        "gpu_memory_gb",
        "storage_gb",
        "replicas",
    }
    boolean_fields = {
        "high_availability",
        "gpu_required",
        "cpu_required",
        "internet_access",
        "container_compatible",
        "latency_sensitive",
    }

    if field in integer_fields:
        match = re.search(r"\d+", lowered.replace(",", ""))
        if match:
            value = int(match.group())
            if "tb" in lowered and field == "storage_gb":
                value *= 1024
            return value
        return _NUMBER_WORDS.get(lowered)
    if field in boolean_fields:
        if lowered in {"y", "yes", "true", "yep", "sure", "please", "1"}:
            return True
        if lowered in {"n", "no", "false", "nope", "0"}:
            return False
        return None
    if field == "environment":
        for phrases, environment in ENVIRONMENT_PHRASES:
            if any(p.strip() in lowered for p in phrases):
                return environment
        return None
    if field == "sensitive_data":
        for phrases, level in SENSITIVITY_PHRASES:
            if any(p.strip() in lowered for p in phrases):
                return level
        return None
    if field == "workload_isolation":
        for level in IsolationLevel:
            if str(level) in lowered:
                return level
        if _contains(lowered, HIGH_ISOLATION_PHRASES):
            return IsolationLevel.HIGH
        return None
    if field == "optimization_goal":
        for goal in OptimizationGoal:
            if str(goal) in lowered:
                return goal
        return None
    if field == "network_exposure":
        for exposure in NetworkExposure:
            if str(exposure) in lowered:
                return exposure
        if _contains(lowered, PUBLIC_PHRASES):
            return NetworkExposure.PUBLIC
        return None
    return text
