"""The guided wizard (section 10).

The Copilot must ask only what it genuinely needs. Each question declares when
it is relevant and why it matters; anything the platform can infer or default
safely is never asked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..governor.sizing import WorkloadProfile, WorkloadProfiles
from ..models import Environment, WizardQuestion, WorkloadIntent

#: At most this many questions per turn -- a wall of questions is not a copilot.
MAX_QUESTIONS_PER_TURN = 2
#: Questions at or below this priority block a recommendation. Anything above
#: it has a safe default, so the Copilot offers it on the Deploy page and in the
#: recommendation's "open questions" instead of interrogating the operator.
BLOCKING_PRIORITY = 25


@dataclass
class QuestionSpec:
    field: str
    question: str
    why: str
    kind: str = "text"
    options: tuple[str, ...] = ()
    priority: int = 50
    relevant: Callable[[WorkloadIntent, WorkloadProfile], bool] = lambda i, p: True

    def build(self) -> WizardQuestion:
        return WizardQuestion(
            field=self.field,
            question=self.question,
            why=self.why,
            kind=self.kind,
            options=list(self.options),
            priority=self.priority,
        )


def _uses_user_scaling(profile: WorkloadProfile) -> bool:
    return bool(
        profile.scaling.get("concurrent_users_per_gpu")
        or profile.scaling.get("concurrent_users_per_vcpu")
    )


QUESTIONS: tuple[QuestionSpec, ...] = (
    QuestionSpec(
        field="workload_type",
        question="What kind of workload is this?",
        why="The workload class decides whether a GPU, a VM or a container is appropriate.",
        kind="choice",
        options=(
            "llm-inference",
            "web-api",
            "database",
            "ml-training",
            "ai-dev-workstation",
            "legacy-app",
            "batch",
        ),
        priority=5,
        relevant=lambda intent, profile: profile.key == "generic",
    ),
    QuestionSpec(
        field="concurrent_users",
        question="How many concurrent users should it handle at peak?",
        why="Concurrency drives GPU count and replica count far more than total headcount.",
        kind="number",
        priority=10,
        relevant=lambda intent, profile: (
            _uses_user_scaling(profile)
            and intent.concurrent_users is None
            and intent.gpu_count is None
            and intent.estimated_vcpu is None
        ),
    ),
    QuestionSpec(
        field="storage_gb",
        question="How much storage does it need, in GB?",
        why="Storage sizing selects the backend pool and affects the capacity check.",
        kind="number",
        priority=15,
        relevant=lambda intent, profile: (
            intent.storage_gb is None and profile.key in ("database", "ml-training")
        ),
    ),
    QuestionSpec(
        field="environment",
        question="Is this for development, test or production?",
        why="Production adds availability requirements; development prefers the cheapest "
        "compatible GPU.",
        kind="choice",
        options=("dev", "test", "prod"),
        priority=20,
    ),
    QuestionSpec(
        field="sensitive_data",
        question="How sensitive is the data it will handle?",
        why="Classification decides network exposure, encryption and whether a shared "
        "kernel is acceptable.",
        kind="choice",
        options=("public", "internal", "confidential", "restricted"),
        priority=25,
    ),
    QuestionSpec(
        field="operating_system",
        question="Which operating system does it need?",
        why="A specific OS (or Windows) rules out container runtimes.",
        kind="text",
        priority=41,
        relevant=lambda intent, profile: (
            intent.operating_system is None
            and profile.key in ("legacy-app", "ai-dev-workstation")
        ),
    ),
    QuestionSpec(
        field="gpu_count",
        question="How many GPUs does the job need?",
        why="Training jobs scale with GPU count; this also decides whether it fits at all.",
        kind="number",
        priority=40,
        relevant=lambda intent, profile: (
            profile.key == "ml-training" and intent.gpu_count is None
        ),
    ),
    QuestionSpec(
        field="high_availability",
        question="Does it need high availability (survive a node failure)?",
        why="HA changes replica count, anti-affinity and the runtime ranking.",
        kind="boolean",
        priority=44,
        relevant=lambda intent, profile: (
            intent.environment is Environment.PROD
            and profile.key in ("database", "web-api", "llm-inference")
            and not intent.high_availability
        ),
    ),
    QuestionSpec(
        field="workload_isolation",
        question="Does this need strict VM-level isolation, or is a container acceptable?",
        why="Strict isolation moves the workload from Kubernetes onto a dedicated VM.",
        kind="choice",
        options=("low", "medium", "high"),
        priority=42,
        relevant=lambda intent, profile: intent.gpu_required and profile.key != "ai-dev-workstation",
    ),
    QuestionSpec(
        field="network_exposure",
        question="Should it be reachable from the internet, or internal only?",
        why="Exposure decides the service type, the NetworkPolicy and the security review.",
        kind="choice",
        options=("internal", "organisation", "public"),
        priority=45,
        relevant=lambda intent, profile: profile.key in ("web-api",),
    ),
)


class Wizard:
    def __init__(self, profiles: WorkloadProfiles) -> None:
        self.profiles = profiles

    def pending(self, intent: WorkloadIntent, provided: set[str]) -> list[QuestionSpec]:
        profile = self.profiles.get(intent.workload_type)
        return sorted(
            (
                spec
                for spec in QUESTIONS
                if spec.field not in provided and spec.relevant(intent, profile)
            ),
            key=lambda spec: spec.priority,
        )

    def next_questions(
        self, intent: WorkloadIntent, provided: set[str]
    ) -> list[WizardQuestion]:
        pending = [
            spec for spec in self.pending(intent, provided) if spec.priority <= BLOCKING_PRIORITY
        ]
        return [spec.build() for spec in pending[:MAX_QUESTIONS_PER_TURN]]

    def ready(self, intent: WorkloadIntent, provided: set[str]) -> bool:
        """True when nothing blocking is left to ask."""
        return not any(
            spec.priority <= BLOCKING_PRIORITY for spec in self.pending(intent, provided)
        )

    def all_questions(self, intent: WorkloadIntent, provided: set[str]) -> list[WizardQuestion]:
        """Everything still unanswered -- used by the Deploy wizard page."""
        return [spec.build() for spec in self.pending(intent, provided)]
