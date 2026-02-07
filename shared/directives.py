from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LeaderDirectives:
    max_tasks: int | None
    personas: list[str]


_MAX_TASKS_RE = re.compile(
    r"(?im)^(?:max[_ ]tasks|task[_ ]count|tasks)\s*[:=]\s*(\d+)\s*$"
)
_MAX_TASKS_INLINE_RE = re.compile(r"(?i)\b(\d+)\s+tasks\b")
_PERSONAS_RE = re.compile(r"(?im)^(?:personas|roles|team)\s*[:=]\s*(.+?)\s*$")
_PERSONAS_INLINE_RE = re.compile(
    r"(?i)\b(?:having|with)\s+(?:an?\s+)?([a-zA-Z][a-zA-Z0-9_-]{1,20})\s+and\s+([a-zA-Z][a-zA-Z0-9_-]{1,20})\b"
)

_BLOCK_RE = re.compile(r"(?is)\[agentfleet\](.*?)\[/agentfleet\]")


def parse_leader_directives(prompt: str) -> LeaderDirectives:
    block_match = _BLOCK_RE.search(prompt)
    scope = block_match.group(1) if block_match else prompt

    max_tasks: int | None = None
    m = _MAX_TASKS_RE.search(scope)
    if m:
        try:
            max_tasks = int(m.group(1))
        except Exception:
            max_tasks = None
        if max_tasks is not None and max_tasks <= 0:
            max_tasks = None
    if max_tasks is None:
        mi = _MAX_TASKS_INLINE_RE.search(scope)
        if mi:
            try:
                max_tasks = int(mi.group(1))
            except Exception:
                max_tasks = None

    personas: list[str] = []
    p = _PERSONAS_RE.search(scope)
    if p:
        raw = p.group(1)
        parts = [x.strip() for x in raw.split(",") if x.strip()]
        if len(parts) <= 1:
            parts = [x.strip() for x in raw.split() if x.strip()]
        seen: set[str] = set()
        for item in parts:
            norm = item.strip().lower()
            if not norm or norm in seen:
                continue
            seen.add(norm)
            personas.append(norm)

    if not personas:
        inline = _PERSONAS_INLINE_RE.search(scope)
        if inline:
            a = inline.group(1).strip().lower()
            b = inline.group(2).strip().lower()
            if a and b and a != b:
                personas = [a, b]

    return LeaderDirectives(max_tasks=max_tasks, personas=personas)


def strip_directive_block(prompt: str) -> str:
    return _BLOCK_RE.sub("", prompt).strip()
