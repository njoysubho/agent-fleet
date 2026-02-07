from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage


@dataclass(frozen=True)
class PlannedTask:
    subject: str
    description: str
    activeForm: str = "Working"
    blocks: list[str] | None = None
    agent_type: str = "general"
    persona: str = ""


async def plan_tasks(
    *,
    prompt: str,
    model: str | None,
    max_budget_usd: float | None = 0.5,
    max_tasks: int | None = None,
    personas: list[str] | None = None,
) -> list[PlannedTask]:
    max_items = max_tasks if (isinstance(max_tasks, int) and max_tasks > 0) else 12
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "minItems": 1,
                "maxItems": max_items,
                "items": {
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string"},
                        "description": {"type": "string"},
                        "activeForm": {"type": "string"},
                        "persona": {"type": "string"},
                        "blocks": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "agent_type": {"type": "string"},
                    },
                    "required": ["subject", "description"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["tasks"],
        "additionalProperties": False,
    }

    persona_hint = ""
    if personas:
        persona_hint = (
            "\n\nPersonas: Assign each task a persona from this list (lowercase): "
            + ", ".join(personas)
            + "."
        )
    count_hint = ""
    if max_tasks and max_tasks > 0:
        count_hint = f"\n\nTask count: Return at most {max_tasks} tasks."

    planner_prompt = (
        "Decompose the following job prompt into a small set of concrete engineering tasks. "
        "Each task should be independently executable by a coding agent in a git worktree. "
        "Return 3-7 tasks when possible. Use blocks only when a task must wait for another task."
        + count_hint
        + persona_hint
        + "\n\nJOB PROMPT:\n"
        + prompt
        + "\n"
    )

    options = ClaudeAgentOptions(
        allowed_tools=[],
        permission_mode="plan",
        model=model or None,
        max_turns=2,
        max_budget_usd=max_budget_usd,
        output_format={"type": "json_schema", "schema": schema},
    )

    last_result: ResultMessage | None = None
    async with ClaudeSDKClient(options=options) as client:
        await client.query(planner_prompt)
        async for message in client.receive_response():
            if isinstance(message, ResultMessage):
                last_result = message

    if not last_result or last_result.is_error:
        raise RuntimeError(last_result.result if last_result else "Planner failed")

    data = last_result.structured_output
    if not isinstance(data, dict) or "tasks" not in data:
        raise RuntimeError("Planner returned invalid structured_output")

    tasks: list[PlannedTask] = []
    for t in data.get("tasks", []) or []:
        if not isinstance(t, dict):
            continue
        tasks.append(
            PlannedTask(
                subject=str(t.get("subject") or ""),
                description=str(t.get("description") or ""),
                activeForm=str(t.get("activeForm") or "Working"),
                blocks=[str(x) for x in (t.get("blocks") or [])],
                agent_type=str(t.get("agent_type") or "general"),
                persona=str(t.get("persona") or ""),
            )
        )

    tasks = [t for t in tasks if t.subject.strip() and t.description.strip()]
    if not tasks:
        raise RuntimeError("Planner produced no tasks")
    return tasks
