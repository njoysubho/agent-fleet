import { Redis } from "ioredis";
import {
  AgentMessage,
  Task,
  WorkerRequest,
  type Task as TaskType,
  type AgentMessage as AgentMessageType,
  type WorkerRequest as WorkerRequestType,
} from "./protocol.js";

// ---------------------------------------------------------------------------
// Key namespace
// ---------------------------------------------------------------------------

export class RedisKeys {
  readonly jobsStream = "stream:jobs";
  readonly agentsActive = "agents:active";
  readonly workerRequestQueue = "queue:worker_requests";

  jobHash(jobId: string) {
    return `job:${jobId}`;
  }
  jobTasksZset(jobId: string) {
    return `job:${jobId}:tasks`;
  }
  jobNextTaskId(jobId: string) {
    return `job:${jobId}:next_task_id`;
  }
  taskHash(jobId: string, taskId: string) {
    return `task:${jobId}:${taskId}`;
  }
  taskBlocks(jobId: string, taskId: string) {
    return `task:${jobId}:${taskId}:blocks`;
  }
  taskBlockedBy(jobId: string, taskId: string) {
    return `task:${jobId}:${taskId}:blockedBy`;
  }
  taskLock(jobId: string, taskId: string) {
    return `lock:task:${jobId}:${taskId}`;
  }
  inbox(jobId: string, agentName: string) {
    return `inbox:${jobId}:${agentName}`;
  }
  agentHash(agentId: string) {
    return `agent:${agentId}`;
  }
  jobEventsChannel(jobId: string) {
    return `channel:job:${jobId}:events`;
  }
}

// ---------------------------------------------------------------------------
// Coordinator
// ---------------------------------------------------------------------------

export class RedisCoordinator {
  readonly keys = new RedisKeys();
  readonly redis: Redis;

  constructor(redisUrl: string) {
    this.redis = new Redis(redisUrl);
  }

  async close(): Promise<void> {
    await this.redis.quit();
  }

  // --- Job metadata (Hash) -------------------------------------------------

  async upsertJob(
    jobId: string,
    data: Record<string, unknown>,
  ): Promise<void> {
    const flat: string[] = [];
    for (const [k, v] of Object.entries(data)) {
      flat.push(
        k,
        typeof v === "object" && v !== null ? JSON.stringify(v) : String(v),
      );
    }
    if (flat.length > 0) {
      await this.redis.hset(this.keys.jobHash(jobId), ...flat);
    }
  }

  async getJob(jobId: string): Promise<Record<string, unknown> | null> {
    const raw = await this.redis.hgetall(this.keys.jobHash(jobId));
    if (!raw || Object.keys(raw).length === 0) return null;
    const decoded: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(raw)) {
      if (k === "config" || k === "directives") {
        try {
          decoded[k] = JSON.parse(v);
          continue;
        } catch {
          // fall through
        }
      }
      decoded[k] = v;
    }
    return decoded;
  }

  async setJobStatus(jobId: string, status: string): Promise<void> {
    await this.redis.hset(this.keys.jobHash(jobId), "status", status);
    await this.publishJobEvent(jobId, { type: "job_status", status });
  }

  // --- Jobs (Stream) -------------------------------------------------------

  async submitJob(job: Record<string, unknown>): Promise<string> {
    const jobId = String(job.job_id ?? job.id ?? "");
    await this.redis.xadd(
      this.keys.jobsStream,
      "*",
      "data",
      JSON.stringify(job),
    );
    return jobId;
  }

  async ensureConsumerGroup(group: string): Promise<void> {
    try {
      await this.redis.xgroup(
        "CREATE",
        this.keys.jobsStream,
        group,
        "0",
        "MKSTREAM",
      );
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      if (!message.includes("BUSYGROUP")) throw err;
    }
  }

  async readJobs(
    group: string,
    consumer: string,
    blockMs = 5000,
    count = 1,
  ): Promise<[string, [string, string[]][]][] | null> {
    const result = await this.redis.xreadgroup(
      "GROUP",
      group,
      consumer,
      "COUNT",
      count,
      "BLOCK",
      blockMs,
      "STREAMS",
      this.keys.jobsStream,
      ">",
    );
    return result as [string, [string, string[]][]][] | null;
  }

  async ackJob(group: string, entryId: string): Promise<void> {
    await this.redis.xack(this.keys.jobsStream, group, entryId);
  }

  // --- Inboxes (List) ------------------------------------------------------

  async sendMessage(
    jobId: string,
    targetAgent: string,
    message: AgentMessageType,
  ): Promise<void> {
    await this.redis.lpush(
      this.keys.inbox(jobId, targetAgent),
      JSON.stringify(message),
    );
  }

  async waitForMessage(
    jobId: string,
    agentName: string,
    timeout = 0,
  ): Promise<AgentMessageType | null> {
    const result = await this.redis.brpop(
      this.keys.inbox(jobId, agentName),
      timeout,
    );
    if (!result) return null;
    const [, raw] = result;
    return AgentMessage.parse(JSON.parse(raw));
  }

  async broadcast(
    jobId: string,
    message: AgentMessageType,
    agentNames: string[],
  ): Promise<void> {
    const pipeline = this.redis.pipeline();
    const payload = JSON.stringify(message);
    for (const name of agentNames) {
      pipeline.lpush(this.keys.inbox(jobId, name), payload);
    }
    await pipeline.exec();
  }

  // --- Tasks (Hashes / Sets / ZSet + Locks) --------------------------------

  async createTask(jobId: string, task: TaskType): Promise<string> {
    const taskId = String(
      await this.redis.incr(this.keys.jobNextTaskId(jobId)),
    );
    task.id = taskId;

    await this.redis.hset(
      this.keys.taskHash(jobId, taskId),
      "id",
      taskId,
      "subject",
      task.subject,
      "description",
      task.description,
      "activeForm",
      task.activeForm,
      "owner",
      task.owner,
      "status",
      task.status,
    );
    await this.redis.zadd(
      this.keys.jobTasksZset(jobId),
      Date.now(),
      taskId,
    );

    for (const downstream of task.blocks) {
      await this.redis.sadd(
        this.keys.taskBlocks(jobId, taskId),
        downstream,
      );
      await this.redis.sadd(
        this.keys.taskBlockedBy(jobId, downstream),
        taskId,
      );
    }
    for (const upstream of task.blockedBy) {
      await this.redis.sadd(
        this.keys.taskBlockedBy(jobId, taskId),
        upstream,
      );
      await this.redis.sadd(
        this.keys.taskBlocks(jobId, upstream),
        taskId,
      );
    }

    await this.publishJobEvent(jobId, {
      type: "task_created",
      task_id: taskId,
    });
    return taskId;
  }

  async getTask(jobId: string, taskId: string): Promise<TaskType | null> {
    const data = await this.redis.hgetall(
      this.keys.taskHash(jobId, taskId),
    );
    if (!data || Object.keys(data).length === 0) return null;
    const blockedBy = await this.redis.smembers(
      this.keys.taskBlockedBy(jobId, taskId),
    );
    const blocks = await this.redis.smembers(
      this.keys.taskBlocks(jobId, taskId),
    );
    return Task.parse({
      ...data,
      blockedBy: blockedBy.sort(),
      blocks: blocks.sort(),
    });
  }

  async listTasks(jobId: string): Promise<TaskType[]> {
    const taskIds = await this.redis.zrange(
      this.keys.jobTasksZset(jobId),
      0,
      -1,
    );
    const tasks: TaskType[] = [];
    for (const tid of taskIds) {
      const t = await this.getTask(jobId, tid);
      if (t) tasks.push(t);
    }
    return tasks;
  }

  async claimTask(
    jobId: string,
    taskId: string,
    agentId: string,
    ttlSeconds = 300,
  ): Promise<boolean> {
    const claimed = await this.redis.set(
      this.keys.taskLock(jobId, taskId),
      agentId,
      "EX",
      ttlSeconds,
      "NX",
    );
    if (claimed) {
      await this.redis.hset(
        this.keys.taskHash(jobId, taskId),
        "status",
        "in_progress",
        "owner",
        agentId,
      );
      await this.publishJobEvent(jobId, {
        type: "task_claimed",
        task_id: taskId,
        agent_id: agentId,
      });
    }
    return claimed !== null;
  }

  private async releaseTaskLock(
    jobId: string,
    taskId: string,
    agentId: string,
  ): Promise<void> {
    const script =
      "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end";
    await this.redis.eval(
      script,
      1,
      this.keys.taskLock(jobId, taskId),
      agentId,
    );
  }

  async completeTask(
    jobId: string,
    taskId: string,
    agentId: string,
    result = "",
  ): Promise<void> {
    await this.redis.hset(
      this.keys.taskHash(jobId, taskId),
      "status",
      "completed",
      "result",
      result,
      "owner",
      agentId,
    );
    await this.releaseTaskLock(jobId, taskId, agentId);

    // Auto-unblock downstream tasks.
    const downstream = await this.redis.smembers(
      this.keys.taskBlocks(jobId, taskId),
    );
    for (const tid of downstream) {
      await this.redis.srem(this.keys.taskBlockedBy(jobId, tid), taskId);
    }

    await this.redis.publish(
      "channel:tasks:completed",
      JSON.stringify({
        job_id: jobId,
        task_id: taskId,
        agent_id: agentId,
        status: "completed",
        result,
      }),
    );
    await this.publishJobEvent(jobId, {
      type: "task_completed",
      task_id: taskId,
      agent_id: agentId,
    });
  }

  async failTask(
    jobId: string,
    taskId: string,
    agentId: string,
    error: string,
  ): Promise<void> {
    await this.redis.hset(
      this.keys.taskHash(jobId, taskId),
      "status",
      "failed",
      "result",
      error,
      "owner",
      agentId,
    );
    await this.releaseTaskLock(jobId, taskId, agentId);

    await this.redis.publish(
      "channel:tasks:completed",
      JSON.stringify({
        job_id: jobId,
        task_id: taskId,
        agent_id: agentId,
        status: "failed",
        result: error,
      }),
    );
    await this.publishJobEvent(jobId, {
      type: "task_failed",
      task_id: taskId,
      agent_id: agentId,
      error,
    });
  }

  async getUnblockedTasks(jobId: string): Promise<TaskType[]> {
    const taskIds = await this.redis.zrange(
      this.keys.jobTasksZset(jobId),
      0,
      -1,
    );
    const unblocked: TaskType[] = [];
    for (const tid of taskIds) {
      const data = await this.redis.hgetall(
        this.keys.taskHash(jobId, tid),
      );
      if (!data || Object.keys(data).length === 0) continue;
      if (data.status !== "pending" || data.owner) continue;
      const blockers = await this.redis.smembers(
        this.keys.taskBlockedBy(jobId, tid),
      );
      if (blockers.length > 0) continue;
      const task = await this.getTask(jobId, tid);
      if (task) unblocked.push(task);
    }
    return unblocked;
  }

  // --- Agent registry (Hash + ZSet) ----------------------------------------

  async registerAgent(
    agentId: string,
    agentData: Record<string, string>,
  ): Promise<void> {
    const flat: string[] = [];
    for (const [k, v] of Object.entries(agentData)) {
      flat.push(k, v);
    }
    if (flat.length > 0) {
      await this.redis.hset(this.keys.agentHash(agentId), ...flat);
    }
    await this.redis.zadd(this.keys.agentsActive, Date.now(), agentId);
  }

  async heartbeat(
    agentId: string,
    status: string,
    currentTask = "",
  ): Promise<void> {
    const ts = String(Date.now() / 1000);
    await this.redis.zadd(this.keys.agentsActive, Date.now(), agentId);
    await this.redis.hset(
      this.keys.agentHash(agentId),
      "status",
      status,
      "current_task",
      currentTask,
      "heartbeat_ts",
      ts,
    );
  }

  async getIdleWorkers(opts?: {
    staleAfterSeconds?: number;
    jobId?: string;
  }): Promise<string[]> {
    const stale = opts?.staleAfterSeconds ?? 30;
    const cutoff = Date.now() - stale * 1000;
    const agentIds = await this.redis.zrangebyscore(
      this.keys.agentsActive,
      cutoff,
      "+inf",
    );
    const idle: string[] = [];
    for (const aid of agentIds) {
      const data = await this.redis.hgetall(this.keys.agentHash(aid));
      if (data.role !== "worker" || data.status !== "idle") continue;
      if (opts?.jobId && data.job_id !== opts.jobId) continue;
      idle.push(aid);
    }
    return idle;
  }

  async getStaleAgents(thresholdSeconds = 30): Promise<string[]> {
    const cutoff = Date.now() - thresholdSeconds * 1000;
    return this.redis.zrangebyscore(
      this.keys.agentsActive,
      "-inf",
      cutoff,
    );
  }

  async listAgents(): Promise<Record<string, string>[]> {
    const agentIds = await this.redis.zrange(
      this.keys.agentsActive,
      0,
      -1,
    );
    const agents: Record<string, string>[] = [];
    for (const aid of agentIds) {
      const data = await this.redis.hgetall(this.keys.agentHash(aid));
      agents.push(data);
    }
    return agents;
  }

  async listJobAgents(
    jobId: string,
    staleAfterSeconds = 30,
  ): Promise<string[]> {
    const cutoff = Date.now() - staleAfterSeconds * 1000;
    const agentIds = await this.redis.zrangebyscore(
      this.keys.agentsActive,
      cutoff,
      "+inf",
    );
    const result: string[] = [];
    for (const aid of agentIds) {
      const data = await this.redis.hgetall(this.keys.agentHash(aid));
      if (data.job_id !== jobId) continue;
      if (data.role !== "worker" && data.role !== "leader") continue;
      result.push(aid);
    }
    return result;
  }

  // --- Worker requests (List) ----------------------------------------------

  async submitWorkerRequest(request: WorkerRequestType): Promise<void> {
    await this.redis.lpush(
      this.keys.workerRequestQueue,
      JSON.stringify(request),
    );
  }

  async waitForWorkerRequest(
    timeout = 0,
  ): Promise<WorkerRequestType | null> {
    const result = await this.redis.brpop(
      this.keys.workerRequestQueue,
      timeout,
    );
    if (!result) return null;
    const [, raw] = result;
    return WorkerRequest.parse(JSON.parse(raw));
  }

  // --- Events --------------------------------------------------------------

  async publishJobEvent(
    jobId: string,
    event: Record<string, unknown>,
  ): Promise<void> {
    try {
      await this.redis.publish(
        this.keys.jobEventsChannel(jobId),
        JSON.stringify(event),
      );
    } catch {
      // Pub/Sub is best-effort.
    }
  }
}
