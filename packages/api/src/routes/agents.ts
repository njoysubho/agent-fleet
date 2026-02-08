import type { FastifyInstance } from "fastify";
import { verifyApiKey } from "../middleware/auth.js";
import { AgentInfo } from "../schemas.js";
import type { AppState } from "../services/state.js";

export async function agentRoutes(app: FastifyInstance): Promise<void> {
  app.addHook("preHandler", verifyApiKey);

  app.get("/agents", async () => {
    const state = (app as unknown as { state: AppState }).state;
    const agents = await state.coordinator.listAgents();
    return agents.map((a) => AgentInfo.parse(a));
  });
}
