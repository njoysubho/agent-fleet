import {
  type TaskAssignment as TaskAssignmentType,
  TaskAssignment,
  ShutdownRequest,
  InterAgentMessage,
  commitAndPush,
  createWorktree,
  cleanupWorktree,
} from "@agent-fleet/shared";
import { BaseAgent } from "../base-agent.js";
import type { Settings } from "../config/settings.js";
import { getToolsForType } from "../config/agent-types.js";
import { createProvider } from "../provider/factory.js";
import { runAgentTask } from "./executor.js";

export class WorkerAgent extends BaseAgent {
  private messageBuffer: string[] = [];
  private provider = createProvider();

  constructor(agentId: string, settings: Settings) {
    super(agentId, "worker", settings);
  }

  async run(): Promise<void> {
    const jobId = (process.env.JOB_ID ?? "").trim();

    const pending: TaskAssignmentType[] = [];
    let inflight: Promise<void> | null = null;
    let inflightDone = true;

    while (!this.shutdownRequested) {
      if (inflight && inflightDone) {
        inflight = null;
      }

      const inboxScope = jobId || "control";
      const msg = await this.coordinator.waitForMessage(
        inboxScope,
        this.agentId,
        5,
      );

      if (!msg) {
        // Check if job is terminal
        if (jobId) {
          try {
            const job = await this.coordinator.getJob(jobId);
            const status = String(job?.status ?? "");
            if (["completed", "cancelled", "failed"].includes(status))
              return;
          } catch {
            // ignore
          }
        }
        if (pending.length > 0 && inflight === null) {
          const next = pending.shift()!;
          inflightDone = false;
          inflight = this.executeTask(next).finally(() => {
            inflightDone = true;
          });
        }
        continue;
      }

      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(msg.text) as Record<string, unknown>;
      } catch {
        continue;
      }

      if (payload.type === "shutdown_request") {
        ShutdownRequest.parse(payload);
        this.shutdownRequested = true;
        return;
      }

      if (payload.type === "agent_message") {
        const imsg = InterAgentMessage.parse(payload);
        this.messageBuffer.push(
          `from=${imsg.from_agent}: ${imsg.text}`,
        );
        await this.coordinator.publishJobEvent(imsg.job_id, {
          type: "agent_message_received",
          from: imsg.from_agent,
          to: imsg.to_agent,
          text: imsg.text,
        });
        console.log(
          `[msg] from=${imsg.from_agent} to=${imsg.to_agent}: ${imsg.text}`,
        );
        continue;
      }

      if (payload.type !== "task_assignment") continue;

      const assignment = TaskAssignment.parse(payload);
      pending.push(assignment);
      if (inflight === null && pending.length > 0) {
        const next = pending.shift()!;
        inflightDone = false;
        inflight = this.executeTask(next).finally(() => {
          inflightDone = true;
        });
      }
    }
  }

  private async executeTask(
    assignment: TaskAssignmentType,
  ): Promise<void> {
    const jobId = assignment.job_id;
    const taskId = assignment.taskId;

    this.currentJobId = jobId;
    this.currentTaskId = taskId;
    this.currentStatus = "busy";

    try {
      const claimed = await this.coordinator.claimTask(
        jobId,
        taskId,
        this.agentId,
      );
      if (!claimed) return;

      const worktree = await createWorktree(jobId, this.agentId);
      const agentTools = getToolsForType(
        assignment.agent_type || "general",
      );

      let personaPrefix = "";
      if (assignment.persona?.trim()) {
        personaPrefix = `Persona: ${assignment.persona.trim()}\nAct in this role consistently.\n\n`;
      }

      const buffered = [...this.messageBuffer];
      this.messageBuffer = [];

      let messageContext = "";
      if (buffered.length > 0) {
        console.log(
          `[context] applying ${buffered.length} buffered message(s) to task ${taskId}`,
        );
        messageContext =
          "Recent messages from other agents (use as context for this task):\n" +
          buffered.join("\n") +
          "\n\n";
      }

      const result = await runAgentTask(this.provider, {
        prompt: messageContext + personaPrefix + assignment.description,
        cwd: worktree,
        allowedTools: agentTools,
        model: this.settings.model || undefined,
        maxTurns: this.settings.maxTurns,
      });

      await commitAndPush(
        worktree,
        `task/${taskId}: ${assignment.subject}`,
      );

      if (result.ok) {
        await this.coordinator.completeTask(
          jobId,
          taskId,
          this.agentId,
          result.resultText,
        );
      } else {
        await this.coordinator.failTask(
          jobId,
          taskId,
          this.agentId,
          result.resultText,
        );
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      await this.coordinator.failTask(jobId, taskId, this.agentId, msg);
    } finally {
      try {
        await cleanupWorktree(jobId, this.agentId);
      } catch {
        // ignore
      }
      this.currentTaskId = "";
      this.currentStatus = "idle";
    }
  }
}
