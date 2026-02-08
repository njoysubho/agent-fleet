import type { AgentProvider, ExecutionResult } from "../provider/types.js";

export async function runAgentTask(
  provider: AgentProvider,
  opts: {
    prompt: string;
    cwd: string;
    allowedTools: string[];
    model?: string;
    maxTurns?: number;
  },
): Promise<ExecutionResult> {
  return provider.executeTask({
    prompt: opts.prompt,
    cwd: opts.cwd,
    tools: opts.allowedTools,
    model: opts.model || undefined,
    maxTurns: opts.maxTurns,
  });
}
