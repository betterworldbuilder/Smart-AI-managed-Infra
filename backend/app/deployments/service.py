"""Deployment orchestration -- the human-in-the-loop workflow (sections 14-16).

This service owns the lifecycle. It is the only place that moves a deployment
between states, and it always goes through `StateMachine.check()`, which
refuses to let a non-human cross either approval gate.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..adapters.deployment_backend import DeploymentBackend
from ..audit import AuditService
from ..events import EventBus
from ..governor import RecommendationEngine, StateMachine, build_spec, workload_name
from ..inventory import GlobalResourceInventoryService
from ..models import (
    Actor,
    ActorKind,
    AuditAction,
    Deployment,
    DeploymentEvent,
    DeploymentState,
    Placement,
    Recommendation,
    StateTransition,
    WorkloadIntent,
)
from ..store import Repositories

log = logging.getLogger(__name__)


class DeploymentService:
    def __init__(
        self,
        backend: DeploymentBackend,
        repositories: Repositories,
        audit: AuditService,
        bus: EventBus,
        inventory: GlobalResourceInventoryService,
        engine: RecommendationEngine,
    ) -> None:
        self.backend = backend
        self.repositories = repositories
        self.audit = audit
        self.bus = bus
        self.inventory = inventory
        self.engine = engine
        self._tasks: set[asyncio.Task] = set()

    # -- queries -------------------------------------------------------------

    async def list(self) -> list[Deployment]:
        deployments = await self.repositories.deployments.list()
        return sorted(deployments, key=lambda d: d.created_at, reverse=True)

    async def get(self, deployment_id: str) -> Deployment:
        return await self.repositories.deployments.require(deployment_id)

    # -- creation ------------------------------------------------------------

    async def create_from_recommendation(
        self,
        recommendation_id: str,
        option_key: str,
        actor: Actor,
        name: str | None = None,
    ) -> Deployment:
        recommendation = await self.repositories.recommendations.require(recommendation_id)
        option = recommendation.option_by_key(option_key)
        if option is None:
            raise KeyError(f"option '{option_key}' is not part of {recommendation_id}")
        if not option.viable:
            raise ValueError(
                f"option '{option_key}' was rejected by policy: "
                + "; ".join(option.blocked_by)
            )

        intent = recommendation.intent
        deployment = Deployment(
            name=name or workload_name(intent),
            conversation_id=recommendation.conversation_id,
            recommendation_id=recommendation.id,
            selected_option=option.option,
            runtime=option.runtime,
            intent=intent,
            option=option,
            state=DeploymentState.DRAFT,
        )
        await self._transition(
            deployment, DeploymentState.RECOMMENDED, Actor.ai(), "Governor ranked the options"
        )
        await self._transition(
            deployment,
            DeploymentState.WAITING_FOR_HUMAN,
            Actor.ai(),
            "Awaiting human decision -- the AI stops here",
        )
        await self.repositories.deployments.save(deployment)

        await self.audit.record(
            action=AuditAction.DEPLOYMENT_SELECTED,
            user=actor.name,
            actor_kind=actor.kind,
            deployment=deployment.id,
            recommendation=recommendation.id,
            resources=_digest(option),
            message=f"{actor.name} selected {option.title} for review",
        )
        self._publish(deployment, "deployment.created")
        return deployment

    # -- gate 1: approve the option -----------------------------------------

    async def approve(self, deployment_id: str, actor: Actor, note: str = "") -> Deployment:
        """HUMAN APPROVAL GATE 1. Generates the spec and the openCenter plan."""
        deployment = await self.get(deployment_id)
        await self._transition(
            deployment, DeploymentState.APPROVED, actor, note or "Option approved"
        )
        await self.audit.record(
            action=AuditAction.DEPLOYMENT_APPROVED,
            user=actor.name,
            actor_kind=actor.kind,
            deployment=deployment.id,
            recommendation=deployment.recommendation_id,
            resources=_digest(deployment.option),
            message=f"{actor.name} approved {deployment.option.title}",
        )
        await self.repositories.deployments.save(deployment)
        self._publish(deployment, "deployment.approved")

        try:
            await self._build_plan(deployment, actor)
        except Exception as exc:  # noqa: BLE001
            log.exception("planning failed")
            deployment.failure_reason = str(exc)
            await self._transition(
                deployment, DeploymentState.FAILED, Actor.system(), f"planning failed: {exc}"
            )
            await self.repositories.deployments.save(deployment)
            self._publish(deployment, "deployment.failed")
        return deployment

    async def _build_plan(self, deployment: Deployment, actor: Actor) -> None:
        policy = None
        if deployment.recommendation_id:
            recommendation = await self.repositories.recommendations.get(
                deployment.recommendation_id
            )
            if recommendation is not None:
                from ..models import PolicyEvaluation

                policy = PolicyEvaluation(
                    runtime=deployment.runtime,
                    decisions=deployment.option.policy_decisions,
                )

        spec = build_spec(deployment.intent, deployment.option, policy, name=deployment.name)
        deployment.spec = spec
        await self.audit.record(
            action=AuditAction.SPEC_GENERATED,
            user=actor.name,
            actor_kind=ActorKind.SYSTEM,
            deployment=deployment.id,
            detail={"spec": spec.to_wire()},
            message=f"DeploymentSpec generated for {deployment.name}",
        )

        plan = await self.backend.plan_deployment(spec, deployment.id)
        deployment.plan = plan
        self._add_event(
            deployment,
            "openCenter generated a plan: " + ", ".join(plan.rendered_changes()[:3]) + "...",
            source="opencenter",
        )
        await self._transition(
            deployment,
            DeploymentState.PLANNED,
            Actor.system(),
            f"{len(plan.changes)} changes, {len(plan.artifacts)} artefacts",
        )
        await self.audit.record(
            action=AuditAction.PLAN_GENERATED,
            user=actor.name,
            actor_kind=ActorKind.SYSTEM,
            deployment=deployment.id,
            detail={
                "changes": plan.rendered_changes(),
                "issues": [issue.model_dump() for issue in plan.issues],
                "valid": plan.valid,
                "cluster": plan.cluster_id,
            },
            message=f"plan {plan.id} ({'valid' if plan.valid else 'INVALID'})",
        )
        await self._transition(
            deployment,
            DeploymentState.WAITING_FOR_FINAL_APPROVAL,
            Actor.system(),
            "Plan ready -- waiting for the final human approval",
        )
        await self.repositories.deployments.save(deployment)
        self._publish(deployment, "deployment.planned")

    # -- gate 2: authorise execution ----------------------------------------

    async def apply(self, deployment_id: str, actor: Actor, note: str = "") -> Deployment:
        """HUMAN APPROVAL GATE 2. Starts the real (or simulated) pipeline."""
        deployment = await self.get(deployment_id)
        if deployment.plan is not None and not deployment.plan.valid:
            raise ValueError(
                "the plan has blocking validation errors: "
                + "; ".join(
                    issue.message for issue in deployment.plan.issues if issue.severity == "error"
                )
            )

        await self._transition(
            deployment, DeploymentState.DEPLOYING, actor, note or "Execution authorised"
        )
        deployment.opencenter_run_id = f"run-{deployment.id[-6:]}"
        deployment.progress = 0
        await self.repositories.deployments.save(deployment)

        await self.audit.record(
            action=AuditAction.FINAL_APPROVAL,
            user=actor.name,
            actor_kind=actor.kind,
            deployment=deployment.id,
            resources=_digest(deployment.option),
            message=f"{actor.name} authorised execution of {deployment.name}",
        )
        await self.audit.record(
            action=AuditAction.DEPLOYMENT_EXECUTION,
            user=actor.name,
            actor_kind=ActorKind.SYSTEM,
            deployment=deployment.id,
            detail={"engine": self.backend.engine_label, "run": deployment.opencenter_run_id},
            message=f"pipeline started via {self.backend.engine_label}",
        )
        self._publish(deployment, "deployment.deploying")
        self._spawn(self._run_pipeline(deployment.id, actor))
        return deployment

    async def _run_pipeline(self, deployment_id: str, actor: Actor) -> None:
        deployment = await self.get(deployment_id)
        assert deployment.spec is not None
        failed = False
        try:
            async for step in self.backend.apply_deployment(deployment.spec, deployment):
                deployment = await self.get(deployment_id)
                deployment.progress = max(deployment.progress, step.progress)
                self._add_event(
                    deployment,
                    f"{step.title}{': ' + step.message if step.message else ''}",
                    level=step.level,
                    data=step.data,
                )
                placement = step.data.get("placement")
                if placement:
                    deployment.placement = Placement(**placement)
                    deployment.allocated_gpu_ids = deployment.placement.gpu_ids
                await self.repositories.deployments.save(deployment)
                self._publish(deployment, "deployment.progress", {"step": step.model_dump()})
                if step.failed:
                    failed = True
                    deployment.failure_reason = step.message or step.title
                    break
        except Exception as exc:  # noqa: BLE001
            log.exception("deployment pipeline crashed")
            failed = True
            deployment = await self.get(deployment_id)
            deployment.failure_reason = str(exc)
            self._add_event(deployment, f"pipeline error: {exc}", level="error")

        deployment = await self.get(deployment_id)
        if failed:
            await self._transition(
                deployment,
                DeploymentState.FAILED,
                Actor.system(),
                deployment.failure_reason or "pipeline failed",
            )
            deployment.health = "failed"
        else:
            await self._transition(
                deployment, DeploymentState.RUNNING, Actor.system(), "workload healthy"
            )
            deployment.health = "healthy"
            deployment.progress = 100
        await self.repositories.deployments.save(deployment)
        self.inventory.invalidate()

        await self.audit.record(
            action=AuditAction.DEPLOYMENT_RESULT,
            user=actor.name,
            actor_kind=ActorKind.SYSTEM,
            deployment=deployment.id,
            resources=_digest(deployment.option),
            detail={
                "state": str(deployment.state),
                "placement": deployment.placement.model_dump() if deployment.placement else None,
            },
            message=f"{deployment.name} is {deployment.state}",
        )
        self._publish(
            deployment, "deployment.failed" if failed else "deployment.running"
        )

    # -- rejection / modification / rollback ---------------------------------

    async def reject(self, deployment_id: str, actor: Actor, reason: str = "") -> Deployment:
        deployment = await self.get(deployment_id)
        await self._transition(
            deployment, DeploymentState.REJECTED, actor, reason or "rejected by the operator"
        )
        await self.repositories.deployments.save(deployment)
        await self.audit.record(
            action=AuditAction.DEPLOYMENT_REJECTED,
            user=actor.name,
            actor_kind=actor.kind,
            deployment=deployment.id,
            message=reason or f"{actor.name} rejected {deployment.name}",
        )
        self._publish(deployment, "deployment.rejected")
        return deployment

    async def modify(
        self, deployment_id: str, intent: WorkloadIntent, actor: Actor
    ) -> tuple[Deployment, Recommendation]:
        """Human edits the request: back to DRAFT and re-run the Governor."""
        deployment = await self.get(deployment_id)
        await self._transition(
            deployment, DeploymentState.DRAFT, actor, "human modified the request"
        )
        deployment.intent = intent
        deployment.spec = None
        deployment.plan = None

        recommendation = await self.engine.recommend(
            intent, conversation_id=deployment.conversation_id
        )
        await self.repositories.recommendations.save(recommendation)
        deployment.recommendation_id = recommendation.id

        option = recommendation.option_by_key(deployment.selected_option or "") or (
            recommendation.top
        )
        if option is None:
            raise ValueError("no viable option remains after the modification")
        deployment.option = option
        deployment.runtime = option.runtime
        deployment.selected_option = option.option

        await self._transition(deployment, DeploymentState.RECOMMENDED, Actor.ai(), "re-scored")
        await self._transition(
            deployment, DeploymentState.WAITING_FOR_HUMAN, Actor.ai(), "awaiting decision"
        )
        await self.repositories.deployments.save(deployment)
        await self.audit.record(
            action=AuditAction.HUMAN_MODIFICATION,
            user=actor.name,
            actor_kind=actor.kind,
            deployment=deployment.id,
            recommendation=recommendation.id,
            detail=intent.model_dump(mode="json"),
            message=f"{actor.name} modified the request; options re-scored",
        )
        self._publish(deployment, "deployment.modified")
        return deployment, recommendation

    async def rollback(self, deployment_id: str, actor: Actor, reason: str = "") -> Deployment:
        deployment = await self.get(deployment_id)
        await self._transition(
            deployment, DeploymentState.ROLLING_BACK, actor, reason or "rollback requested"
        )
        await self.repositories.deployments.save(deployment)
        await self.audit.record(
            action=AuditAction.DEPLOYMENT_ROLLBACK,
            user=actor.name,
            actor_kind=actor.kind,
            deployment=deployment.id,
            message=reason or f"{actor.name} requested a rollback of {deployment.name}",
        )
        self._publish(deployment, "deployment.rolling_back")
        self._spawn(self._run_rollback(deployment.id, actor))
        return deployment

    async def _run_rollback(self, deployment_id: str, actor: Actor) -> None:
        deployment = await self.get(deployment_id)
        failed = False
        try:
            async for step in self.backend.rollback_deployment(deployment):
                deployment = await self.get(deployment_id)
                deployment.progress = step.progress
                self._add_event(deployment, step.title, level=step.level, data=step.data)
                await self.repositories.deployments.save(deployment)
                self._publish(deployment, "deployment.progress", {"step": step.model_dump()})
                if step.failed:
                    failed = True
                    break
        except Exception as exc:  # noqa: BLE001
            log.exception("rollback crashed")
            failed = True
            deployment = await self.get(deployment_id)
            self._add_event(deployment, f"rollback error: {exc}", level="error")

        deployment = await self.get(deployment_id)
        await self._transition(
            deployment,
            DeploymentState.FAILED if failed else DeploymentState.ROLLED_BACK,
            Actor.system(),
            "rollback finished",
        )
        deployment.allocated_gpu_ids = []
        deployment.health = "rolled_back" if not failed else "failed"
        await self.repositories.deployments.save(deployment)
        self.inventory.invalidate()
        self._publish(deployment, "deployment.rolled_back")

    # -- helpers -------------------------------------------------------------

    async def _transition(
        self, deployment: Deployment, target: DeploymentState, actor: Actor, note: str = ""
    ) -> None:
        StateMachine.check(deployment.state, target, actor)
        transition = StateTransition(
            from_state=deployment.state, to_state=target, actor=actor, note=note
        )
        deployment.history.append(transition)
        deployment.state = target
        deployment.updated_at = transition.at
        await self.audit.record(
            action=AuditAction.STATE_TRANSITION,
            user=actor.name,
            actor_kind=actor.kind,
            deployment=deployment.id,
            detail={"from": str(transition.from_state), "to": str(target), "note": note},
            message=f"{transition.from_state} -> {target}",
        )

    def _add_event(
        self,
        deployment: Deployment,
        message: str,
        level: str = "info",
        source: str = "opencenter",
        data: dict[str, Any] | None = None,
    ) -> None:
        event = DeploymentEvent(
            deployment_id=deployment.id,
            level=level,
            source=source,
            message=message,
            data=data or {},
        )
        deployment.events.append(event)
        self.bus.publish("deployment.event", event.model_dump(mode="json"))

    def _publish(
        self, deployment: Deployment, topic: str, extra: dict[str, Any] | None = None
    ) -> None:
        payload: dict[str, Any] = {
            "id": deployment.id,
            "name": deployment.name,
            "state": str(deployment.state),
            "progress": deployment.progress,
            "runtime": str(deployment.runtime),
            "health": deployment.health,
        }
        if deployment.placement:
            payload["placement"] = deployment.placement.model_dump()
        if extra:
            payload.update(extra)
        self.bus.publish(topic, payload)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_for_idle(self, timeout: float = 120.0) -> None:
        """Test helper: wait until background pipelines finish."""
        if not self._tasks:
            return
        await asyncio.wait(set(self._tasks), timeout=timeout)


def _digest(option) -> dict[str, Any]:
    plan = option.resources
    return {
        "runtime": str(option.runtime),
        "gpu": plan.total_gpu,
        "gpu_model": plan.gpu_model,
        "vcpu": plan.total_vcpu,
        "memory_gb": plan.total_ram_gb,
        "storage_gb": plan.storage_gb,
        "score": option.score,
    }
