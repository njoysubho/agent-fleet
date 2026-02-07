from __future__ import annotations

import asyncio
import os
import socket

from agents.config.settings import Settings
from agents.leader.leader_agent import LeaderAgent
from agents.worker.worker_agent import WorkerAgent


async def _amain() -> None:
    role = os.environ.get("AGENT_ROLE", "worker")
    hostname = socket.gethostname()
    agent_id = os.environ.get("AGENT_ID") or f"{role}-{hostname[:8]}"

    settings = Settings.from_env()
    if role == "orchestrator":
        # Run the per-job crew orchestrator.
        from agents.orchestrator.orchestrator import main as orch_main

        await orch_main()
        return
    if role == "leader":
        agent = LeaderAgent(agent_id=agent_id, settings=settings)
    else:
        agent = WorkerAgent(agent_id=agent_id, settings=settings)

    await agent.start()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
