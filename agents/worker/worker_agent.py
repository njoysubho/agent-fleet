from __future__ import annotations

import asyncio
import json
import os

from agents.base_agent import BaseAgent
from agents.config.settings import Settings
from agents.config.agent_types import AGENT_TYPE_TOOLS
from agents.worker.executor import run_agent_task
from shared.git_helpers import commit_and_push, create_worktree, cleanup_worktree
from shared.protocol import InterAgentMessage, ShutdownRequest, TaskAssignment


class WorkerAgent(BaseAgent):
    def __init__(self, agent_id: str, settings: Settings):
        super().__init__(agent_id=agent_id, role="worker", settings=settings)
        self._message_buffer: list[str] = []
        self._message_lock = asyncio.Lock()

    async def run(self) -> None:
        assert self.coordinator is not None

        job_id = os.environ.get("JOB_ID", "").strip()

        pending: list[TaskAssignment] = []
        inflight: asyncio.Task[None] | None = None

        # Workers wait until they receive a task assignment.
        while not self.shutdown_requested:
            if inflight is not None and inflight.done():
                inflight = None

            # Without a job_id, we cannot BRPOP a job-scoped inbox; receive assignments via a control inbox.
            inbox_scope = job_id or "control"
            msg = await self.coordinator.wait_for_message(
                inbox_scope, self.agent_id, timeout=5
            )
            if not msg:
                if job_id:
                    try:
                        job = await self.coordinator.get_job(job_id)
                        status = (job or {}).get("status") or ""
                        if status in {"completed", "cancelled", "failed"}:
                            return
                    except Exception:
                        pass

                if pending and inflight is None:
                    next_a = pending.pop(0)
                    inflight = asyncio.create_task(self._execute_task(next_a))
                continue

            try:
                payload = json.loads(msg.text)
            except Exception:
                continue

            if payload.get("type") == "shutdown_request":
                _ = ShutdownRequest.model_validate(payload)
                self.shutdown_requested = True
                return

            if payload.get("type") == "agent_message":
                msg = InterAgentMessage.model_validate(payload)
                async with self._message_lock:
                    self._message_buffer.append(f"from={msg.from_agent}: {msg.text}")
                await self.coordinator.publish_job_event(
                    msg.job_id,
                    {
                        "type": "agent_message_received",
                        "from": msg.from_agent,
                        "to": msg.to_agent,
                        "text": msg.text,
                    },
                )
                # Best-effort local visibility.
                print(
                    f"[msg] from={msg.from_agent} to={msg.to_agent}: {msg.text}",
                    flush=True,
                )
                continue

            if payload.get("type") != "task_assignment":
                continue

            assignment = TaskAssignment.model_validate(payload)
            pending.append(assignment)
            if inflight is None and pending:
                next_a = pending.pop(0)
                inflight = asyncio.create_task(self._execute_task(next_a))

    async def _execute_task(self, assignment: TaskAssignment) -> None:
        assert self.coordinator is not None

        job_id = assignment.job_id
        task_id = assignment.taskId

        self.current_job_id = job_id
        self.current_task_id = task_id
        self.current_status = "busy"

        try:
            claimed = await self.coordinator.claim_task(job_id, task_id, self.agent_id)
            if not claimed:
                return

            worktree = create_worktree(job_id, self.agent_id)
            agent_tools = AGENT_TYPE_TOOLS.get(
                assignment.agent_type or "general", AGENT_TYPE_TOOLS["general"]
            )

            persona_prefix = ""
            if (assignment.persona or "").strip():
                persona_prefix = (
                    f"Persona: {assignment.persona.strip()}\n"
                    "Act in this role consistently.\n\n"
                )

            async with self._message_lock:
                buffered = list(self._message_buffer)
                self._message_buffer.clear()

            message_context = ""
            if buffered:
                print(
                    f"[context] applying {len(buffered)} buffered message(s) to task {task_id}",
                    flush=True,
                )
                message_context = (
                    "Recent messages from other agents (use as context for this task):\n"
                    + "\n".join(buffered)
                    + "\n\n"
                )

            result = await run_agent_task(
                prompt=message_context + persona_prefix + assignment.description,
                cwd=str(worktree),
                allowed_tools=agent_tools,
                permission_mode=self.settings.permission_mode,
                model=self.settings.model,
                max_turns=self.settings.max_turns,
                max_budget_usd=self.settings.max_budget_usd,
            )
            commit_and_push(worktree, f"task/{task_id}: {assignment.subject}")

            if result.ok:
                await self.coordinator.complete_task(
                    job_id, task_id, self.agent_id, result=result.result_text
                )
            else:
                await self.coordinator.fail_task(
                    job_id, task_id, self.agent_id, error=result.result_text
                )
        except Exception as e:
            await self.coordinator.fail_task(
                job_id, task_id, self.agent_id, error=str(e)
            )
        finally:
            try:
                cleanup_worktree(job_id, self.agent_id)
            except Exception:
                pass
            self.current_task_id = ""
            self.current_status = "idle"
