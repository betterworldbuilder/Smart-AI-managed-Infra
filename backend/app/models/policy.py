"""Policy evaluation results (section 12).

Policies are deterministic. The LLM never produces a `PolicyDecision`; it can
only read them back to explain itself to the human.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .enums import PolicyEffect, RuntimeType


class PolicyDecision(BaseModel):
    """One rule firing against one candidate runtime."""

    rule_id: str
    title: str
    effect: PolicyEffect
    message: str
    runtime: RuntimeType | None = None
    #: Score delta applied by PREFER/DISCOURAGE effects (ignored for DENY).
    score_delta: float = 0.0
    #: Constraints the DeploymentSpec generator must honour (e.g. network policy).
    obligations: dict[str, object] = Field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.effect is PolicyEffect.DENY


class PolicyEvaluation(BaseModel):
    """All decisions for one candidate runtime."""

    runtime: RuntimeType
    decisions: list[PolicyDecision] = Field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return not any(d.blocking for d in self.decisions)

    @property
    def denials(self) -> list[PolicyDecision]:
        return [d for d in self.decisions if d.blocking]

    @property
    def score_delta(self) -> float:
        return sum(d.score_delta for d in self.decisions)

    @property
    def obligations(self) -> dict[str, object]:
        merged: dict[str, object] = {}
        for decision in self.decisions:
            merged.update(decision.obligations)
        return merged


class PolicyReport(BaseModel):
    """Everything the policy engine concluded for one request."""

    engine: str = "internal"
    global_decisions: list[PolicyDecision] = Field(default_factory=list)
    per_runtime: dict[str, PolicyEvaluation] = Field(default_factory=dict)

    def for_runtime(self, runtime: RuntimeType) -> PolicyEvaluation:
        return self.per_runtime.get(str(runtime), PolicyEvaluation(runtime=runtime))
