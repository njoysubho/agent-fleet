import { setTimeout } from "node:timers/promises";
import {
  type Task as TaskType,
  Task,
  TaskAssignment,
  AgentMessage,
  InterAgentMessage,
  WorkerRequest,
  utcNow,
  parseLeaderDirectives,
  stripDirectiveBlock,
  initBareRepo,
  mergeBranch,
} from "@agent-fleet/shared";
import { BaseAgent } from "../base-agent.js";
import type { Settings } from "../config/settings.js";
import { createProvider } from "../provider/factory.js";
import { planTasks } from "./planner.js";

export class LeaderAgent extends BaseAgent {
  private grantedWorkerCount: number | null = null;
  private provider = createProvider();

  constructor(agentId: string, settings: Settings) {
    super(agentId, "leader", settings);
  }

  async run(): Promise<void> {
    const jobIdEnv = (process.env.JOB_ID ?? "").trim();

    if (jobIdEnv) {
      // Ephemeral leader: single job then exit.
      this.listenCompletions(jobIdEnv);
      this.schedulerLoop(jobIdEnv);
      this.inboxLoop(jobIdEnv);

      const job = (await this.coordinator.getJob(jobIdEnv)) ?? {};
      (job as Record<string, unknown>).job_id = jobIdEnv;
      await this.handleJob(job as Record<string, unknown>);

      while (!this.shutdownRequested) {
        const j = await this.coordinator.getJob(jobIdEnv);
        const status = String(j?.status ?? "");
        if (["completed", "cancelled", "failed"].includes(status)) return;
        await setTimeout(2000);
      }
      return;
    }

    // Pool leader
    await this.coordinator.ensureConsumerGroup("leaders");
    this.listenCompletions(null);
    this.schedulerLoop(null);

    while (!this.shutdownRequested) {
      const messages = await this.coordinator.readJobs(
        "leaders",
        this.agentId,
        5000,
      );
      if (!messages) continue;

      for (const [, entries] of messages) {
        for (const [entryId, fields] of entries) {
          const raw =
            typeof fields === "object" && !Array.isArray(fields)
              ? (fields as Record<string, string>).data
              : Array.isArray(fields)
                ? fields[fields.indexOf("data") + 1]
                : undefined;
          if (!raw) {
            await this.coordinator.ackJob("leaders", entryId);
            continue;
          }

          const job = JSON.parse(raw) as Record<string, unknown>;

          // Skip orchestrated jobs
          try {
            const jid = String(job.job_id ?? job.id ?? "");
            if (jid) {
              const meta = await this.coordinator.getJob(jid);
              const flag = String(meta?.orchestrated ?? "").toLowerCase();
              if (["true", "1", "yes"].includes(flag)) {
                await this.coordinator.ackJob("leaders", entryId);
                continue;
              }
            }
          } catch {
            // ignore
          }

          await this.handleJob(job);
          await this.coordinator.ackJob("leaders", entryId);
        }
      }
    }
  }

  // -------------------------------------------------------------------------
  // Job handling
  // -------------------------------------------------------------------------

  private async handleJob(job: Record<string, unknown>): Promise<void> {
    const jobId = String(job.job_id ?? job.id ?? crypto.randomUUID());
    const rawPrompt = String(job.prompt ?? "");
    const directives = parseLeaderDirectives(rawPrompt);
    const prompt = stripDirectiveBlock(rawPrompt);
    const workspaceUrl = job.workspace_url
      ? String(job.workspace_url)
      : undefined;

    this.currentJobId = jobId;
    await this.coordinator.upsertJob(jobId, {
      job_id: jobId,
      prompt,
      status: "planning",
      workspace_url: workspaceUrl ?? "",
      directives: {
        max_tasks: directives.maxTasks,
        personas: directives.personas,
      },
      created_at: utcNow(),
    });

    await initBareRepo(jobId, workspaceUrl);

    const taskIds: string[] = [];

    const useSdk =
      this.settings.sdkMode.toLowerCase() === "live";

    if (!useSdk) {
      // Dry-run fallback: create tasks from directives
      let maxTasks = directives.maxTasks;
      let personas = [...directives.personas];

      if (!maxTasks) maxTasks = 2;
      if (personas.length === 0) {
        personas = Array(Math.max(1, maxTasks - 1)).fill("dev");
        if (maxTasks >= 2) personas.push("qa");
      }
      if (personas.length < maxTasks) {
        const nonQa = personas.filter((p) => p !== "qa");
        const primary = nonQa[0] ?? "dev";
        const wantsQa = personas.includes("qa");
        if (maxTasks === 1) {
          personas = [primary];
        } else if (wantsQa) {
          personas = [
            ...Array(maxTasks - 1).fill(primary),
            "qa",
          ];
        } else {
          personas = Array(maxTasks).fill(primary);
        }
      }
      personas = personas.slice(0, Math.max(1, maxTasks));

      let previousTid: string | null = null;
      let firstDevTid: string | null = null;

      for (const persona of personas) {
        const isQa = persona === "qa";
        const agentType = isQa ? "bash" : "general";
        const activeForm = isQa ? "Testing" : "Implementing";
        const subject = isQa
          ? "QA: tests + verification"
          : `${persona.charAt(0).toUpperCase() + persona.slice(1)}: implementation`;
        const suffix = isQa
          ? "Role: qa. Add tests, run them, and report failures/fixes."
          : `Role: ${persona}. Implement your part of the system.`;

        const blockedBy: string[] = [];
        if (isQa && firstDevTid) {
          blockedBy.push(firstDevTid);
        } else if (previousTid) {
          blockedBy.push(previousTid);
        }

        const tid = await this.coordinator.createTask(
          jobId,
          Task.parse({
            subject,
            description: prompt + "\n\n" + suffix,
            activeForm,
            blockedBy,
          }),
        );
        await this.coordinator.redis.hset(
          this.coordinator.keys.taskHash(jobId, tid),
          "agent_type",
          agentType,
          "persona",
          persona,
        );
        taskIds.push(tid);

        if (firstDevTid === null && !isQa) firstDevTid = tid;
        previousTid = tid;
      }
    } else {
      // Use provider for task planning
      try {
        const planned = await Promise.race([
          planTasks(this.provider, {
            prompt,
            model: this.settings.model || undefined,
            maxTasks: directives.maxTasks ?? undefined,
            personas:
              directives.personas.length > 0
                ? directives.personas
                : undefined,
          }),
          setTimeout(this.settings.plannerTimeoutSeconds * 1000).then(
            () => {
              throw new Error("Planner timeout");
            },
          ),
        ]);
        for (const pt of planned) {
          const tid = await this.coordinator.createTask(
            jobId,
            Task.parse({
              subject: pt.subject,
              description: pt.description,
              activeForm: pt.activeForm ?? "Working",
              blocks: [],
              blockedBy: [],
            }),
          );
          taskIds.push(tid);
          await this.coordinator.redis.hset(
            this.coordinator.keys.taskHash(jobId, tid),
            "agent_type",
            pt.agent_type ?? "general",
            "persona",
            (pt.persona ?? "").trim().toLowerCase(),
          );
        }
      } catch {
        // Fallback: single task
        const tid = await this.coordinator.createTask(
          jobId,
          Task.parse({
            subject: "Execute job",
            description: prompt || "(empty prompt)",
            activeForm: "Working",
          }),
        );
        taskIds.push(tid);
        await this.coordinator.redis.hset(
          this.coordinator.keys.taskHash(jobId, tid),
          "agent_type",
          "general",
        );
      }
    }

    await this.coordinator.setJobStatus(jobId, "in_progress");
    try {
      await this.coordinator.redis.sadd("jobs:active", jobId);
    } catch {
      // ignore
    }

    // Request workers from orchestrator (ephemeral mode)
    if (process.env.JOB_ID && taskIds.length > 0) {
      const maxCap = parseInt(
        process.env.MAX_WORKERS_PER_JOB ?? "6",
        10,
      );
      const desired = Math.min(taskIds.length, maxCap);
      if (desired > 0) {
        try {
          await this.coordinator.submitWorkerRequest(
            WorkerRequest.parse({
              job_id: jobId,
              leader_id: this.agentId,
              requested_count: desired,
              timestamp: utcNow(),
            }),
          );
          console.log(
            `[leader] requested ${desired} workers for job_id=${jobId}`,
          );
          await this.waitForWorkers(jobId, desired, 60);
        } catch (err: unknown) {
          console.log(
            `[leader] worker request failed: ${err instanceof Error ? err.message : err}`,
          );
        }
      }
    }

    await this.assignAvailable(jobId);
    await this.coordinator.publishJobEvent(jobId, {
      type: "job_planned",
      job_id: jobId,
      task_ids: taskIds,
    });
  }

  // -------------------------------------------------------------------------
  // Worker wait
  // -------------------------------------------------------------------------

  private async waitForWorkers(
    jobId: string,
    desired: number,
    timeoutSecs: number,
  ): Promise<void> {
    let target = desired;
    let elapsed = 0;
    while (elapsed < timeoutSecs) {
      if (this.grantedWorkerCount !== null)
        target = this.grantedWorkerCount;
      const idle = await this.coordinator.getIdleWorkers({
        jobId,
      });
      if (idle.length >= target) {
        console.log(
          `[leader] ${idle.length} workers ready for job_id=${jobId}`,
        );
        return;
      }
      await setTimeout(1000);
      elapsed += 1;
    }
    const idle = await this.coordinator.getIdleWorkers({ jobId });
    console.log(
      `[leader] timeout waiting for workers, proceeding with ${idle.length} for job_id=${jobId}`,
    );
  }

  // -------------------------------------------------------------------------
  // Task assignment
  // -------------------------------------------------------------------------

  private async assignAvailable(jobId: string): Promise<void> {
    const unblocked = await this.coordinator.getUnblockedTasks(jobId);
    const idleWorkers = await this.coordinator.getIdleWorkers({
      jobId: process.env.JOB_ID ? jobId : undefined,
    });

    const pairs = unblocked.slice(
      0,
      Math.min(unblocked.length, idleWorkers.length),
    );

    for (let i = 0; i < pairs.length; i++) {
      const task = pairs[i];
      const workerId = idleWorkers[i];

      const rawAgentType = await this.coordinator.redis.hget(
        this.coordinator.keys.taskHash(jobId, task.id),
        "agent_type",
      );
      const rawPersona = await this.coordinator.redis.hget(
        this.coordinator.keys.taskHash(jobId, task.id),
        "persona",
      );

      const assignment = TaskAssignment.parse({
        job_id: jobId,
        taskId: task.id,
        subject: task.subject,
        description: task.description,
        assignedBy: this.agentId,
        timestamp: utcNow(),
        agent_type: rawAgentType ?? "general",
        persona: rawPersona ?? "",
      });
      const agentMsg = AgentMessage.parse({
        from_agent: this.agentId,
        text: JSON.stringify(assignment),
        summary: `Assigned: ${task.subject}`,
      });

      const scope = process.env.JOB_ID ? jobId : "control";
      await this.coordinator.sendMessage(scope, workerId, agentMsg);
      await this.coordinator.publishJobEvent(jobId, {
        type: "task_assigned",
        task_id: task.id,
        worker: workerId,
      });
    }
  }

  // -------------------------------------------------------------------------
  // Completion listener
  // -------------------------------------------------------------------------

  private async listenCompletions(
    jobIdFilter: string | null,
  ): Promise<void> {
    const sub = this.coordinator.redis.duplicate();
    await sub.subscribe("channel:tasks:completed");

    sub.on("message", async (_channel: string, data: string) => {
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(data) as Record<string, unknown>;
      } catch {
        return;
      }

      const jobId = String(payload.job_id ?? "");
      const agentId = String(payload.agent_id ?? "");
      const status = String(payload.status ?? "completed");
      if (!jobId || !agentId) return;
      if (jobIdFilter && jobId !== jobIdFilter) return;

      if (status === "completed") {
        const branch = `job/${jobId}/${agentId}`;
        try {
          await mergeBranch(jobId, branch);
          await this.coordinator.publishJobEvent(jobId, {
            type: "branch_merged",
            branch,
          });
        } catch (err: unknown) {
          await this.coordinator.publishJobEvent(jobId, {
            type: "merge_failed",
            branch,
            error: err instanceof Error ? err.message : String(err),
          });
        }
      }

      await this.assignAvailable(jobId);

      const tasks = await this.coordinator.listTasks(jobId);
      if (
        tasks.length > 0 &&
        tasks.every(
          (t) => t.status === "completed" || t.status === "failed",
        )
      ) {
        await this.coordinator.setJobStatus(jobId, "completed");
        try {
          await this.coordinator.redis.srem("jobs:active", jobId);
        } catch {
          // ignore
        }
      }
    });
  }

  // -------------------------------------------------------------------------
  // Scheduler loop
  // -------------------------------------------------------------------------

  private async schedulerLoop(
    jobIdFilter: string | null,
  ): Promise<void> {
    while (!this.shutdownRequested) {
      try {
        if (jobIdFilter) {
          await this.assignAvailable(jobIdFilter);
        } else {
          const rawJobs = await this.coordinator.redis.smembers(
            "jobs:active",
          );
          for (const jid of rawJobs) {
            await this.assignAvailable(jid);
          }
        }
      } catch {
        // ignore
      }
      await setTimeout(2000);
    }
  }

  // -------------------------------------------------------------------------
  // Inbox loop (ephemeral mode)
  // -------------------------------------------------------------------------

  private async inboxLoop(jobId: string): Promise<void> {
    while (!this.shutdownRequested) {
      const msg = await this.coordinator.waitForMessage(
        jobId,
        this.agentId,
        5,
      );
      if (!msg) continue;

      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(msg.text) as Record<string, unknown>;
      } catch {
        continue;
      }

      if (payload.type === "worker_request_response") {
        this.grantedWorkerCount = Number(payload.granted_count ?? 0);
        console.log(
          `[leader] orchestrator granted ${this.grantedWorkerCount} workers`,
        );
      } else if (payload.type === "agent_message") {
        const imsg = InterAgentMessage.parse(payload);
        await this.coordinator.publishJobEvent(jobId, {
          type: "agent_message_received",
          from: imsg.from_agent,
          to: imsg.to_agent,
          text: imsg.text,
        });
        console.log(
          `[msg] from=${imsg.from_agent} to=${imsg.to_agent}: ${imsg.text}`,
        );
      }
    }
  }
}
