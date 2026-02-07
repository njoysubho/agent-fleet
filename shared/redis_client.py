from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Iterable

import redis.asyncio as redis

from shared.protocol import AgentMessage, Task, WorkerRequest


@dataclass(frozen=True)
class RedisKeys:
    jobs_stream: str = "stream:jobs"
    agents_active: str = "agents:active"
    worker_request_queue: str = "queue:worker_requests"

    def job_hash(self, job_id: str) -> str:
        return f"job:{job_id}"

    def job_tasks_zset(self, job_id: str) -> str:
        return f"job:{job_id}:tasks"

    def job_next_task_id(self, job_id: str) -> str:
        return f"job:{job_id}:next_task_id"

    def task_hash(self, job_id: str, task_id: str) -> str:
        return f"task:{job_id}:{task_id}"

    def task_blocks(self, job_id: str, task_id: str) -> str:
        return f"task:{job_id}:{task_id}:blocks"

    def task_blocked_by(self, job_id: str, task_id: str) -> str:
        return f"task:{job_id}:{task_id}:blockedBy"

    def task_lock(self, job_id: str, task_id: str) -> str:
        return f"lock:task:{job_id}:{task_id}"

    def inbox(self, job_id: str, agent_name: str) -> str:
        return f"inbox:{job_id}:{agent_name}"

    def agent_hash(self, agent_id: str) -> str:
        return f"agent:{agent_id}"

    def job_events_channel(self, job_id: str) -> str:
        return f"channel:job:{job_id}:events"


class RedisCoordinator:
    """Single class handling all Redis coordination patterns."""

    def __init__(self, redis_url: str):
        self.keys = RedisKeys()
        self.pool = redis.ConnectionPool.from_url(redis_url, decode_responses=False)
        self.redis = redis.Redis(connection_pool=self.pool)

    async def close(self) -> None:
        await self.redis.aclose()
        await self.pool.disconnect(inuse_connections=True)

    # --- Job metadata (Hash) ---
    async def upsert_job(self, job_id: str, data: dict[str, Any]) -> None:
        mapping = {
            k: json.dumps(v).encode()
            if isinstance(v, (dict, list))
            else str(v).encode()
            for k, v in data.items()
        }
        await self.redis.hset(self.keys.job_hash(job_id), mapping=mapping)

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        raw = await self.redis.hgetall(self.keys.job_hash(job_id))
        if not raw:
            return None
        decoded: dict[str, Any] = {}
        for k, v in raw.items():
            key = k.decode()
            val = v.decode()
            if key in {"config", "directives"}:
                try:
                    decoded[key] = json.loads(val)
                    continue
                except Exception:
                    pass
            decoded[key] = val
        return decoded

    async def set_job_status(self, job_id: str, status: str) -> None:
        await self.redis.hset(
            self.keys.job_hash(job_id), mapping={"status": status.encode()}
        )
        await self.publish_job_event(job_id, {"type": "job_status", "status": status})

    # --- Jobs (Stream) ---
    async def submit_job(self, job: dict[str, Any]) -> str:
        job_id = str(job.get("job_id") or job.get("id") or "")
        payload = {"data": json.dumps(job)}
        await self.redis.xadd(self.keys.jobs_stream, payload)
        return job_id

    async def ensure_consumer_group(self, group: str) -> None:
        try:
            await self.redis.xgroup_create(
                self.keys.jobs_stream, group, id="0", mkstream=True
            )
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def read_jobs(
        self,
        group: str,
        consumer: str,
        block_ms: int = 5000,
        count: int = 1,
    ):
        return await self.redis.xreadgroup(
            groupname=group,
            consumername=consumer,
            streams={self.keys.jobs_stream: ">"},
            count=count,
            block=block_ms,
        )

    async def ack_job(self, group: str, entry_id: str) -> None:
        await self.redis.xack(self.keys.jobs_stream, group, entry_id)

    # --- Inboxes (List) ---
    async def send_message(
        self, job_id: str, target_agent: str, message: AgentMessage
    ) -> None:
        await self.redis.lpush(
            self.keys.inbox(job_id, target_agent), message.model_dump_json().encode()
        )

    async def wait_for_message(
        self, job_id: str, agent_name: str, timeout: int = 0
    ) -> AgentMessage | None:
        result = await self.redis.brpop(
            self.keys.inbox(job_id, agent_name), timeout=timeout
        )
        if not result:
            return None
        _, raw = result
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return AgentMessage.model_validate_json(raw)

    async def broadcast(
        self, job_id: str, message: AgentMessage, agent_names: Iterable[str]
    ) -> None:
        pipe = self.redis.pipeline()
        payload = message.model_dump_json().encode()
        for name in agent_names:
            pipe.lpush(self.keys.inbox(job_id, name), payload)
        await pipe.execute()

    # --- Tasks (Hashes/Sets/ZSet + Locks) ---
    async def create_task(self, job_id: str, task: Task) -> str:
        task_id = str(await self.redis.incr(self.keys.job_next_task_id(job_id)))
        task.id = task_id
        mapping: dict[bytes, bytes] = {
            b"id": task_id.encode(),
            b"subject": task.subject.encode(),
            b"description": task.description.encode(),
            b"activeForm": task.activeForm.encode(),
            b"owner": task.owner.encode(),
            b"status": task.status.encode(),
        }
        await self.redis.hset(self.keys.task_hash(job_id, task_id), mapping=mapping)
        await self.redis.zadd(
            self.keys.job_tasks_zset(job_id), {task_id.encode(): time.time()}
        )

        for downstream in task.blocks:
            await self.redis.sadd(
                self.keys.task_blocks(job_id, task_id), downstream.encode()
            )
            await self.redis.sadd(
                self.keys.task_blocked_by(job_id, downstream), task_id.encode()
            )

        for upstream in task.blockedBy:
            await self.redis.sadd(
                self.keys.task_blocked_by(job_id, task_id), upstream.encode()
            )
            await self.redis.sadd(
                self.keys.task_blocks(job_id, upstream), task_id.encode()
            )

        await self.publish_job_event(
            job_id, {"type": "task_created", "task_id": task_id}
        )
        return task_id

    async def get_task(self, job_id: str, task_id: str) -> Task | None:
        data = await self.redis.hgetall(self.keys.task_hash(job_id, task_id))
        if not data:
            return None
        decoded = {k.decode(): v.decode() for k, v in data.items()}
        blocked_by = await self.redis.smembers(
            self.keys.task_blocked_by(job_id, task_id)
        )
        blocks = await self.redis.smembers(self.keys.task_blocks(job_id, task_id))
        decoded["blockedBy"] = sorted([b.decode() for b in blocked_by])
        decoded["blocks"] = sorted([b.decode() for b in blocks])
        return Task.model_validate(decoded)

    async def list_tasks(self, job_id: str) -> list[Task]:
        task_ids = await self.redis.zrange(self.keys.job_tasks_zset(job_id), 0, -1)
        tasks: list[Task] = []
        for raw in task_ids:
            tid = raw.decode() if isinstance(raw, bytes) else str(raw)
            t = await self.get_task(job_id, tid)
            if t:
                tasks.append(t)
        return tasks

    async def claim_task(
        self, job_id: str, task_id: str, agent_id: str, ttl_seconds: int = 300
    ) -> bool:
        claimed = await self.redis.set(
            self.keys.task_lock(job_id, task_id),
            agent_id.encode(),
            nx=True,
            ex=ttl_seconds,
        )
        if claimed:
            await self.redis.hset(
                self.keys.task_hash(job_id, task_id),
                mapping={"status": b"in_progress", "owner": agent_id.encode()},
            )
            await self.publish_job_event(
                job_id,
                {"type": "task_claimed", "task_id": task_id, "agent_id": agent_id},
            )
        return bool(claimed)

    async def _release_task_lock(
        self, job_id: str, task_id: str, agent_id: str
    ) -> None:
        # Atomic check-and-delete.
        script = (
            "if redis.call('get',KEYS[1])==ARGV[1] then "
            "return redis.call('del',KEYS[1]) else return 0 end"
        )
        await self.redis.eval(script, 1, self.keys.task_lock(job_id, task_id), agent_id)

    async def complete_task(
        self, job_id: str, task_id: str, agent_id: str, result: str = ""
    ) -> None:
        await self.redis.hset(
            self.keys.task_hash(job_id, task_id),
            mapping={
                "status": b"completed",
                "result": result.encode(),
                "owner": agent_id.encode(),
            },
        )
        await self._release_task_lock(job_id, task_id, agent_id)

        # Auto-unblock downstream tasks.
        downstream = await self.redis.smembers(self.keys.task_blocks(job_id, task_id))
        for raw in downstream:
            tid = raw.decode() if isinstance(raw, bytes) else str(raw)
            await self.redis.srem(
                self.keys.task_blocked_by(job_id, tid), task_id.encode()
            )

        await self.redis.publish(
            "channel:tasks:completed",
            json.dumps(
                {
                    "job_id": job_id,
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "status": "completed",
                    "result": result,
                }
            ).encode(),
        )
        await self.publish_job_event(
            job_id, {"type": "task_completed", "task_id": task_id, "agent_id": agent_id}
        )

    async def fail_task(
        self, job_id: str, task_id: str, agent_id: str, error: str
    ) -> None:
        await self.redis.hset(
            self.keys.task_hash(job_id, task_id),
            mapping={
                "status": b"failed",
                "result": error.encode(),
                "owner": agent_id.encode(),
            },
        )
        await self._release_task_lock(job_id, task_id, agent_id)
        await self.redis.publish(
            "channel:tasks:completed",
            json.dumps(
                {
                    "job_id": job_id,
                    "task_id": task_id,
                    "agent_id": agent_id,
                    "status": "failed",
                    "result": error,
                }
            ).encode(),
        )
        await self.publish_job_event(
            job_id,
            {
                "type": "task_failed",
                "task_id": task_id,
                "agent_id": agent_id,
                "error": error,
            },
        )

    async def get_unblocked_tasks(self, job_id: str) -> list[Task]:
        task_ids = await self.redis.zrange(self.keys.job_tasks_zset(job_id), 0, -1)
        unblocked: list[Task] = []
        for raw in task_ids:
            tid = raw.decode() if isinstance(raw, bytes) else str(raw)
            data = await self.redis.hgetall(self.keys.task_hash(job_id, tid))
            if not data:
                continue
            status = (data.get(b"status") or b"").decode()
            owner = (data.get(b"owner") or b"").decode()
            if status != "pending" or owner:
                continue
            blockers = await self.redis.smembers(self.keys.task_blocked_by(job_id, tid))
            if blockers:
                continue
            task = await self.get_task(job_id, tid)
            if task:
                unblocked.append(task)
        return unblocked

    # --- Agent registry (Hash + ZSet) ---
    async def register_agent(self, agent_id: str, agent_data: dict[str, Any]) -> None:
        mapping = {k: str(v).encode() for k, v in agent_data.items()}
        await self.redis.hset(self.keys.agent_hash(agent_id), mapping=mapping)
        await self.redis.zadd(self.keys.agents_active, {agent_id.encode(): time.time()})

    async def heartbeat(
        self, agent_id: str, status: str, current_task: str = ""
    ) -> None:
        ts = str(time.time())
        await self.redis.zadd(self.keys.agents_active, {agent_id.encode(): time.time()})
        await self.redis.hset(
            self.keys.agent_hash(agent_id),
            mapping={
                "status": status.encode(),
                "current_task": current_task.encode(),
                "heartbeat_ts": ts.encode(),
            },
        )

    async def get_idle_workers(
        self, *, stale_after_seconds: int = 30, job_id: str | None = None
    ) -> list[str]:
        cutoff = time.time() - stale_after_seconds
        agent_ids = await self.redis.zrangebyscore(
            self.keys.agents_active, cutoff, "+inf"
        )
        idle: list[str] = []
        for raw in agent_ids:
            aid = raw.decode() if isinstance(raw, bytes) else str(raw)
            data = await self.redis.hgetall(self.keys.agent_hash(aid))
            role = (data.get(b"role") or b"").decode()
            status = (data.get(b"status") or b"").decode()
            agent_job_id = (data.get(b"job_id") or b"").decode()
            if role == "worker" and status == "idle":
                if job_id and agent_job_id != job_id:
                    continue
                idle.append(aid)
        return idle

    async def get_stale_agents(self, threshold_seconds: int = 30) -> list[str]:
        cutoff = time.time() - threshold_seconds
        raw = await self.redis.zrangebyscore(self.keys.agents_active, "-inf", cutoff)
        return [r.decode() if isinstance(r, bytes) else str(r) for r in raw]

    async def list_agents(self) -> list[dict[str, str]]:
        agent_ids = await self.redis.zrange(self.keys.agents_active, 0, -1)
        agents: list[dict[str, str]] = []
        for raw in agent_ids:
            aid = raw.decode() if isinstance(raw, bytes) else str(raw)
            data = await self.redis.hgetall(self.keys.agent_hash(aid))
            agents.append({k.decode(): v.decode() for k, v in data.items()})
        return agents

    async def list_job_agents(
        self, job_id: str, stale_after_seconds: int = 30
    ) -> list[str]:
        cutoff = time.time() - stale_after_seconds
        agent_ids = await self.redis.zrangebyscore(
            self.keys.agents_active, cutoff, "+inf"
        )
        result: list[str] = []
        for raw in agent_ids:
            aid = raw.decode() if isinstance(raw, bytes) else str(raw)
            data = await self.redis.hgetall(self.keys.agent_hash(aid))
            role = (data.get(b"role") or b"").decode()
            ajob = (data.get(b"job_id") or b"").decode()
            if ajob != job_id:
                continue
            if role not in {"worker", "leader"}:
                continue
            result.append(aid)
        return result

    # --- Worker requests (List) ---
    async def submit_worker_request(self, request: WorkerRequest) -> None:
        await self.redis.lpush(
            self.keys.worker_request_queue, request.model_dump_json().encode()
        )

    async def wait_for_worker_request(self, timeout: int = 0) -> WorkerRequest | None:
        result = await self.redis.brpop(
            self.keys.worker_request_queue, timeout=timeout
        )
        if not result:
            return None
        _, raw = result
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return WorkerRequest.model_validate_json(raw)

    # --- Events ---
    async def publish_job_event(self, job_id: str, event: dict[str, Any]) -> None:
        try:
            await self.redis.publish(
                self.keys.job_events_channel(job_id), json.dumps(event).encode()
            )
        except Exception:
            # Pub/Sub is best-effort.
            return
