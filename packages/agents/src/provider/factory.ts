import type { AgentProvider } from "./types.js";
import { PiProvider } from "./pi-provider.js";
import { DryRunProvider } from "./dry-run-provider.js";

/**
 * Create the appropriate AgentProvider based on environment config.
 *
 * AGENT_SDK_MODE=dry_run → DryRunProvider
 * Otherwise            → PiProvider (model-agnostic via pi-mono)
 */
export function createProvider(): AgentProvider {
  const mode = (process.env.AGENT_SDK_MODE ?? "live").toLowerCase();
  if (mode !== "live") {
    return new DryRunProvider();
  }
  return new PiProvider();
}
