"""Copilot chat, wizard and recommendations."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models import (
    ChatRequest,
    Conversation,
    Recommendation,
    WizardAnswers,
    WorkloadIntent,
)
from .deps import ContainerDep, UserDep

router = APIRouter(prefix="/copilot", tags=["copilot"])


@router.get("/conversations", response_model=list[Conversation])
async def list_conversations(container: ContainerDep, user: UserDep):
    return await container.copilot.list()


@router.post("/conversations", response_model=Conversation)
async def create_conversation(container: ContainerDep, user: UserDep):
    return await container.copilot.start()


@router.get("/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str, container: ContainerDep, user: UserDep):
    try:
        return await container.copilot.get(conversation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/messages")
async def send_message(payload: ChatRequest, container: ContainerDep, user: UserDep) -> dict:
    """Main chat turn. Returns the conversation and, when ready, the options."""
    try:
        conversation, recommendation = await container.copilot.handle_message(
            payload.conversation_id, payload.message, user=user.username
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "conversation": conversation.model_dump(mode="json"),
        "recommendation": recommendation.model_dump(mode="json") if recommendation else None,
    }


@router.post("/conversations/{conversation_id}/answers")
async def answer(
    conversation_id: str, payload: WizardAnswers, container: ContainerDep, user: UserDep
) -> dict:
    try:
        conversation, recommendation = await container.copilot.answer(
            conversation_id, payload.answers, user=user.username
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "conversation": conversation.model_dump(mode="json"),
        "recommendation": recommendation.model_dump(mode="json") if recommendation else None,
    }


@router.post("/conversations/{conversation_id}/intent")
async def set_intent(
    conversation_id: str, intent: WorkloadIntent, container: ContainerDep, user: UserDep
) -> dict:
    """Deploy wizard: submit the whole intent at once."""
    try:
        conversation, recommendation = await container.copilot.set_intent(
            conversation_id, intent, user=user.username
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "conversation": conversation.model_dump(mode="json"),
        "recommendation": recommendation.model_dump(mode="json") if recommendation else None,
    }


@router.get("/questions")
async def questions(container: ContainerDep, user: UserDep) -> list[dict]:
    """Every question the wizard can ask, for the Deploy page's form."""
    from ..copilot.wizard import QUESTIONS

    return [spec.build().model_dump() for spec in QUESTIONS]


@router.get("/profiles")
async def profiles(container: ContainerDep, user: UserDep) -> list[dict]:
    return [
        {
            "key": profile.key,
            "display": profile.display,
            "gpu_required": profile.gpu_required,
            "container_compatible": profile.container_compatible,
            "default_isolation": profile.default_isolation,
            "base": profile.base,
            "runtime_affinity": profile.runtime_affinity,
        }
        for profile in container.profiles.all()
    ]


recommendations_router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@recommendations_router.post("", response_model=Recommendation)
async def create_recommendation(
    intent: WorkloadIntent, container: ContainerDep, user: UserDep
):
    """Score an intent directly, without a conversation."""
    recommendation = await container.engine.recommend(intent)
    await container.repositories.recommendations.save(recommendation)
    return recommendation


@recommendations_router.get("", response_model=list[Recommendation])
async def list_recommendations(container: ContainerDep, user: UserDep):
    items = await container.repositories.recommendations.list()
    return sorted(items, key=lambda item: item.created_at, reverse=True)


@recommendations_router.get("/{recommendation_id}", response_model=Recommendation)
async def get_recommendation(recommendation_id: str, container: ContainerDep, user: UserDep):
    try:
        return await container.repositories.recommendations.require(recommendation_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
