"""The AI Infra Copilot (section 9).

Responsibilities, and nothing more:

* understand the request (deterministic extraction, optionally enriched by an LLM),
* ask only the questions that change the answer,
* hand the intent to the Governor,
* explain the Governor's ranked options in plain language.

It cannot deploy, approve, or change infrastructure. It never sees a secret:
the only infrastructure data it passes to a model comes from
`inventory.sanitize_inventory()`.
"""

from __future__ import annotations

import logging
from typing import Any

from ..audit import AuditService
from ..governor import RecommendationEngine
from ..governor.sizing import WorkloadProfiles
from ..inventory import GlobalResourceInventoryService, assert_clean, sanitize_inventory
from ..models import (
    ActorKind,
    AuditAction,
    ChatMessage,
    Conversation,
    IntentPatch,
    Recommendation,
    WizardQuestion,
    WorkloadIntent,
)
from ..store import Repositories
from .extraction import extract_intent, parse_answer
from .llm import LLMProvider, context_block
from .wizard import Wizard

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the AI Infra Copilot for a GPU-native private cloud.

You help an infrastructure operator describe what they need. You do NOT choose
infrastructure: a deterministic Governor scores runtimes and a human approves
them. Never claim anything has been deployed.

Rules:
- Ask at most two questions, and only ones that change the recommendation.
- Explain trade-offs in plain language; always say why.
- You only receive sanitised capacity facts. Never ask for or repeat
  credentials, kubeconfigs, tokens or keys.
- Always remind the operator that nothing is deployed without their approval.
"""


class CopilotService:
    def __init__(
        self,
        provider: LLMProvider,
        profiles: WorkloadProfiles,
        wizard: Wizard,
        engine: RecommendationEngine,
        inventory: GlobalResourceInventoryService,
        repositories: Repositories,
        audit: AuditService,
    ) -> None:
        self.provider = provider
        self.profiles = profiles
        self.wizard = wizard
        self.engine = engine
        self.inventory = inventory
        self.repositories = repositories
        self.audit = audit

    # -- conversation lifecycle ---------------------------------------------

    async def start(self, title: str = "New request") -> Conversation:
        conversation = Conversation(title=title, llm_provider=self.provider.name)
        conversation.add(
            "assistant",
            "Describe the workload you need and I will size it, check live capacity "
            "and policy, and propose options. Nothing is deployed without your approval.",
        )
        await self.repositories.conversations.save(conversation)
        return conversation

    async def get(self, conversation_id: str) -> Conversation:
        return await self.repositories.conversations.require(conversation_id)

    async def list(self) -> list[Conversation]:
        return await self.repositories.conversations.list()

    # -- the main turn -------------------------------------------------------

    async def handle_message(
        self, conversation_id: str | None, text: str, user: str = "admin"
    ) -> tuple[Conversation, Recommendation | None]:
        conversation = (
            await self.get(conversation_id) if conversation_id else await self.start()
        )
        conversation.add("user", text)
        if conversation.title in ("New request", "") and text.strip():
            conversation.title = text.strip()[:60]

        await self.audit.record(
            action=AuditAction.USER_REQUEST,
            user=user,
            actor_kind=ActorKind.HUMAN,
            conversation=conversation.id,
            message=text,
        )

        applied_answer = self._apply_pending_answer(conversation, text)
        await self._absorb(conversation, text, skip_extraction=applied_answer)

        await self.audit.record(
            action=AuditAction.AI_INTERPRETATION,
            user=user,
            actor_kind=ActorKind.AI,
            conversation=conversation.id,
            detail=conversation.intent.model_dump(mode="json"),
            message=f"interpreted as {conversation.intent.workload_type}",
        )

        return await self._respond(conversation, user=user)

    async def answer(
        self, conversation_id: str, answers: dict[str, Any], user: str = "admin"
    ) -> tuple[Conversation, Recommendation | None]:
        """Structured answers from the wizard UI."""
        conversation = await self.get(conversation_id)
        updates: dict[str, Any] = {}
        for field, raw in answers.items():
            value = parse_answer(field, raw)
            if value is not None:
                updates[field] = value
                if field not in conversation.provided:
                    conversation.provided.append(field)
        if updates:
            conversation.intent = conversation.intent.merged_with(updates)
            readable = ", ".join(f"{k}={v}" for k, v in updates.items())
            conversation.add("user", readable, wizard=True)
            await self.audit.record(
                action=AuditAction.HUMAN_MODIFICATION,
                user=user,
                actor_kind=ActorKind.HUMAN,
                conversation=conversation.id,
                detail=updates,
                message=f"wizard answers: {readable}",
            )
        return await self._respond(conversation, user=user)

    async def set_intent(
        self, conversation_id: str, intent: WorkloadIntent, user: str = "admin"
    ) -> tuple[Conversation, Recommendation | None]:
        """Full intent replacement from the Deploy wizard's review step."""
        conversation = await self.get(conversation_id)
        conversation.intent = intent
        conversation.provided = sorted(
            set(conversation.provided) | set(intent.model_dump(exclude_none=True))
        )
        await self.audit.record(
            action=AuditAction.HUMAN_MODIFICATION,
            user=user,
            actor_kind=ActorKind.HUMAN,
            conversation=conversation.id,
            detail=intent.model_dump(mode="json"),
            message="intent replaced from the wizard",
        )
        return await self._respond(conversation, user=user)

    # -- internals -----------------------------------------------------------

    def _apply_pending_answer(self, conversation: Conversation, text: str) -> bool:
        """If we just asked something and this reply answers it, take it."""
        if not conversation.pending_questions:
            return False
        question = conversation.pending_questions[0]
        value = parse_answer(question.field, text)
        if value is None:
            return False
        conversation.intent = conversation.intent.merged_with({question.field: value})
        if question.field not in conversation.provided:
            conversation.provided.append(question.field)
        # A bare answer ("20") should not also be keyword-extracted.
        return len(text.strip().split()) <= 3

    async def _absorb(
        self, conversation: Conversation, text: str, skip_extraction: bool = False
    ) -> None:
        if skip_extraction:
            return
        inventory = await self.inventory.get()
        models = sorted({device.model for device in inventory.gpu.devices})
        patch = extract_intent(text, self.profiles, models)
        updates = patch.as_updates()
        # A name is a convenience, never an answer.
        for field in updates:
            if field not in ("description", "name") and field not in conversation.provided:
                conversation.provided.append(field)
        if conversation.intent.name and "name" in updates:
            updates.pop("name")
        conversation.intent = conversation.intent.merged_with(updates)

        enriched = await self._enrich_with_llm(conversation, text)
        if enriched:
            missing = {
                field: value
                for field, value in enriched.items()
                if field not in conversation.provided
            }
            if missing:
                conversation.intent = conversation.intent.merged_with(missing)
                conversation.provided.extend(missing)

    async def _enrich_with_llm(
        self, conversation: Conversation, text: str
    ) -> dict[str, Any] | None:
        """Ask the model for anything the rules missed. Advisory only."""
        if self.provider.name == "mock":
            return None
        schema = IntentPatch.model_json_schema()
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "system",
                "content": "Extract requirements from the operator's message as JSON "
                "matching the schema. Omit anything not stated or clearly implied.",
            },
            {"role": "user", "content": text},
        ]
        assert_clean(messages)
        result = await self.provider.generate(messages, schema=schema)
        if not result.data:
            return None
        try:
            return IntentPatch(**result.data).as_updates()
        except Exception as exc:  # noqa: BLE001 - model output is never trusted
            log.warning("discarding malformed LLM intent patch: %s", exc)
            return None

    async def _respond(
        self, conversation: Conversation, user: str
    ) -> tuple[Conversation, Recommendation | None]:
        provided = set(conversation.provided)
        questions = self.wizard.next_questions(conversation.intent, provided)
        conversation.pending_questions = questions
        conversation.ready = self.wizard.ready(conversation.intent, provided)

        recommendation: Recommendation | None = None
        if conversation.ready:
            recommendation = await self.engine.recommend(
                conversation.intent,
                conversation_id=conversation.id,
                open_questions=self.wizard.all_questions(conversation.intent, provided),
            )
            await self.repositories.recommendations.save(recommendation)
            conversation.recommendation_id = recommendation.id
            await self.audit.record(
                action=AuditAction.AI_RECOMMENDATION,
                user=user,
                actor_kind=ActorKind.AI,
                conversation=conversation.id,
                recommendation=recommendation.id,
                resources=_resource_digest(recommendation),
                message=recommendation.summary,
            )
            await self.audit.record(
                action=AuditAction.POLICY_EVALUATION,
                user=user,
                actor_kind=ActorKind.SYSTEM,
                conversation=conversation.id,
                recommendation=recommendation.id,
                detail={
                    "engine": recommendation.policy_engine,
                    "viable": [option.option for option in recommendation.options],
                    "blocked": {
                        option.option: option.blocked_by
                        for option in recommendation.rejected_options
                    },
                },
                message=f"policy engine '{recommendation.policy_engine}' evaluated "
                f"{len(recommendation.options) + len(recommendation.rejected_options)} runtimes",
            )
            text = await self._speak_recommendation(conversation, recommendation)
            conversation.add(
                "assistant",
                text,
                recommendation_id=recommendation.id,
                kind="recommendation",
            )
        else:
            text = await self._speak_questions(conversation, questions)
            conversation.add(
                "assistant",
                text,
                questions=[question.model_dump() for question in questions],
                kind="questions",
            )
            await self.audit.record(
                action=AuditAction.AI_QUESTION,
                user=user,
                actor_kind=ActorKind.AI,
                conversation=conversation.id,
                detail={"fields": [question.field for question in questions]},
                message=text[:200],
            )

        await self.repositories.conversations.save(conversation)
        return conversation, recommendation

    async def _speak_questions(
        self, conversation: Conversation, questions: list[WizardQuestion]
    ) -> str:
        inventory = await self.inventory.get()
        payload = {
            "task": "ask" if questions else "acknowledge",
            "intent": conversation.intent.model_dump(mode="json", exclude_none=True),
            "questions": [question.model_dump() for question in questions],
            "capacity": sanitize_inventory(inventory),
        }
        return await self._say(conversation, payload)

    async def _speak_recommendation(
        self, conversation: Conversation, recommendation: Recommendation
    ) -> str:
        payload = {
            "task": "recommend",
            "intent": conversation.intent.model_dump(mode="json", exclude_none=True),
            "options": [
                {
                    "option": option.option,
                    "title": option.title,
                    "score": option.score,
                    "summary": option.resources.summary(),
                    "reason": option.reason,
                    "risks": option.risks,
                    "native_scheduler": option.native_scheduler,
                }
                for option in recommendation.options
            ],
            "blocked": [
                f"{option.title}: {option.blocked_by[0]}"
                for option in recommendation.rejected_options
                if option.blocked_by
            ],
            "capacity": recommendation.inventory_snapshot,
        }
        return await self._say(conversation, payload)

    async def _say(self, conversation: Conversation, payload: dict[str, Any]) -> str:
        assert_clean(payload)
        history = [
            {"role": message.role, "content": message.content}
            for message in conversation.messages[-8:]
        ]
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *history,
            {"role": "system", "content": context_block(payload)},
        ]
        result = await self.provider.generate(messages)
        return result.text.strip() or "(no response)"


def _resource_digest(recommendation: Recommendation) -> dict[str, Any]:
    top = recommendation.top
    if top is None:
        return {}
    return {
        "runtime": str(top.runtime),
        "gpu": top.resources.total_gpu,
        "gpu_model": top.resources.gpu_model,
        "vcpu": top.resources.total_vcpu,
        "memory_gb": top.resources.total_ram_gb,
        "storage_gb": top.resources.storage_gb,
    }
