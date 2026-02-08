import { setTimeout } from "node:timers/promises";
import type {
  AgentProvider,
  ExecuteTaskOptions,
  ExecutionResult,
  PlanTasksOptions,
  PlannedTask,
} from "./types.js";

/**
 * A no-op provider for end-to-end wiring tests without LLM calls.
 */
export class DryRunProvider implements AgentProvider {
  private readonly sleepMs: number;

  constructor() {
    const s = parseFloat(process.env.DRY_RUN_SLEEP_SECONDS ?? "0");
    this.sleepMs = isNaN(s) ? 0 : s * 1000;
  }

  async executeTask(_opts: ExecuteTaskOptions): Promise<ExecutionResult> {
    if (this.sleepMs > 0) await setTimeout(this.sleepMs);
    return {
      ok: true,
      resultText: "dry-run: skipped agent execution",
    };
  }

  async planTasks(opts: PlanTasksOptions): Promise<PlannedTask[]> {
    if (this.sleepMs > 0) await setTimeout(this.sleepMs);
    // Return a simple fallback plan
    return [
      {
        subject: "Execute job",
        description: opts.prompt,
        activeForm: "Working",
        agent_type: "general",
      },
    ];
  }
}
