-- Database schema for audit + cold storage.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  prompt text NOT NULL,
  status text NOT NULL DEFAULT 'queued',
  workspace_url text,
  config jsonb NOT NULL DEFAULT '{}'::jsonb,
  cost_usd numeric(12, 4) NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);

CREATE TABLE IF NOT EXISTS tasks (
  job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  task_id text NOT NULL,
  subject text NOT NULL,
  description text NOT NULL,
  active_form text NOT NULL DEFAULT 'Working',
  status text NOT NULL DEFAULT 'pending',
  owner text NOT NULL DEFAULT '',
  result text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (job_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_job_status ON tasks (job_id, status);

CREATE TABLE IF NOT EXISTS task_dependencies (
  job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  task_id text NOT NULL,
  depends_on_task_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (job_id, task_id, depends_on_task_id),
  FOREIGN KEY (job_id, task_id) REFERENCES tasks(job_id, task_id) ON DELETE CASCADE,
  FOREIGN KEY (job_id, depends_on_task_id) REFERENCES tasks(job_id, task_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id uuid REFERENCES jobs(id) ON DELETE CASCADE,
  from_agent text NOT NULL,
  to_agent text,
  text text NOT NULL,
  summary text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_messages_job_created_at ON messages (job_id, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_id uuid REFERENCES jobs(id) ON DELETE CASCADE,
  agent_id text NOT NULL,
  event_type text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_agent_events_created_at ON agent_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_events_job_created_at ON agent_events (job_id, created_at DESC);
