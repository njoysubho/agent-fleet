from __future__ import annotations

import asyncio
import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone

from shared.redis_client import RedisCoordinator
from shared.protocol import AgentMessage, WorkerRequestResponse

import docker


@dataclass(frozen=True)
class OrchestratorSettings:
    redis_url: str
    agents_image: str
    docker_network: str
    repo_volume: str
    max_workers_per_job: int
    cleanup_delay_seconds: float

    @staticmethod
    def from_env() -> "OrchestratorSettings":
        return OrchestratorSettings(
            redis_url=os.environ.get("REDIS_URL", "redis://redis:6379/0"),
            agents_image=os.environ.get("AGENTS_IMAGE", "agentfleet-agents:latest"),
            docker_network=os.environ.get("DOCKER_NETWORK", "agentfleet_default"),
            repo_volume=os.environ.get("REPO_VOLUME", "agentfleet_repo"),
            max_workers_per_job=int(os.environ.get("MAX_WORKERS_PER_JOB", "6")),
            cleanup_delay_seconds=float(os.environ.get("CLEANUP_DELAY_SECONDS", "0")),
        )


class Orchestrator:
    def __init__(self, settings: OrchestratorSettings):
        self.settings = settings
        self.coordinator = RedisCoordinator(settings.redis_url)
        self.docker_client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
        self.hostname = socket.gethostname()

    async def start(self) -> None:
        await self.coordinator.ensure_consumer_group("orchestrators")

        # Launch the worker-request listener alongside the job stream consumer.
        asyncio.create_task(self._worker_request_loop())

        while True:
            messages = await self.coordinator.read_jobs(
                "orchestrators", consumer=f"orch-{self.hostname[:8]}", block_ms=5000
            )
            if not messages:
                continue

            for _stream, entries in messages:
                for entry_id, data in entries:
                    raw = data.get(b"data")
                    if not raw:
                        await self.coordinator.ack_job("orchestrators", entry_id)
                        continue

                    try:
                        job = json.loads(raw.decode("utf-8"))
                    except Exception:
                        await self.coordinator.ack_job("orchestrators", entry_id)
                        continue

                    try:
                        await self._spawn_leader(job)
                        await self.coordinator.ack_job("orchestrators", entry_id)
                    except Exception as e:
                        job_id = str(job.get("job_id") or job.get("id") or "")
                        if job_id:
                            await self.coordinator.publish_job_event(
                                job_id,
                                {"type": "orchestrator_error", "error": str(e)},
                            )
                        await self.coordinator.ack_job("orchestrators", entry_id)

    async def _spawn_leader(self, job: dict) -> None:
        job_id = str(job.get("job_id") or job.get("id") or "")
        if not job_id:
            raise RuntimeError("job_id missing")

        prompt = str(job.get("prompt") or "")

        await self.coordinator.upsert_job(
            job_id,
            {
                "job_id": job_id,
                "prompt": prompt,
                "status": "queued",
                "orchestrated": True,
            },
        )

        leader_id = f"leader-{job_id[:8]}"

        print(
            f"[orchestrator] spawning leader for job_id={job_id}",
            flush=True,
        )

        base_env = {
            "REDIS_URL": self.settings.redis_url,
            "AGENT_SDK_MODE": os.environ.get("AGENT_SDK_MODE", "live"),
            "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
            "MODEL": os.environ.get("MODEL", ""),
            "DRY_RUN_SLEEP_SECONDS": os.environ.get("DRY_RUN_SLEEP_SECONDS", "0"),
            "JOB_ID": job_id,
            "JOB_MODE": "ephemeral",
            "MAX_WORKERS_PER_JOB": str(self.settings.max_workers_per_job),
        }

        self._run_container(
            name=f"agentfleet-{job_id[:8]}-leader",
            env={
                **base_env,
                "AGENT_ROLE": "leader",
                "AGENT_ID": leader_id,
            },
            labels={"agentteam.job_id": job_id, "agentteam.role": "leader"},
        )

        await self.coordinator.publish_job_event(
            job_id,
            {
                "type": "leader_spawned",
                "job_id": job_id,
                "leader_id": leader_id,
            },
        )

        # Background cleanup: when job completes, stop/remove crew containers.
        asyncio.create_task(self._cleanup_when_done(job_id))

    async def _worker_request_loop(self) -> None:
        """Long-running loop: receives WorkerRequests from leaders, spawns workers."""
        while True:
            try:
                request = await self.coordinator.wait_for_worker_request(timeout=5)
                if not request:
                    continue

                job_id = request.job_id
                leader_id = request.leader_id
                requested = request.requested_count

                # Enforce cap.
                granted = min(requested, self.settings.max_workers_per_job)

                print(
                    f"[orchestrator] spawning {granted} workers for job_id={job_id} "
                    f"(requested={requested})",
                    flush=True,
                )

                base_env = {
                    "REDIS_URL": self.settings.redis_url,
                    "AGENT_SDK_MODE": os.environ.get("AGENT_SDK_MODE", "live"),
                    "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
                    "MODEL": os.environ.get("MODEL", ""),
                    "DRY_RUN_SLEEP_SECONDS": os.environ.get(
                        "DRY_RUN_SLEEP_SECONDS", "0"
                    ),
                    "JOB_ID": job_id,
                    "JOB_MODE": "ephemeral",
                }

                worker_ids: list[str] = []
                for i in range(granted):
                    worker_id = f"worker-{job_id[:8]}-{i}"
                    worker_ids.append(worker_id)
                    self._run_container(
                        name=f"agentfleet-{job_id[:8]}-worker-{i}",
                        env={
                            **base_env,
                            "AGENT_ROLE": "worker",
                            "AGENT_ID": worker_id,
                        },
                        labels={
                            "agentteam.job_id": job_id,
                            "agentteam.role": "worker",
                        },
                    )

                # Send response to leader's inbox.
                response = WorkerRequestResponse(
                    job_id=job_id,
                    granted_count=granted,
                    worker_ids=worker_ids,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
                msg = AgentMessage(
                    from_agent="orchestrator",
                    text=response.model_dump_json(),
                    summary=f"Granted {granted} workers",
                )
                await self.coordinator.send_message(job_id, leader_id, msg)

                await self.coordinator.publish_job_event(
                    job_id,
                    {
                        "type": "workers_spawned",
                        "job_id": job_id,
                        "leader_id": leader_id,
                        "granted": granted,
                        "worker_ids": worker_ids,
                    },
                )

            except Exception as e:
                print(f"[orchestrator] worker_request_loop error: {e}", flush=True)
                await asyncio.sleep(1)

    def _run_container(
        self, *, name: str, env: dict[str, str], labels: dict[str, str]
    ) -> None:
        image = self.settings.agents_image
        volumes = {self.settings.repo_volume: {"bind": "/repo", "mode": "rw"}}
        try:
            existing = self.docker_client.containers.get(name)
            try:
                existing.remove(force=True)
            except Exception:
                pass
        except Exception:
            pass

        self.docker_client.containers.run(
            image=image,
            name=name,
            detach=True,
            environment=env,
            network=self.settings.docker_network,
            volumes=volumes,
            labels=labels,
            restart_policy={"Name": "no"},
        )

    async def _cleanup_when_done(self, job_id: str) -> None:
        # Poll job status in Redis; when terminal, stop/remove containers.
        terminal = {"completed", "cancelled", "failed"}
        while True:
            job = await self.coordinator.get_job(job_id)
            status = (job or {}).get("status") or ""
            if status in terminal:
                break
            await asyncio.sleep(3)

        if self.settings.cleanup_delay_seconds > 0:
            await asyncio.sleep(self.settings.cleanup_delay_seconds)

        prefix = f"agentfleet-{job_id[:8]}-"
        for c in self.docker_client.containers.list(all=True):
            if not c.name.startswith(prefix):
                continue
            try:
                c.remove(force=True)
            except Exception:
                continue

        print(
            f"[orchestrator] cleaned crew job_id={job_id} status={status}",
            flush=True,
        )


async def main() -> None:
    settings = OrchestratorSettings.from_env()
    orch = Orchestrator(settings)
    await orch.start()
