export interface Settings {
  redisUrl: string;
  model: string;
  heartbeatIntervalSeconds: number;
  maxTurns: number;
  maxBudgetUsd: number;
  sdkMode: string;
  plannerTimeoutSeconds: number;
}

export function settingsFromEnv(): Settings {
  return {
    redisUrl: process.env.REDIS_URL ?? "redis://redis:6379/0",
    model: process.env.MODEL ?? "",
    heartbeatIntervalSeconds: parseFloat(
      process.env.HEARTBEAT_INTERVAL ?? "5",
    ),
    maxTurns: parseInt(process.env.AGENT_MAX_TURNS ?? "30", 10),
    maxBudgetUsd: parseFloat(process.env.AGENT_MAX_BUDGET_USD ?? "2.0"),
    sdkMode: process.env.AGENT_SDK_MODE ?? "live",
    plannerTimeoutSeconds: parseFloat(
      process.env.AGENT_PLANNER_TIMEOUT ?? "30",
    ),
  };
}
