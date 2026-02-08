import { z } from "zod";

export const JobCreateBody = z.object({
  prompt: z.string().min(1),
  workspace_url: z.string().optional(),
  config: z.record(z.unknown()).default({}),
});
export type JobCreateBody = z.infer<typeof JobCreateBody>;

export const JobCreateResponse = z.object({
  job_id: z.string(),
  status: z.string(),
});
export type JobCreateResponse = z.infer<typeof JobCreateResponse>;

export const AgentInfo = z.object({
  name: z.string().optional(),
  role: z.string().optional(),
  status: z.string().optional(),
  model: z.string().optional(),
  heartbeat_ts: z.string().optional(),
  current_task: z.string().optional(),
  job_id: z.string().optional(),
});
export type AgentInfo = z.infer<typeof AgentInfo>;
