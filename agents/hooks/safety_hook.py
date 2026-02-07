from __future__ import annotations

import re
from typing import Any


_DANGEROUS_BASH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-rf\s+/\b"),
    re.compile(r"\brm\s+-rf\s+--no-preserve-root\b"),
    re.compile(r"\bmkfs\.(ext2|ext3|ext4|xfs)\b"),
    re.compile(r"\bdd\s+if=.*\s+of=/dev/\w+"),
]


async def safety_pre_tool_use(
    input_data: dict[str, Any], tool_use_id: str | None, context: Any
) -> dict[str, Any]:
    """Block obviously destructive Bash commands.

    This is intentionally narrow (denylist) to avoid false positives while we bootstrap.
    """

    if input_data.get("hook_event_name") != "PreToolUse":
        return {}

    tool_name = input_data.get("tool_name")
    tool_input = input_data.get("tool_input") or {}
    if tool_name != "Bash":
        return {}

    command = str(tool_input.get("command") or "")
    for pat in _DANGEROUS_BASH_PATTERNS:
        if pat.search(command):
            return {
                "hookSpecificOutput": {
                    "hookEventName": input_data.get("hook_event_name"),
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "Destructive command blocked by safety hook",
                }
            }

    return {}
