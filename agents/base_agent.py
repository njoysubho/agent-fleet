from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod

from agents.config.settings import Settings
from shared.redis_client import RedisCoordinator


class BaseAgent(ABC):
    def __init__(self, agent_id: str, role: str, settings: Settings):
        self.agent_id = agent_id
        self.role = role
        self.settings = settings
        self.coordinator: RedisCoordinator | None = None

        self.current_job_id: str = ""
        self.current_task_id: str = ""
        self.current_status: str = "idle"
        self.shutdown_requested: bool = False

    async def start(self) -> None:
        env_job_id = os.environ.get("JOB_ID", "").strip()
        if env_job_id:
            self.current_job_id = env_job_id
        self.coordinator = RedisCoordinator(self.settings.redis_url)
        await self.coordinator.register_agent(
            self.agent_id,
            {
                "name": self.agent_id,
                "role": self.role,
                "model": self.settings.model,
                "status": self.current_status,
                "job_id": self.current_job_id,
                "current_task": self.current_task_id,
            },
        )
        asyncio.create_task(self._heartbeat_loop())
        await self.run()

    async def _heartbeat_loop(self) -> None:
        assert self.coordinator is not None
        while not self.shutdown_requested:
            await self.coordinator.heartbeat(
                self.agent_id, self.current_status, self.current_task_id
            )
            await asyncio.sleep(self.settings.heartbeat_interval_seconds)

    @abstractmethod
    async def run(self) -> None:
        raise NotImplementedError
