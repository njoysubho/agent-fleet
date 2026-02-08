import type { FastifyInstance } from "fastify";
import { verifyApiKey } from "../middleware/auth.js";
import type { AppState } from "../services/state.js";

export async function taskRoutes(app: FastifyInstance): Promise<void> {
  app.addHook("preHandler", verifyApiKey);

  app.get<{ Params: { job_id: string } }>(
    "/tasks/:job_id",
    async (req) => {
      const state = (app as unknown as { state: AppState }).state;
      return state.coordinator.listTasks(req.params.job_id);
    },
  );
}
