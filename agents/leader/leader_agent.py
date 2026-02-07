from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from uuid import uuid4

from agents.base_agent import BaseAgent
from agents.config.settings import Settings
from shared.directives import parse_leader_directives, strip_directive_block
from agents.leader.planner import plan_tasks
from shared.git_helpers import init_bare_repo, merge_branch
from shared.protocol import AgentMessage, Task, TaskAssignment
from shared.protocol import InterAgentMessage, WorkerRequest


class LeaderAgent(BaseAgent):
    def __init__(self, agent_id: str, settings: Settings):
        super().__init__(agent_id=agent_id, role="leader", settings=settings)
        self._granted_worker_count: int | None = None
        self._granted_worker_ids: list[str] = []

    async def run(self) -> None:
        assert self.coordinator is not None

        job_id_env = os.environ.get("JOB_ID", "").strip()
        if job_id_env:
            # Ephemeral leader: manages a single job then exits.
            asyncio.create_task(self._listen_completions(job_id_filter=job_id_env))
            asyncio.create_task(self._scheduler_loop(job_id_filter=job_id_env))
            asyncio.create_task(self._inbox_loop(job_id_env))

            job = await self.coordinator.get_job(job_id_env) or {}
            job["job_id"] = job_id_env
            await self._handle_job(job)

            while not self.shutdown_requested:
                j = await self.coordinator.get_job(job_id_env)
                status = (j or {}).get("status") or ""
                if status in {"completed", "cancelled", "failed"}:
                    return
                await asyncio.sleep(2)

        # Pool leader: consumes jobs from the stream.
        await self.coordinator.ensure_consumer_group("leaders")
        asyncio.create_task(self._listen_completions(job_id_filter=None))
        asyncio.create_task(self._scheduler_loop(job_id_filter=None))

        while not self.shutdown_requested:
            messages = await self.coordinator.read_jobs(
                "leaders", self.agent_id, block_ms=5000
            )
            if not messages:
                continue

            for _stream_name, entries in messages:
                for entry_id, data in entries:
                    raw = data.get(b"data")
                    if not raw:
                        await self.coordinator.ack_job("leaders", entry_id)
                        continue

                    job = json.loads(raw.decode("utf-8"))

                    # If an orchestrator is managing ephemeral crews, skip orchestrated jobs.
                    try:
                        jid = str(job.get("job_id") or job.get("id") or "")
                        if jid:
                            meta = await self.coordinator.get_job(jid)
                            flag = str((meta or {}).get("orchestrated") or "").lower()
                            if flag in {"true", "1", "yes"}:
                                await self.coordinator.ack_job("leaders", entry_id)
                                continue
                    except Exception:
                        pass

                    await self._handle_job(job)
                    await self.coordinator.ack_job("leaders", entry_id)

    async def _handle_job(self, job: dict) -> None:
        assert self.coordinator is not None

        job_id = str(job.get("job_id") or job.get("id") or uuid4())
        raw_prompt = str(job.get("prompt") or "")
        directives = parse_leader_directives(raw_prompt)
        prompt = strip_directive_block(raw_prompt)
        workspace_url = job.get("workspace_url")

        self.current_job_id = job_id
        await self.coordinator.upsert_job(
            job_id,
            {
                "job_id": job_id,
                "prompt": prompt,
                "status": "planning",
                "workspace_url": workspace_url or "",
                "directives": {
                    "max_tasks": directives.max_tasks,
                    "personas": directives.personas,
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        init_bare_repo(job_id, workspace_url)

        task_ids: list[str] = []

        # If the SDK can't be used (no key / out of quota), still allow the system to run end-to-end.
        use_sdk = self.settings.sdk_mode.lower() == "live" and bool(
            os.environ.get("ANTHROPIC_API_KEY")
        )
        if not use_sdk:
            max_tasks = directives.max_tasks
            personas = directives.personas

            # Reasonable defaults when user didn't specify.
            if not max_tasks:
                max_tasks = 2
            if not personas:
                personas = ["dev"] * max(1, max_tasks - 1)
                if max_tasks >= 2:
                    personas.append("qa")

            # If user specified personas but also asked for more tasks, pad by repeating the primary implementation persona.
            if len(personas) < max_tasks:
                non_qa = [p for p in personas if p != "qa"]
                primary = non_qa[0] if non_qa else "dev"
                wants_qa = "qa" in personas
                if max_tasks == 1:
                    personas = [primary]
                elif wants_qa:
                    personas = [primary] * (max_tasks - 1) + ["qa"]
                else:
                    personas = [primary] * max_tasks

            personas = personas[: max(1, max_tasks)]

            previous_tid: str | None = None
            first_dev_tid: str | None = None
            for persona in personas:
                if persona == "qa":
                    agent_type = "bash"
                    active_form = "Testing"
                    subject = "QA: tests + verification"
                    suffix = "Role: qa. Add tests, run them, and report failures/fixes."
                else:
                    agent_type = "general"
                    active_form = "Implementing"
                    subject = f"{persona.strip().title()}: implementation"
                    suffix = f"Role: {persona}. Implement your part of the system."

                blocked_by: list[str] = []
                if persona == "qa" and first_dev_tid:
                    blocked_by = [first_dev_tid]
                elif previous_tid:
                    blocked_by = [previous_tid]

                tid = await self.coordinator.create_task(
                    job_id,
                    Task(
                        subject=subject,
                        description=prompt + "\n\n" + suffix,
                        activeForm=active_form,
                        blockedBy=blocked_by,
                    ),
                )
                await self.coordinator.redis.hset(
                    self.coordinator.keys.task_hash(job_id, tid),
                    mapping={
                        b"agent_type": agent_type.encode(),
                        b"persona": persona.encode(),
                    },
                )
                task_ids.append(tid)

                if first_dev_tid is None and persona != "qa":
                    first_dev_tid = tid
                previous_tid = tid
        else:
            try:
                planned = await asyncio.wait_for(
                    plan_tasks(
                        prompt=prompt,
                        model=self.settings.model,
                        max_tasks=directives.max_tasks,
                        personas=directives.personas or None,
                    ),
                    timeout=self.settings.planner_timeout_seconds,
                )
                for pt in planned:
                    tid = await self.coordinator.create_task(
                        job_id,
                        Task(
                            subject=pt.subject,
                            description=pt.description,
                            activeForm=pt.activeForm,
                            # Dependency mapping is not implemented yet; treat planned tasks as independent for now.
                            blocks=[],
                            blockedBy=[],
                        ),
                    )
                    task_ids.append(tid)
                    await self.coordinator.redis.hset(
                        self.coordinator.keys.task_hash(job_id, tid),
                        mapping={
                            b"agent_type": (pt.agent_type or "general").encode(),
                            b"persona": (pt.persona or "").strip().lower().encode(),
                        },
                    )
            except Exception:
                tid = await self.coordinator.create_task(
                    job_id,
                    Task(
                        subject="Execute job",
                        description=prompt or "(empty prompt)",
                        activeForm="Working",
                    ),
                )
                task_ids.append(tid)
                await self.coordinator.redis.hset(
                    self.coordinator.keys.task_hash(job_id, tid),
                    mapping={b"agent_type": b"general"},
                )
        await self.coordinator.set_job_status(job_id, "in_progress")
        try:
            await self.coordinator.redis.sadd(b"jobs:active", job_id.encode())
        except Exception:
            pass

        # Request workers from orchestrator (ephemeral mode only).
        if os.environ.get("JOB_ID") and task_ids:
            max_cap = int(os.environ.get("MAX_WORKERS_PER_JOB", "6"))
            desired_workers = min(len(task_ids), max_cap)
            if desired_workers > 0:
                try:
                    req = WorkerRequest(
                        job_id=job_id,
                        leader_id=self.agent_id,
                        requested_count=desired_workers,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    )
                    await self.coordinator.submit_worker_request(req)
                    print(
                        f"[leader] requested {desired_workers} workers for job_id={job_id}",
                        flush=True,
                    )
                    await self._wait_for_workers(job_id, desired_workers, timeout=60)
                except Exception as e:
                    print(
                        f"[leader] worker request failed: {e}",
                        flush=True,
                    )

        await self._assign_available(job_id)

        await self.coordinator.publish_job_event(
            job_id,
            {"type": "job_planned", "job_id": job_id, "task_ids": task_ids},
        )

    async def _wait_for_workers(
        self, job_id: str, desired: int, timeout: int = 60
    ) -> None:
        """Poll until enough workers register as idle, or timeout."""
        assert self.coordinator is not None
        target = desired
        elapsed = 0
        while elapsed < timeout:
            # If the orchestrator responded with a lower grant, adjust target.
            if self._granted_worker_count is not None:
                target = self._granted_worker_count
            idle = await self.coordinator.get_idle_workers(job_id=job_id)
            if len(idle) >= target:
                print(
                    f"[leader] {len(idle)} workers ready for job_id={job_id}",
                    flush=True,
                )
                return
            await asyncio.sleep(1)
            elapsed += 1
        # Proceed with whatever workers showed up.
        idle = await self.coordinator.get_idle_workers(job_id=job_id)
        print(
            f"[leader] timeout waiting for workers, proceeding with {len(idle)} "
            f"for job_id={job_id}",
            flush=True,
        )

    async def _assign_available(self, job_id: str) -> None:
        assert self.coordinator is not None
        unblocked = await self.coordinator.get_unblocked_tasks(job_id)
        idle_workers = await self.coordinator.get_idle_workers(
            job_id=(job_id if os.environ.get("JOB_ID") else None)
        )
        for task, worker_id in zip(unblocked, idle_workers):
            raw_agent_type = await self.coordinator.redis.hget(
                self.coordinator.keys.task_hash(job_id, task.id), b"agent_type"
            )
            raw_persona = await self.coordinator.redis.hget(
                self.coordinator.keys.task_hash(job_id, task.id), b"persona"
            )
            agent_type = (
                raw_agent_type.decode("utf-8")
                if isinstance(raw_agent_type, (bytes, bytearray))
                else "general"
            )
            persona = (
                raw_persona.decode("utf-8")
                if isinstance(raw_persona, (bytes, bytearray))
                else ""
            )
            assignment = TaskAssignment(
                job_id=job_id,
                taskId=task.id,
                subject=task.subject,
                description=task.description,
                assignedBy=self.agent_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                agent_type=agent_type,
                persona=persona,
            )
            msg = AgentMessage(
                from_agent=self.agent_id,
                text=assignment.model_dump_json(),
                summary=f"Assigned: {task.subject}",
            )
            # Pool mode uses a control inbox; ephemeral mode sends directly to the job-scoped inbox.
            scope = job_id if os.environ.get("JOB_ID") else "control"
            await self.coordinator.send_message(scope, worker_id, msg)
            await self.coordinator.publish_job_event(
                job_id,
                {"type": "task_assigned", "task_id": task.id, "worker": worker_id},
            )

    async def _listen_completions(self, job_id_filter: str | None) -> None:
        assert self.coordinator is not None
        pubsub = self.coordinator.redis.pubsub()
        await pubsub.subscribe("channel:tasks:completed")
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            data = msg.get("data")
            if isinstance(data, (bytes, bytearray)):
                data = data.decode("utf-8")
            try:
                payload = json.loads(data)
            except Exception:
                continue

            job_id = payload.get("job_id")
            agent_id = payload.get("agent_id")
            status = payload.get("status") or "completed"
            if not job_id or not agent_id:
                continue

            if job_id_filter and job_id != job_id_filter:
                continue

            if status == "completed":
                branch = f"job/{job_id}/{agent_id}"
                try:
                    merge_branch(job_id, branch)
                    await self.coordinator.publish_job_event(
                        job_id, {"type": "branch_merged", "branch": branch}
                    )
                except Exception as e:
                    await self.coordinator.publish_job_event(
                        job_id,
                        {"type": "merge_failed", "branch": branch, "error": str(e)},
                    )

            await self._assign_available(job_id)

            tasks = await self.coordinator.list_tasks(job_id)
            if tasks and all(t.status in {"completed", "failed"} for t in tasks):
                await self.coordinator.set_job_status(job_id, "completed")
                try:
                    await self.coordinator.redis.srem(b"jobs:active", job_id.encode())
                except Exception:
                    pass

    async def _scheduler_loop(self, job_id_filter: str | None) -> None:
        assert self.coordinator is not None
        while not self.shutdown_requested:
            try:
                if job_id_filter:
                    await self._assign_available(job_id_filter)
                else:
                    raw_jobs = await self.coordinator.redis.smembers(b"jobs:active")
                    for raw in raw_jobs:
                        jid = (
                            raw.decode("utf-8")
                            if isinstance(raw, (bytes, bytearray))
                            else str(raw)
                        )
                        await self._assign_available(jid)
            except Exception:
                pass
            await asyncio.sleep(2)

    async def _inbox_loop(self, job_id: str) -> None:
        assert self.coordinator is not None
        while not self.shutdown_requested:
            msg = await self.coordinator.wait_for_message(
                job_id, self.agent_id, timeout=5
            )
            if not msg:
                continue
            try:
                payload = json.loads(msg.text)
            except Exception:
                continue
            if payload.get("type") == "worker_request_response":
                self._granted_worker_count = payload.get("granted_count", 0)
                self._granted_worker_ids = payload.get("worker_ids", [])
                print(
                    f"[leader] orchestrator granted {self._granted_worker_count} workers",
                    flush=True,
                )
            elif payload.get("type") == "agent_message":
                imsg = InterAgentMessage.model_validate(payload)
                await self.coordinator.publish_job_event(
                    job_id,
                    {
                        "type": "agent_message_received",
                        "from": imsg.from_agent,
                        "to": imsg.to_agent,
                        "text": imsg.text,
                    },
                )
                print(
                    f"[msg] from={imsg.from_agent} to={imsg.to_agent}: {imsg.text}",
                    flush=True,
                )
