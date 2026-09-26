"""MockProvider -- mandatory, and the default.

It renders the Copilot's replies from the *same* structured context a real
model would receive, using deterministic templates. That has two benefits:

1. the entire POC is demonstrable with no external service,
2. the conversation text can never contradict the Governor, because both read
   the same context object.
"""

from __future__ import annotations

from typing import Any

from .base import LLMProvider, LLMResult, Message, parse_context


class MockProvider(LLMProvider):
    name = "mock"
    model = "deterministic-templates"

    async def generate(
        self, messages: list[Message], schema: dict[str, Any] | None = None
    ) -> LLMResult:
        context = parse_context(messages)
        if schema is not None:
            # Structured extraction is handled deterministically by
            # `copilot.extraction`; the mock adds nothing on top.
            return LLMResult(data={}, provider=self.name, model=self.model)
        task = context.get("task", "acknowledge")
        renderer = {
            "ask": self._ask,
            "recommend": self._recommend,
            "acknowledge": self._acknowledge,
            "explain": self._explain,
            "post_deploy": self._post_deploy,
            "remediation": self._remediation,
        }.get(task, self._acknowledge)
        return LLMResult(text=renderer(context), provider=self.name, model=self.model)

    # -- templates -----------------------------------------------------------

    def _ask(self, ctx: dict[str, Any]) -> str:
        intent = ctx.get("intent", {})
        questions = ctx.get("questions", [])
        lines: list[str] = []
        understood = self._understood(intent)
        if understood:
            lines.append(f"Understood: {understood}.")
        if len(questions) == 1:
            question = questions[0]
            lines.append(question["question"])
            lines.append(f"_Why I ask: {question['why']}_")
        else:
            lines.append("A couple of details decide the architecture:")
            for index, question in enumerate(questions, start=1):
                lines.append(f"{index}. {question['question']} - _{question['why']}_")
        return "\n\n".join(lines)

    def _recommend(self, ctx: dict[str, Any]) -> str:
        options = ctx.get("options", [])
        intent = ctx.get("intent", {})
        if not options:
            blocked = ctx.get("blocked", [])
            body = "\n".join(f"- {reason}" for reason in blocked[:4])
            return (
                "I could not find a runtime that satisfies this request with the current "
                f"capacity and policies.\n\n{body}\n\n"
                "Relax one of the constraints, or free capacity, and ask me again."
            )

        top = options[0]
        lines = [
            f"Based on {self._understood(intent) or 'your request'}, I checked live capacity "
            f"and evaluated {len(options)} viable option(s)."
        ]
        lines.append(
            f"**Recommended - {top['title']} (score {top['score']:.0f}/100)**\n{top['summary']}"
        )
        for reason in top.get("reason", [])[:4]:
            lines.append(f"- {reason}")
        if len(options) > 1:
            alt = options[1]
            lines.append(
                f"**Alternative - {alt['title']} (score {alt['score']:.0f})**\n{alt['summary']}"
            )
            for reason in alt.get("reason", [])[:2]:
                lines.append(f"- {reason}")
        lines.append(
            f"Node placement stays with {top.get('native_scheduler', 'the platform scheduler')}. "
            "Nothing is deployed until you approve - review the options and pick one."
        )
        return "\n\n".join(lines)

    def _explain(self, ctx: dict[str, Any]) -> str:
        option = ctx.get("option", {})
        reasons = "\n".join(f"- {r}" for r in option.get("reason", []))
        risks = "\n".join(f"- {r}" for r in option.get("risks", []))
        parts = [f"**{option.get('title', 'Option')}** - {option.get('summary', '')}", reasons]
        if risks:
            parts.append(f"Risks:\n{risks}")
        return "\n\n".join(part for part in parts if part)

    def _acknowledge(self, ctx: dict[str, Any]) -> str:
        intent = ctx.get("intent", {})
        understood = self._understood(intent)
        if understood:
            return (
                f"Got it: {understood}. Let me check what the platform has available and "
                "put some options together."
            )
        return (
            "Tell me what you want to run and I will size it, check capacity and policy, "
            "and propose options. For example: \"Deploy a private Llama inference service "
            "for 50 employees with confidential data\"."
        )

    def _post_deploy(self, ctx: dict[str, Any]) -> str:
        advice = ctx.get("advice", {})
        observations = "\n".join(f"- {line}" for line in advice.get("observation", []))
        return (
            f"**{advice.get('title', 'Observation')}**\n\n{observations}\n\n"
            f"{advice.get('recommendation', '')}\n\nI will not change anything on my own - "
            "simulate it first, or ignore it."
        )

    def _remediation(self, ctx: dict[str, Any]) -> str:
        alert = ctx.get("alert", {})
        options = "\n".join(
            f"- **{option['key']}.** {option['title']} - {option['impact']}"
            for option in alert.get("options", [])
        )
        return (
            f"**{alert.get('alertname')}** on `{alert.get('resource')}`\n\n"
            f"{alert.get('summary')}\n\nPossible actions:\n{options}\n\n"
            f"Recommended: **{alert.get('recommended_option')}**. Your call."
        )

    @staticmethod
    def _understood(intent: dict[str, Any]) -> str:
        bits: list[str] = []
        workload = intent.get("workload_type")
        if workload and workload != "generic":
            bits.append(str(workload).replace("-", " "))
        if intent.get("expected_users"):
            bits.append(f"{intent['expected_users']} users")
        if intent.get("concurrent_users"):
            bits.append(f"{intent['concurrent_users']} concurrent")
        if intent.get("environment"):
            bits.append(f"{intent['environment']} environment")
        if intent.get("sensitive_data") and intent["sensitive_data"] != "internal":
            bits.append(f"{intent['sensitive_data']} data")
        if intent.get("gpu_required"):
            bits.append("GPU required")
        return ", ".join(bits)
