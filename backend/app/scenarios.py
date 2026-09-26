"""Predefined demo scenarios (section 27).

Each scenario replays a scripted conversation through the *real* Copilot,
Governor and policy engine -- no shortcuts. That makes them both a demo button
in the UI and the end-to-end regression fixtures used by the test-suite.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .copilot import CopilotService
from .models import Conversation, Recommendation

log = logging.getLogger(__name__)


class Scenario(BaseModel):
    key: str
    title: str
    summary: str = ""
    prompt: str
    answers: dict[str, Any] = Field(default_factory=dict)
    expected_runtime: str | None = None
    expected_reason_contains: list[str] = Field(default_factory=list)


class ScenarioResult(BaseModel):
    scenario: Scenario
    conversation: Conversation
    recommendation: Recommendation | None = None
    matched_expectation: bool | None = None
    recommended_runtime: str | None = None


class ScenarioService:
    def __init__(self, copilot: CopilotService, scenarios_file: Path) -> None:
        self.copilot = copilot
        self.scenarios_file = Path(scenarios_file)
        self._scenarios: list[Scenario] | None = None

    def list(self) -> list[Scenario]:
        if self._scenarios is None:
            if not self.scenarios_file.exists():
                log.warning("scenarios file %s not found", self.scenarios_file)
                self._scenarios = []
            else:
                raw = yaml.safe_load(self.scenarios_file.read_text(encoding="utf-8")) or {}
                self._scenarios = [
                    Scenario(**entry) for entry in raw.get("scenarios", [])
                ]
        return list(self._scenarios)

    def get(self, key: str) -> Scenario:
        for scenario in self.list():
            if scenario.key.lower() == key.lower():
                return scenario
        raise KeyError(f"unknown scenario '{key}'")

    async def run(self, key: str, user: str = "admin") -> ScenarioResult:
        """Play the scenario up to the recommendation. Never approves anything."""
        scenario = self.get(key)
        conversation, recommendation = await self.copilot.handle_message(
            None, scenario.prompt, user=user
        )
        if scenario.answers and recommendation is None:
            conversation, recommendation = await self.copilot.answer(
                conversation.id, scenario.answers, user=user
            )
        # Any question still open after the scripted answers: answer what we can.
        guard = 0
        while recommendation is None and conversation.pending_questions and guard < 3:
            guard += 1
            answers = {
                question.field: scenario.answers.get(question.field)
                or _default_answer(question.field)
                for question in conversation.pending_questions
            }
            answers = {k: v for k, v in answers.items() if v is not None}
            if not answers:
                break
            conversation, recommendation = await self.copilot.answer(
                conversation.id, answers, user=user
            )

        matched: bool | None = None
        runtime: str | None = None
        if recommendation is not None and recommendation.top is not None:
            runtime = str(recommendation.top.runtime)
            if scenario.expected_runtime:
                matched = runtime == scenario.expected_runtime
        return ScenarioResult(
            scenario=scenario,
            conversation=conversation,
            recommendation=recommendation,
            recommended_runtime=runtime,
            matched_expectation=matched,
        )


def _default_answer(field: str) -> Any:
    return {
        "environment": "prod",
        "sensitive_data": "internal",
        "concurrent_users": 20,
        "storage_gb": 500,
        "high_availability": False,
        "workload_isolation": "medium",
        "network_exposure": "internal",
        "operating_system": "ubuntu 22.04",
        "gpu_count": 1,
        "workload_type": "web-api",
    }.get(field)
