import os from "node:os";
import { settingsFromEnv } from "./config/settings.js";
import { LeaderAgent } from "./leader/leader-agent.js";
import { WorkerAgent } from "./worker/worker-agent.js";

async function main(): Promise<void> {
  const role = process.env.AGENT_ROLE ?? "worker";
  const hostname = os.hostname();
  const agentId = process.env.AGENT_ID ?? `${role}-${hostname.slice(0, 8)}`;

  const settings = settingsFromEnv();

  if (role === "orchestrator") {
    const { main: orchMain } = await import(
      "./orchestrator/orchestrator.js"
    );
    await orchMain();
    return;
  }

  if (role === "leader") {
    const agent = new LeaderAgent(agentId, settings);
    await agent.start();
  } else {
    const agent = new WorkerAgent(agentId, settings);
    await agent.start();
  }
}

main().catch((err) => {
  console.error("[entrypoint] fatal:", err);
  process.exit(1);
});
