import type { FastifyInstance } from "fastify";
import { verifyApiKey } from "../middleware/auth.js";
import { JobCreateBody } from "../schemas.js";
import type { AppState } from "../services/state.js";

export async function jobRoutes(app: FastifyInstance): Promise<void> {
  app.addHook("preHandler", verifyApiKey);

  app.post("/jobs", async (req, reply) => {
    const state = (app as unknown as { state: AppState }).state;
    const body = JobCreateBody.parse(req.body);
    const jobId = crypto.randomUUID();

    await state.coordinator.upsertJob(jobId, {
      job_id: jobId,
      prompt: body.prompt,
      status: "queued",
      workspace_url: body.workspace_url ?? "",
      config: body.config,
    });
    await state.coordinator.submitJob({
      job_id: jobId,
      prompt: body.prompt,
      workspace_url: body.workspace_url,
      config: body.config,
    });
    await state.coordinator.publishJobEvent(jobId, {
      type: "job_submitted",
      job_id: jobId,
    });

    return { job_id: jobId, status: "queued" };
  });

  app.get<{ Params: { job_id: string } }>(
    "/jobs/:job_id",
    async (req) => {
      const state = (app as unknown as { state: AppState }).state;
      const { job_id: jobId } = req.params;
      const job = await state.coordinator.getJob(jobId);
      const tasks = await state.coordinator.listTasks(jobId);
      return {
        job: job ?? { job_id: jobId, status: "unknown" },
        tasks,
      };
    },
  );

  app.get<{ Params: { job_id: string } }>(
    "/jobs/:job_id/tasks",
    async (req) => {
      const state = (app as unknown as { state: AppState }).state;
      return state.coordinator.listTasks(req.params.job_id);
    },
  );

  app.delete<{ Params: { job_id: string } }>(
    "/jobs/:job_id",
    async (req) => {
      const state = (app as unknown as { state: AppState }).state;
      const { job_id: jobId } = req.params;
      await state.coordinator.setJobStatus(jobId, "cancelled");
      return { job_id: jobId, status: "cancelled" };
    },
  );
}
