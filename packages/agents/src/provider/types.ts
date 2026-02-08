export interface ExecutionResult {
  ok: boolean;
  resultText: string;
  totalCostUsd?: number;
  numTurns?: number;
  sessionId?: string;
}

export interface PlannedTask {
  subject: string;
  description: string;
  activeForm?: string;
  blocks?: string[];
  agent_type?: string;
  persona?: string;
}

export interface ExecuteTaskOptions {
  prompt: string;
  cwd: string;
  tools: string[];
  model?: string;
  maxTurns?: number;
}

export interface PlanTasksOptions {
  prompt: string;
  model?: string;
  maxBudgetUsd?: number;
  maxTasks?: number;
  personas?: string[];
}

export interface AgentProvider {
  executeTask(opts: ExecuteTaskOptions): Promise<ExecutionResult>;
  planTasks(opts: PlanTasksOptions): Promise<PlannedTask[]>;
}
