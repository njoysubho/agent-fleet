from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk import ResultMessage
from claude_agent_sdk import HookMatcher

from agents.hooks.safety_hook import safety_pre_tool_use


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    result_text: str
    total_cost_usd: float | None = None
    num_turns: int | None = None
    session_id: str | None = None


async def run_agent_task(
    *,
    prompt: str,
    cwd: str,
    allowed_tools: list[str],
    permission_mode: str,
    model: str | None,
    max_turns: int | None,
    max_budget_usd: float | None,
) -> ExecutionResult:
    if os.environ.get("AGENT_SDK_MODE", "live").lower() != "live":
        try:
            sleep_s = float(os.environ.get("DRY_RUN_SLEEP_SECONDS", "0"))
        except Exception:
            sleep_s = 0
        if sleep_s > 0:
            await asyncio.sleep(sleep_s)
        return ExecutionResult(
            ok=True, result_text="dry-run: skipped Agent SDK execution"
        )
    options = ClaudeAgentOptions(
        cwd=cwd,
        allowed_tools=allowed_tools,
        permission_mode=permission_mode,
        model=model or None,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        hooks={
            "PreToolUse": [HookMatcher(matcher="Bash", hooks=[safety_pre_tool_use])],
        },
    )

    last_result: ResultMessage | None = None

    async def _run() -> None:
        nonlocal last_result
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                if isinstance(message, ResultMessage):
                    last_result = message

    timeout_seconds = 600
    try:
        await asyncio.wait_for(_run(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return ExecutionResult(
            ok=False, result_text=f"Timed out after {timeout_seconds}s"
        )

    if last_result is None:
        return ExecutionResult(ok=False, result_text="No ResultMessage received")

    ok = not bool(last_result.is_error)
    return ExecutionResult(
        ok=ok,
        result_text=last_result.result or "",
        total_cost_usd=last_result.total_cost_usd,
        num_turns=last_result.num_turns,
        session_id=last_result.session_id,
    )
