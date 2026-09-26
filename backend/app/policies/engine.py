"""Policy engine front door.

`InternalPolicyEngine` runs the Python rules in `rules.py`. `OPAPolicyEngine`
sends the same input document to an OPA sidecar and merges the rego verdict --
with the internal engine as the fallback, because a POC must never become
undeployable just because OPA is down.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from ..config import Settings
from ..models import (
    GlobalInventory,
    PolicyDecision,
    PolicyEffect,
    PolicyEvaluation,
    PolicyReport,
    ResourcePlan,
    RuntimeType,
    WorkloadIntent,
)
from .rules import GLOBAL_RULES, RUNTIME_RULES, PolicyContext

log = logging.getLogger(__name__)


class PolicyEngine(ABC):
    name = "policy"

    @abstractmethod
    async def evaluate(
        self,
        intent: WorkloadIntent,
        inventory: GlobalInventory,
        candidates: dict[RuntimeType, ResourcePlan],
    ) -> PolicyReport: ...


class InternalPolicyEngine(PolicyEngine):
    name = "internal"

    async def evaluate(
        self,
        intent: WorkloadIntent,
        inventory: GlobalInventory,
        candidates: dict[RuntimeType, ResourcePlan],
    ) -> PolicyReport:
        base = PolicyContext(intent=intent, inventory=inventory)
        global_decisions: list[PolicyDecision] = []
        for rule in GLOBAL_RULES:
            global_decisions.extend(rule(base))

        per_runtime: dict[str, PolicyEvaluation] = {}
        globally_denied = [d for d in global_decisions if d.effect is PolicyEffect.DENY]
        for runtime, plan in candidates.items():
            ctx = PolicyContext(
                intent=intent, inventory=inventory, runtime=runtime, plan=plan
            )
            decisions: list[PolicyDecision] = list(globally_denied)
            for rule in RUNTIME_RULES:
                decisions.extend(rule(ctx))
            per_runtime[str(runtime)] = PolicyEvaluation(runtime=runtime, decisions=decisions)

        return PolicyReport(
            engine=self.name, global_decisions=global_decisions, per_runtime=per_runtime
        )


class OPAPolicyEngine(PolicyEngine):
    """Rego-backed engine (`POLICY_ENGINE=opa`).

    Expects a policy at `data.<OPA_PACKAGE>.decisions` returning a list of
    objects shaped like `PolicyDecision`. See `policies/*.rego`.
    """

    name = "opa"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.fallback = InternalPolicyEngine()

    async def evaluate(
        self,
        intent: WorkloadIntent,
        inventory: GlobalInventory,
        candidates: dict[RuntimeType, ResourcePlan],
    ) -> PolicyReport:
        payload = {
            "input": {
                "intent": intent.model_dump(mode="json"),
                "inventory": {
                    "cpu": inventory.cpu.model_dump(),
                    "ram_gb": inventory.ram_gb.model_dump(),
                    "gpu": {
                        "total": inventory.gpu.total,
                        "available": inventory.gpu.available,
                        "by_model": inventory.gpu_model_counts(),
                    },
                    "storage": {
                        "total_tb": inventory.storage.total_tb,
                        "available_tb": inventory.storage.available_tb,
                    },
                },
                "candidates": {
                    str(runtime): plan.model_dump(mode="json")
                    for runtime, plan in candidates.items()
                },
            }
        }
        try:
            import httpx

            url = (
                f"{self.settings.opa_url.rstrip('/')}/v1/data/"
                f"{self.settings.opa_package.strip('/')}"
            )
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                result = response.json().get("result", {})
        except Exception as exc:  # noqa: BLE001
            log.warning("OPA unreachable (%s); falling back to the internal engine", exc)
            report = await self.fallback.evaluate(intent, inventory, candidates)
            report.engine = "internal (OPA unreachable)"
            return report

        report = await self.fallback.evaluate(intent, inventory, candidates)
        report.engine = "opa"
        for raw in result.get("decisions", []):
            try:
                decision = PolicyDecision(**raw)
            except Exception:  # noqa: BLE001 - never trust external policy output blindly
                log.warning("ignoring malformed OPA decision: %s", raw)
                continue
            if decision.runtime is None:
                report.global_decisions.append(decision)
                continue
            evaluation = report.per_runtime.setdefault(
                str(decision.runtime), PolicyEvaluation(runtime=decision.runtime)
            )
            evaluation.decisions.append(decision)
        return report


def build_policy_engine(settings: Settings) -> PolicyEngine:
    if settings.policy_engine == "opa":
        return OPAPolicyEngine(settings)
    return InternalPolicyEngine()
