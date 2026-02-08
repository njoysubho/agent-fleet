import type { AgentProvider, PlannedTask } from "../provider/types.js";

export async function planTasks(
  provider: AgentProvider,
  opts: {
    prompt: string;
    model?: string;
    maxBudgetUsd?: number;
    maxTasks?: number;
    personas?: string[];
  },
): Promise<PlannedTask[]> {
  return provider.planTasks({
    prompt: opts.prompt,
    model: opts.model || undefined,
    maxBudgetUsd: opts.maxBudgetUsd,
    maxTasks: opts.maxTasks,
    personas: opts.personas,
  });
}
