import { setTimeout } from "node:timers/promises";
import { RedisCoordinator } from "@agent-fleet/shared";
import type { Settings } from "./config/settings.js";

export abstract class BaseAgent {
  readonly agentId: string;
  readonly role: string;
  readonly settings: Settings;
  coordinator!: RedisCoordinator;

  currentJobId = "";
  currentTaskId = "";
  currentStatus = "idle";
  shutdownRequested = false;

  constructor(agentId: string, role: string, settings: Settings) {
    this.agentId = agentId;
    this.role = role;
    this.settings = settings;
  }

  async start(): Promise<void> {
    const envJobId = (process.env.JOB_ID ?? "").trim();
    if (envJobId) this.currentJobId = envJobId;

    this.coordinator = new RedisCoordinator(this.settings.redisUrl);
    await this.coordinator.registerAgent(this.agentId, {
      name: this.agentId,
      role: this.role,
      model: this.settings.model,
      status: this.currentStatus,
      job_id: this.currentJobId,
      current_task: this.currentTaskId,
    });

    // Fire-and-forget heartbeat loop
    this.heartbeatLoop();
    await this.run();
  }

  private async heartbeatLoop(): Promise<void> {
    while (!this.shutdownRequested) {
      try {
        await this.coordinator.heartbeat(
          this.agentId,
          this.currentStatus,
          this.currentTaskId,
        );
      } catch {
        // best-effort
      }
      await setTimeout(this.settings.heartbeatIntervalSeconds * 1000);
    }
  }

  abstract run(): Promise<void>;
}
