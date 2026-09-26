"""The Recommendation Engine (section 13).

Deterministic end to end: profile -> sizing -> policy -> score -> ranked
options. The LLM never ranks anything; at most it phrases the summary.

Every viable option is returned, not just the winner, because the human is the
one who decides.
"""

from __future__ import annotations

import logging

from ..inventory import GlobalResourceInventoryService, sanitize_inventory
from ..models import (
    GlobalInventory,
    PolicyReport,
    Recommendation,
    RecommendationOption,
    RuntimeType,
    WizardQuestion,
    WorkloadIntent,
)
from ..policies import PolicyEngine
from .scoring import Scorer, _label
from .sizing import SizingCalculator, WorkloadProfiles

log = logging.getLogger(__name__)

TITLES = {
    RuntimeType.KUBERNETES_GPU: "Kubernetes GPU workload",
    RuntimeType.KUBERNETES_CPU: "Kubernetes CPU workload",
    RuntimeType.OPENSTACK_GPU_VM: "OpenStack GPU VM",
    RuntimeType.OPENSTACK_CPU_VM: "OpenStack CPU VM",
    RuntimeType.DATABASE_SERVICE: "Managed database service",
    RuntimeType.VMWARE_VM: "VMware VM",
    RuntimeType.BARE_METAL: "Bare metal server",
}


class RecommendationEngine:
    def __init__(
        self,
        profiles: WorkloadProfiles,
        sizing: SizingCalculator,
        scorer: Scorer,
        policy_engine: PolicyEngine,
        inventory_service: GlobalResourceInventoryService,
    ) -> None:
        self.profiles = profiles
        self.sizing = sizing
        self.scorer = scorer
        self.policy_engine = policy_engine
        self.inventory_service = inventory_service

    async def recommend(
        self,
        intent: WorkloadIntent,
        *,
        conversation_id: str | None = None,
        open_questions: list[WizardQuestion] | None = None,
        inventory: GlobalInventory | None = None,
    ) -> Recommendation:
        inventory = inventory or await self.inventory_service.get(refresh=True)
        profile = self.profiles.get(intent.workload_type)
        candidates = self.sizing.candidates(intent, inventory)
        policy_report: PolicyReport = await self.policy_engine.evaluate(
            intent, inventory, candidates
        )

        viable: list[RecommendationOption] = []
        rejected: list[RecommendationOption] = []

        for runtime, plan in candidates.items():
            evaluation = policy_report.for_runtime(runtime)
            result = self.scorer.score(
                intent=intent,
                plan=plan,
                runtime=runtime,
                profile=profile,
                inventory=inventory,
                policy=evaluation,
            )
            allowed = evaluation.allowed and result.capacity_impact.fits
            blocked_by = [f"{d.rule_id}: {d.message}" for d in evaluation.denials]
            if not result.capacity_impact.fits and not blocked_by:
                blocked_by.append("Insufficient free capacity for this plan")

            option = RecommendationOption(
                option=str(runtime).lower(),
                runtime=runtime,
                title=TITLES.get(runtime, str(runtime)),
                score=max(0.0, min(100.0, round(result.breakdown.total, 1))),
                viable=allowed,
                reason=_dedupe(result.reasons),
                risks=_dedupe(result.risks),
                blocked_by=blocked_by,
                resources=plan,
                breakdown=result.breakdown,
                capacity_impact=result.capacity_impact,
                policy_decisions=evaluation.decisions,
                relative_cost_units=result.relative_cost_units,
                native_scheduler=runtime.native_scheduler,
            )
            (viable if allowed else rejected).append(option)

        viable.sort(key=lambda o: o.score, reverse=True)
        rejected.sort(key=lambda o: o.score, reverse=True)
        if viable:
            viable[0].recommended = True

        recommendation = Recommendation(
            conversation_id=conversation_id,
            intent=intent,
            options=viable,
            rejected_options=rejected,
            open_questions=open_questions or [],
            summary=self._summarise(viable, rejected, intent),
            inventory_snapshot=sanitize_inventory(inventory),
            policy_engine=policy_report.engine,
        )
        log.info(
            "recommendation %s: %s viable, top=%s",
            recommendation.id,
            len(viable),
            viable[0].option if viable else "none",
        )
        return recommendation

    def _summarise(
        self,
        viable: list[RecommendationOption],
        rejected: list[RecommendationOption],
        intent: WorkloadIntent,
    ) -> str:
        if not viable:
            blockers = rejected[0].blocked_by[:2] if rejected else []
            return (
                "No runtime satisfies this request with the current capacity and policies. "
                + (" ".join(blockers) if blockers else "")
            ).strip()

        top = viable[0]
        parts = [
            f"Recommended: {top.title} (score {top.score:.0f}/100) -- {top.resources.summary()}."
        ]
        if top.reason:
            parts.append(top.reason[0] + ".")
        if len(viable) > 1:
            alt = viable[1]
            parts.append(
                f"Alternative: {alt.title} (score {alt.score:.0f}) "
                f"-- {alt.resources.summary()}."
            )
        parts.append(
            f"Final node placement stays with {top.native_scheduler}; "
            "nothing is deployed until you approve."
        )
        return " ".join(parts)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


__all__ = ["RecommendationEngine", "TITLES", "_label"]
