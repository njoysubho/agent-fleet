from __future__ import annotations

import os
from dataclasses import dataclass

from shared.redis_client import RedisCoordinator


@dataclass
class AppState:
    coordinator: RedisCoordinator


async def build_state() -> AppState:
    coordinator = RedisCoordinator(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    return AppState(coordinator=coordinator)
