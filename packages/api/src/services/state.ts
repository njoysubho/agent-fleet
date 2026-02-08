import { RedisCoordinator } from "@agent-fleet/shared";

export interface AppState {
  coordinator: RedisCoordinator;
}

export function buildState(): AppState {
  const coordinator = new RedisCoordinator(
    process.env.REDIS_URL ?? "redis://redis:6379/0",
  );
  return { coordinator };
}
