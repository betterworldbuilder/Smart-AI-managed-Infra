"""Deployment state machine (section 14).

The single most important rule in this codebase lives here:

    WAITING_FOR_HUMAN        -> APPROVED                    requires a human
    WAITING_FOR_FINAL_APPROVAL -> DEPLOYING                 requires a human

Any attempt by the AI (or any non-human actor) to make those transitions raises
`HumanApprovalRequired`. There is no flag, no configuration and no "auto
approve" mode that relaxes it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Actor, ActorKind, DeploymentState


class TransitionError(RuntimeError):
    """The requested transition is not part of the lifecycle."""


class HumanApprovalRequired(PermissionError):
    """A non-human actor tried to cross an approval gate."""


@dataclass(frozen=True)
class Transition:
    source: DeploymentState
    target: DeploymentState
    requires_human: bool = False
    description: str = ""


S = DeploymentState

TRANSITIONS: tuple[Transition, ...] = (
    Transition(S.DRAFT, S.RECOMMENDED, description="Governor produced ranked options"),
    Transition(S.DRAFT, S.REJECTED, requires_human=True, description="Request abandoned"),
    Transition(
        S.RECOMMENDED,
        S.WAITING_FOR_HUMAN,
        description="Options presented, awaiting a decision",
    ),
    Transition(
        S.RECOMMENDED, S.DRAFT, requires_human=True, description="Human edited the request"
    ),
    Transition(
        S.WAITING_FOR_HUMAN,
        S.APPROVED,
        requires_human=True,
        description="HUMAN APPROVAL GATE 1 -- option approved",
    ),
    Transition(
        S.WAITING_FOR_HUMAN,
        S.DRAFT,
        requires_human=True,
        description="Human modified the recommendation",
    ),
    Transition(
        S.WAITING_FOR_HUMAN, S.REJECTED, requires_human=True, description="Human rejected"
    ),
    Transition(S.APPROVED, S.PLANNED, description="openCenter generated and validated a plan"),
    Transition(S.APPROVED, S.FAILED, description="Plan generation failed"),
    Transition(
        S.PLANNED,
        S.WAITING_FOR_FINAL_APPROVAL,
        description="Plan shown to the human before execution",
    ),
    Transition(S.PLANNED, S.REJECTED, requires_human=True, description="Plan rejected"),
    Transition(
        S.WAITING_FOR_FINAL_APPROVAL,
        S.DEPLOYING,
        requires_human=True,
        description="HUMAN APPROVAL GATE 2 -- execution authorised",
    ),
    Transition(
        S.WAITING_FOR_FINAL_APPROVAL,
        S.REJECTED,
        requires_human=True,
        description="Execution cancelled",
    ),
    Transition(
        S.WAITING_FOR_FINAL_APPROVAL,
        S.DRAFT,
        requires_human=True,
        description="Human sent the plan back for changes",
    ),
    Transition(S.DEPLOYING, S.RUNNING, description="openCenter reported success"),
    Transition(S.DEPLOYING, S.FAILED, description="openCenter reported failure"),
    Transition(
        S.RUNNING, S.ROLLING_BACK, requires_human=True, description="Human requested rollback"
    ),
    Transition(
        S.FAILED, S.ROLLING_BACK, requires_human=True, description="Human requested rollback"
    ),
    Transition(S.ROLLING_BACK, S.ROLLED_BACK, description="Rollback finished"),
    Transition(S.ROLLING_BACK, S.FAILED, description="Rollback failed"),
)

_INDEX: dict[tuple[DeploymentState, DeploymentState], Transition] = {
    (t.source, t.target): t for t in TRANSITIONS
}

#: The two gates the AI can never cross on its own.
HUMAN_GATES: tuple[tuple[DeploymentState, DeploymentState], ...] = (
    (S.WAITING_FOR_HUMAN, S.APPROVED),
    (S.WAITING_FOR_FINAL_APPROVAL, S.DEPLOYING),
)


class StateMachine:
    @staticmethod
    def allowed_targets(state: DeploymentState) -> list[DeploymentState]:
        return [t.target for t in TRANSITIONS if t.source == state]

    @staticmethod
    def find(source: DeploymentState, target: DeploymentState) -> Transition:
        transition = _INDEX.get((source, target))
        if transition is None:
            raise TransitionError(
                f"{source} -> {target} is not a valid transition "
                f"(allowed: {[str(s) for s in StateMachine.allowed_targets(source)]})"
            )
        return transition

    @staticmethod
    def check(source: DeploymentState, target: DeploymentState, actor: Actor) -> Transition:
        transition = StateMachine.find(source, target)
        if transition.requires_human and actor.kind is not ActorKind.HUMAN:
            raise HumanApprovalRequired(
                f"{source} -> {target} requires an explicit human action; "
                f"actor '{actor.name}' is {actor.kind}."
            )
        return transition

    @staticmethod
    def is_gate(source: DeploymentState, target: DeploymentState) -> bool:
        return (source, target) in HUMAN_GATES

    @staticmethod
    def graph() -> list[dict[str, object]]:
        """Serialisable lifecycle, rendered by the UI."""
        return [
            {
                "from": str(t.source),
                "to": str(t.target),
                "requires_human": t.requires_human,
                "description": t.description,
                "gate": StateMachine.is_gate(t.source, t.target),
            }
            for t in TRANSITIONS
        ]
