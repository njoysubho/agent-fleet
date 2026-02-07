from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    redis_url: str
    model: str
    heartbeat_interval_seconds: float
    permission_mode: str
    max_turns: int
    max_budget_usd: float
    sdk_mode: str
    planner_timeout_seconds: float

    @staticmethod
    def from_env() -> "Settings":
        return Settings(
            redis_url=os.environ.get("REDIS_URL", "redis://redis:6379/0"),
            model=os.environ.get("MODEL", ""),
            heartbeat_interval_seconds=float(os.environ.get("HEARTBEAT_INTERVAL", "5")),
            permission_mode=os.environ.get(
                "AGENT_PERMISSION_MODE", "bypassPermissions"
            ),
            max_turns=int(os.environ.get("AGENT_MAX_TURNS", "30")),
            max_budget_usd=float(os.environ.get("AGENT_MAX_BUDGET_USD", "2.0")),
            sdk_mode=os.environ.get("AGENT_SDK_MODE", "live"),
            planner_timeout_seconds=float(
                os.environ.get("AGENT_PLANNER_TIMEOUT", "30")
            ),
        )
