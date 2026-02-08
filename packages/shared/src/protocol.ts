import { z } from "zod";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

export function utcNow(): string {
  return new Date().toISOString();
}

// ---------------------------------------------------------------------------
// Agent Message
// ---------------------------------------------------------------------------

export const AgentMessage = z.object({
  id: z.string().default(() => crypto.randomUUID()),
  from_agent: z.string(),
  text: z.string(),
  summary: z.string(),
  timestamp: z.string().default(() => utcNow()),
  read: z.boolean().default(false),
});
export type AgentMessage = z.infer<typeof AgentMessage>;

// ---------------------------------------------------------------------------
// Task
// ---------------------------------------------------------------------------

export const TaskStatus = z.enum([
  "pending",
  "in_progress",
  "completed",
  "failed",
]);
export type TaskStatus = z.infer<typeof TaskStatus>;

export const Task = z.object({
  id: z.string().default(""),
  subject: z.string(),
  description: z.string(),
  activeForm: z.string().default("Working"),
  owner: z.string().default(""),
  status: TaskStatus.default("pending"),
  blocks: z.array(z.string()).default([]),
  blockedBy: z.array(z.string()).default([]),
});
export type Task = z.infer<typeof Task>;

// ---------------------------------------------------------------------------
// Inbox message types
// ---------------------------------------------------------------------------

export const TaskAssignment = z.object({
  type: z.literal("task_assignment").default("task_assignment"),
  job_id: z.string(),
  taskId: z.string(),
  subject: z.string(),
  description: z.string(),
  assignedBy: z.string(),
  timestamp: z.string(),
  agent_type: z.string().default("general"),
  persona: z.string().default(""),
});
export type TaskAssignment = z.infer<typeof TaskAssignment>;

export const IdleNotification = z.object({
  type: z.literal("idle_notification").default("idle_notification"),
  from_agent: z.string(),
  timestamp: z.string(),
  idleReason: z.enum(["available", "blocked", "waiting_for_blocker"]),
});
export type IdleNotification = z.infer<typeof IdleNotification>;

export const ShutdownRequest = z.object({
  type: z.literal("shutdown_request").default("shutdown_request"),
  requestId: z.string(),
  reason: z.string(),
});
export type ShutdownRequest = z.infer<typeof ShutdownRequest>;

export const InterAgentMessage = z.object({
  type: z.literal("agent_message").default("agent_message"),
  job_id: z.string(),
  from_agent: z.string(),
  to_agent: z.string(),
  text: z.string(),
  timestamp: z.string(),
});
export type InterAgentMessage = z.infer<typeof InterAgentMessage>;

export const PlanApprovalRequest = z.object({
  type: z
    .literal("plan_approval_request")
    .default("plan_approval_request"),
  requestId: z.string(),
  plan: z.string(),
  taskId: z.string(),
});
export type PlanApprovalRequest = z.infer<typeof PlanApprovalRequest>;

export const WorkerRequest = z.object({
  type: z.literal("worker_request").default("worker_request"),
  job_id: z.string(),
  leader_id: z.string(),
  requested_count: z.number(),
  timestamp: z.string(),
});
export type WorkerRequest = z.infer<typeof WorkerRequest>;

export const WorkerRequestResponse = z.object({
  type: z
    .literal("worker_request_response")
    .default("worker_request_response"),
  job_id: z.string(),
  granted_count: z.number(),
  worker_ids: z.array(z.string()),
  timestamp: z.string(),
});
export type WorkerRequestResponse = z.infer<typeof WorkerRequestResponse>;

export const JobEvent = z.object({
  type: z.string(),
  job_id: z.string(),
  payload: z.record(z.unknown()).default({}),
  timestamp: z.string().default(() => utcNow()),
});
export type JobEvent = z.infer<typeof JobEvent>;
