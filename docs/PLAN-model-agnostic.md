# Plan: TypeScript Rewrite + Model-Agnostic via pi-mono

> Status: **Proposed** | Branch: `claude/model-agnostic-exploration-H3Qhb`

## Goals

1. **Rewrite from Python to TypeScript** — align with pi-mono's ecosystem (TypeScript monorepo)
2. **Replace `claude-agent-sdk`** with pi-mono packages for model-agnostic LLM support
3. **Preserve the architecture** — Redis coordination, ephemeral crews, git worktrees, API gateway

## Why Rewrite to TypeScript?

- pi-mono is a TypeScript monorepo (`@mariozechner/pi-ai`, `@mariozechner/pi-agent-core`)
- Current Python agents already shell out to Node.js (`@anthropic-ai/claude-code` CLI) — we're paying for two runtimes
- TypeScript rewrite eliminates the Python↔Node.js bridge; pi-mono packages become direct imports
- One language, one runtime, one dependency tree
- Docker images shrink (no Python + Node.js dual install)

## Current Python → TypeScript Mapping

### Source Files Inventory

```
Python Source                          Lines   TS Target
──────────────────────────────────────────────────────────────────────
shared/protocol.py                      99    packages/shared/src/protocol.ts
shared/redis_client.py                 441    packages/shared/src/redis-client.ts
shared/git_helpers.py                  171    packages/shared/src/git-helpers.ts
shared/directives.py                    74    packages/shared/src/directives.ts

agents/entrypoint.py                    38    packages/agents/src/entrypoint.ts
agents/base_agent.py                    53    packages/agents/src/base-agent.ts
agents/config/settings.py               34    packages/agents/src/config/settings.ts
agents/config/agent_types.py             7    packages/agents/src/config/agent-types.ts
agents/hooks/safety_hook.py             43    packages/agents/src/hooks/safety-hook.ts
agents/worker/worker_agent.py          166    packages/agents/src/worker/worker-agent.ts
agents/worker/executor.py               84    packages/agents/src/worker/executor.ts
agents/leader/leader_agent.py          435    packages/agents/src/leader/leader-agent.ts
agents/leader/planner.py               121    packages/agents/src/leader/planner.ts
agents/orchestrator/orchestrator.py    275    packages/agents/src/orchestrator/orchestrator.ts

api/main.py                             82    packages/api/src/main.ts
api/auth.py                             14    packages/api/src/middleware/auth.ts
api/routers/jobs.py                     66    packages/api/src/routes/jobs.ts
api/routers/agents.py                   20    packages/api/src/routes/agents.ts
api/routers/tasks.py                    18    packages/api/src/routes/tasks.ts
api/services/state.py                   17    packages/api/src/services/state.ts
api/models/schemas.py                   35    packages/api/src/schemas.ts
```

**Total Python: ~2,300 lines across 17 files**

---

## Target Structure: TypeScript npm Workspaces Monorepo

```
agent-fleet/
├── package.json                    # Root workspace config
├── tsconfig.base.json              # Shared TS compiler options
├── docker-compose.yml              # Same 4 services, now Node.js images
├── .env.example
│
├── packages/
│   ├── shared/                     # @agent-fleet/shared
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   └── src/
│   │       ├── protocol.ts         # Zod schemas (replaces Pydantic models)
│   │       ├── redis-client.ts     # RedisCoordinator (ioredis)
│   │       ├── git-helpers.ts      # Git operations (child_process)
│   │       └── directives.ts       # Prompt directive parsing
│   │
│   ├── agents/                     # @agent-fleet/agents
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   ├── Dockerfile
│   │   └── src/
│   │       ├── entrypoint.ts       # Role-based dispatch
│   │       ├── base-agent.ts       # BaseAgent abstract class
│   │       ├── config/
│   │       │   ├── settings.ts     # Settings from env vars
│   │       │   └── agent-types.ts  # Tool sets + model preferences per agent type
│   │       ├── hooks/
│   │       │   └── safety-hook.ts  # Bash command denylist
│   │       ├── provider/
│   │       │   ├── types.ts        # AgentProvider interface + ExecutionResult
│   │       │   ├── pi-provider.ts  # pi-agent-core integration
│   │       │   └── factory.ts      # Provider factory from env
│   │       ├── worker/
│   │       │   ├── worker-agent.ts # Inbox loop, task execution
│   │       │   └── executor.ts     # Thin wrapper around provider
│   │       ├── leader/
│   │       │   ├── leader-agent.ts # Job intake, assignment, merge
│   │       │   └── planner.ts      # Task decomposition via pi-ai
│   │       └── orchestrator/
│   │           └── orchestrator.ts # Container spawner (dockerode)
│   │
│   └── api/                        # @agent-fleet/api
│       ├── package.json
│       ├── tsconfig.json
│       ├── Dockerfile
│       └── src/
│           ├── main.ts             # Fastify app + WebSocket
│           ├── middleware/
│           │   └── auth.ts         # X-Api-Key check
│           ├── routes/
│           │   ├── jobs.ts         # POST/GET/DELETE jobs
│           │   ├── agents.ts       # GET agents
│           │   └── tasks.ts        # GET tasks
│           ├── services/
│           │   └── state.ts        # AppState with RedisCoordinator
│           └── schemas.ts          # Zod request/response schemas
│
└── infra/
    └── redis/redis.conf
```

---

## Technology Choices

| Layer | Python (current) | TypeScript (target) | Rationale |
|---|---|---|---|
| **Runtime** | Python 3.12 + Node.js | Node.js 22 | Single runtime |
| **HTTP framework** | FastAPI + Uvicorn | Fastify | Fastest Node.js framework, schema validation, WebSocket built-in |
| **Schema validation** | Pydantic | Zod | TypeScript-native, similar API to Pydantic |
| **Redis client** | redis-py (async) | ioredis | Battle-tested, Streams/pub-sub/pipeline support |
| **Docker SDK** | docker-py | dockerode | Node.js Docker client |
| **Git operations** | subprocess.run | child_process.execFile | Direct equivalent |
| **LLM agent loop** | claude-agent-sdk | @mariozechner/pi-agent-core | Model-agnostic agent loop |
| **LLM API** | (via Claude SDK) | @mariozechner/pi-ai | Unified API for 20+ providers |
| **Process management** | asyncio | async/await (native) | Direct equivalent |

---

## Detailed Conversion Plan

### Phase 1: Monorepo Scaffold

**Tasks:**
1. Initialize root `package.json` with npm workspaces pointing to `packages/*`
2. Create `tsconfig.base.json` with strict mode, ES2022 target, NodeNext module resolution
3. Create each package with its own `package.json` + `tsconfig.json`
4. Add build scripts (`tsc` for each package, respecting workspace dependency order)
5. Add dev dependencies: `typescript`, `@types/node`, `tsx` (for dev runs)

**Root `package.json`:**
```json
{
  "name": "agent-fleet",
  "private": true,
  "workspaces": ["packages/*"],
  "scripts": {
    "build": "npm run build --workspaces",
    "clean": "rm -rf packages/*/dist"
  }
}
```

**`tsconfig.base.json`:**
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "esModuleInterop": true,
    "declaration": true,
    "outDir": "dist",
    "rootDir": "src",
    "skipLibCheck": true
  }
}
```

### Phase 2: `@agent-fleet/shared` — Protocol, Redis, Git, Directives

This package has **zero LLM coupling**, so it's a clean 1:1 port.

#### 2a. `protocol.ts` — Replace Pydantic with Zod

```typescript
// packages/shared/src/protocol.ts
import { z } from "zod";

export const TaskStatus = z.enum(["pending", "in_progress", "completed", "failed"]);
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

export const TaskAssignment = z.object({
  type: z.literal("task_assignment"),
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

// ... AgentMessage, IdleNotification, ShutdownRequest, etc.
// Same structure as protocol.py — each Pydantic BaseModel becomes a Zod schema.
```

**Migration pattern:**
| Pydantic | Zod |
|---|---|
| `class Foo(BaseModel):` | `export const Foo = z.object({...})` |
| `field: str = ""` | `field: z.string().default("")` |
| `field: list[str] = Field(default_factory=list)` | `field: z.array(z.string()).default([])` |
| `field: Literal["a", "b"]` | `field: z.enum(["a", "b"])` |
| `model_dump_json()` | `JSON.stringify(parsed)` |
| `model_validate_json(raw)` | `Schema.parse(JSON.parse(raw))` |

#### 2b. `redis-client.ts` — Replace redis-py with ioredis

```typescript
// packages/shared/src/redis-client.ts
import Redis from "ioredis";

export class RedisKeys {
  readonly jobsStream = "stream:jobs";
  readonly agentsActive = "agents:active";
  readonly workerRequestQueue = "queue:worker_requests";

  jobHash(jobId: string)       { return `job:${jobId}`; }
  taskHash(jobId: string, taskId: string) { return `task:${jobId}:${taskId}`; }
  inbox(jobId: string, agent: string) { return `inbox:${jobId}:${agent}`; }
  // ... same key patterns as Python
}

export class RedisCoordinator {
  readonly keys = new RedisKeys();
  readonly redis: Redis;

  constructor(redisUrl: string) {
    this.redis = new Redis(redisUrl);
  }

  async close() { await this.redis.quit(); }

  // --- Job metadata (Hash) ---
  async upsertJob(jobId: string, data: Record<string, unknown>) { ... }
  async getJob(jobId: string): Promise<Record<string, string> | null> { ... }
  async setJobStatus(jobId: string, status: string) { ... }

  // --- Jobs (Stream) ---
  async submitJob(job: Record<string, unknown>): Promise<string> { ... }
  async ensureConsumerGroup(group: string) { ... }
  async readJobs(group: string, consumer: string, blockMs = 5000, count = 1) { ... }
  async ackJob(group: string, entryId: string) { ... }

  // --- Inboxes (List BRPOP) ---
  async sendMessage(jobId: string, target: string, message: AgentMessage) { ... }
  async waitForMessage(jobId: string, agent: string, timeout = 0) { ... }

  // --- Tasks ---
  async createTask(jobId: string, task: Task): Promise<string> { ... }
  async claimTask(jobId: string, taskId: string, agentId: string) { ... }
  async completeTask(jobId: string, taskId: string, agentId: string, result = "") { ... }
  async failTask(jobId: string, taskId: string, agentId: string, error: string) { ... }
  async getUnblockedTasks(jobId: string): Promise<Task[]> { ... }

  // --- Agent registry ---
  async registerAgent(agentId: string, data: Record<string, string>) { ... }
  async heartbeat(agentId: string, status: string, currentTask = "") { ... }
  async getIdleWorkers(opts?: { staleAfter?: number; jobId?: string }) { ... }
  async listAgents(): Promise<Record<string, string>[]> { ... }

  // --- Pub/Sub ---
  async publishJobEvent(jobId: string, event: Record<string, unknown>) { ... }
}
```

**Key ioredis equivalences:**
| redis-py (async) | ioredis |
|---|---|
| `await self.redis.hset(key, mapping={...})` | `await this.redis.hset(key, ...flatEntries)` |
| `await self.redis.brpop(key, timeout=5)` | `await this.redis.brpop(key, 5)` |
| `await self.redis.xreadgroup(...)` | `await this.redis.xreadgroup("GROUP", g, c, "COUNT", n, "BLOCK", ms, "STREAMS", s, ">")` |
| `await self.redis.xadd(...)` | `await this.redis.xadd(stream, "*", ...entries)` |
| `await self.redis.eval(script, 1, key, val)` | `await this.redis.eval(script, 1, key, val)` |
| `pubsub = redis.pubsub(); await pubsub.subscribe(ch)` | `const sub = this.redis.duplicate(); await sub.subscribe(ch)` |

#### 2c. `git-helpers.ts` — Replace subprocess with child_process

```typescript
// packages/shared/src/git-helpers.ts
import { execFile } from "node:child_process";
import { promisify } from "node:util";
const execFileAsync = promisify(execFile);

async function run(cmd: string, args: string[], cwd?: string): Promise<string> {
  const { stdout } = await execFileAsync(cmd, args, { cwd });
  return stdout;
}

export function bareRepoPath(jobId: string): string { ... }
export async function initBareRepo(jobId: string, workspaceUrl?: string) { ... }
export async function createWorktree(jobId: string, agentId: string) { ... }
export async function commitAndPush(worktreePath: string, message: string) { ... }
export async function mergeBranch(jobId: string, branch: string) { ... }
export async function cleanupWorktree(jobId: string, agentId: string) { ... }
```

Direct 1:1 — every `subprocess.run(["git", ...])` becomes `execFileAsync("git", [...])`.

#### 2d. `directives.ts` — Pure regex, trivial port

Same regex patterns, same parsing logic. `re.compile(...)` → `new RegExp(...)`.

---

### Phase 3: `@agent-fleet/agents` — Agent Runtime with pi-mono

This is where the model-agnostic transformation happens.

#### 3a. Provider Abstraction

```typescript
// packages/agents/src/provider/types.ts
export interface ExecutionResult {
  ok: boolean;
  resultText: string;
  totalCostUsd?: number;
  numTurns?: number;
  sessionId?: string;
}

export interface PlannedTask {
  subject: string;
  description: string;
  activeForm?: string;
  blocks?: string[];
  agent_type?: string;
  persona?: string;
}

export interface AgentProvider {
  executeTask(opts: {
    prompt: string;
    cwd: string;
    tools: string[];
    model?: string;
    maxTurns?: number;
  }): Promise<ExecutionResult>;

  planTasks(opts: {
    prompt: string;
    model?: string;
    maxTasks?: number;
    personas?: string[];
  }): Promise<PlannedTask[]>;
}
```

#### 3b. pi-mono Provider (primary)

```typescript
// packages/agents/src/provider/pi-provider.ts
import { Agent } from "@mariozechner/pi-agent-core";
import { stream, ModelRegistry } from "@mariozechner/pi-ai";

export class PiProvider implements AgentProvider {
  async executeTask(opts): Promise<ExecutionResult> {
    // Use pi-agent-core Agent class directly (no subprocess!)
    const agent = new Agent({
      model: opts.model ?? "anthropic:claude-sonnet-4-20250514",
      tools: opts.tools,  // ["read", "write", "edit", "bash"]
      cwd: opts.cwd,
      maxTurns: opts.maxTurns,
    });

    const result = await agent.prompt(opts.prompt);
    return {
      ok: !result.error,
      resultText: result.text ?? "",
      numTurns: result.turns,
    };
  }

  async planTasks(opts): Promise<PlannedTask[]> {
    // Use pi-ai stream() for structured output
    const response = await stream({
      model: opts.model ?? "anthropic:claude-sonnet-4-20250514",
      messages: [{ role: "user", content: plannerPrompt(opts) }],
      responseFormat: { type: "json_schema", schema: taskSchema },
    });

    // Parse structured JSON response
    const data = JSON.parse(response.text);
    return data.tasks;
  }
}
```

**Key advantage:** Since we're now in TypeScript, we import pi-mono packages
**directly** — no CLI subprocess wrapper needed. This is significantly cleaner than
the Python approach of shelling out to a Node.js CLI.

#### 3c. Safety Hook as pi Extension

```typescript
// packages/agents/src/hooks/safety-hook.ts
import type { AgentTool } from "@mariozechner/pi-agent-core";

const DANGEROUS_PATTERNS = [
  /\brm\s+-rf\s+\/\b/,
  /\brm\s+-rf\s+--no-preserve-root\b/,
  /\bmkfs\.(ext2|ext3|ext4|xfs)\b/,
  /\bdd\s+if=.*\s+of=\/dev\/\w+/,
];

// Wrap pi's built-in bash tool with safety checks
export function withSafetyCheck(bashTool: AgentTool): AgentTool {
  return {
    ...bashTool,
    async execute(args) {
      const command = args.command ?? "";
      for (const pattern of DANGEROUS_PATTERNS) {
        if (pattern.test(command)) {
          return { error: "Destructive command blocked by safety hook" };
        }
      }
      return bashTool.execute(args);
    },
  };
}
```

#### 3d. Agent Type Config with Model Preferences

```typescript
// packages/agents/src/config/agent-types.ts
export interface AgentTypeConfig {
  tools: string[];
  defaultModel?: string;  // pi-mono model identifier
}

export const AGENT_TYPES: Record<string, AgentTypeConfig> = {
  general: {
    tools: ["read", "write", "edit", "bash"],
    defaultModel: "anthropic:claude-sonnet-4-20250514",
  },
  explore: {
    tools: ["read", "bash"],
    defaultModel: "openai:gpt-4o-mini",  // cheap for read-only
  },
  plan: {
    tools: ["read", "bash"],
    defaultModel: "anthropic:claude-sonnet-4-20250514",
  },
  bash: {
    tools: ["bash", "read"],
    defaultModel: "anthropic:claude-sonnet-4-20250514",
  },
};
```

#### 3e. BaseAgent, Worker, Leader, Orchestrator

These are 1:1 ports of the Python async logic to TypeScript async/await:

| Python pattern | TypeScript equivalent |
|---|---|
| `asyncio.create_task(coro)` | Call async function, store the Promise (or use a task runner) |
| `asyncio.sleep(n)` | `await setTimeout(n * 1000)` (from `node:timers/promises`) |
| `asyncio.wait_for(coro, timeout)` | `Promise.race([fn(), setTimeout(ms).then(() => { throw ... })])` |
| `while not self.shutdown_requested` | `while (!this.shutdownRequested)` |
| Abstract base class | `abstract class BaseAgent` |

Orchestrator's Docker interaction: replace `docker-py` with `dockerode`:
```typescript
import Docker from "dockerode";
const docker = new Docker({ socketPath: "/var/run/docker.sock" });
// docker.createContainer({...}) instead of docker_client.containers.run(...)
```

---

### Phase 4: `@agent-fleet/api` — Fastify Gateway

#### 4a. Replace FastAPI with Fastify

```typescript
// packages/api/src/main.ts
import Fastify from "fastify";
import websocket from "@fastify/websocket";
import cors from "@fastify/cors";
import { RedisCoordinator } from "@agent-fleet/shared";
import { jobRoutes } from "./routes/jobs.js";
import { agentRoutes } from "./routes/agents.js";
import { taskRoutes } from "./routes/tasks.js";

const app = Fastify({ logger: true });

await app.register(cors, { origin: "*" });
await app.register(websocket);

// State
const coordinator = new RedisCoordinator(process.env.REDIS_URL ?? "redis://redis:6379/0");
app.decorate("coordinator", coordinator);

// Routes
await app.register(jobRoutes, { prefix: "/api/v1" });
await app.register(agentRoutes, { prefix: "/api/v1" });
await app.register(taskRoutes, { prefix: "/api/v1" });

// Health
app.get("/api/v1/health", async () => {
  const redisOk = await coordinator.redis.ping() === "PONG";
  return { ok: redisOk, redis: redisOk };
});

// WebSocket for job events
app.get("/api/v1/ws/jobs/:jobId", { websocket: true }, (socket, req) => {
  // Subscribe to Redis pub/sub channel and forward events
  ...
});

// Graceful shutdown
app.addHook("onClose", async () => { await coordinator.close(); });

await app.listen({ port: 8000, host: "0.0.0.0" });
```

#### 4b. Auth Middleware

```typescript
// packages/api/src/middleware/auth.ts
import type { FastifyRequest, FastifyReply } from "fastify";

export async function verifyApiKey(req: FastifyRequest, reply: FastifyReply) {
  const expected = process.env.API_SECRET_KEY;
  if (!expected) return reply.code(500).send({ detail: "API_SECRET_KEY not configured" });
  const key = req.headers["x-api-key"];
  if (!key || key !== expected) return reply.code(401).send({ detail: "Invalid API key" });
}
```

#### 4c. Route Handlers

Same CRUD logic as Python, using Zod for validation:
```typescript
// packages/api/src/routes/jobs.ts
import { z } from "zod";

const JobCreateBody = z.object({
  prompt: z.string().min(1),
  workspace_url: z.string().optional(),
  config: z.record(z.unknown()).default({}),
});

export async function jobRoutes(app: FastifyInstance) {
  app.addHook("preHandler", verifyApiKey);

  app.post("/jobs", async (req, reply) => {
    const body = JobCreateBody.parse(req.body);
    const jobId = crypto.randomUUID();
    await app.coordinator.upsertJob(jobId, { ... });
    await app.coordinator.submitJob({ ... });
    return { job_id: jobId, status: "queued" };
  });
  // GET, DELETE same pattern
}
```

---

### Phase 5: Dockerfiles

#### Agents Dockerfile (replaces Python + Node.js dual install)

```dockerfile
# packages/agents/Dockerfile
FROM node:22-slim

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends git ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Copy workspace root for npm workspaces resolution
COPY package.json package-lock.json ./
COPY packages/shared/package.json packages/shared/
COPY packages/agents/package.json packages/agents/

RUN npm ci --workspace=@agent-fleet/agents

COPY packages/shared/ packages/shared/
COPY packages/agents/ packages/agents/

RUN npm run build --workspace=@agent-fleet/shared \
 && npm run build --workspace=@agent-fleet/agents

ENV NODE_ENV=production
ENV WORKTREES_ROOT=/worktrees
ENV BARE_REPO_ROOT=/repo

CMD ["node", "packages/agents/dist/entrypoint.js"]
```

#### API Dockerfile

```dockerfile
# packages/api/Dockerfile
FROM node:22-slim

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json ./
COPY packages/shared/package.json packages/shared/
COPY packages/api/package.json packages/api/

RUN npm ci --workspace=@agent-fleet/api

COPY packages/shared/ packages/shared/
COPY packages/api/ packages/api/

RUN npm run build --workspace=@agent-fleet/shared \
 && npm run build --workspace=@agent-fleet/api

ENV NODE_ENV=production
EXPOSE 8000

CMD ["node", "packages/api/dist/main.js"]
```

**Image size improvement:** Current = Python 3.12-slim + Node.js ≈ 450MB → New = node:22-slim ≈ 200MB

---

### Phase 6: docker-compose.yml Updates

```yaml
services:
  redis:
    # Unchanged

  api:
    build:
      context: .
      dockerfile: packages/api/Dockerfile
    environment:
      REDIS_URL: ${REDIS_URL:-redis://redis:6379/0}
      API_SECRET_KEY: ${API_SECRET_KEY:-change-me}
    ports:
      - "8000:8000"

  orchestrator:
    build:
      context: .
      dockerfile: packages/agents/Dockerfile
    environment:
      AGENT_ROLE: orchestrator
      REDIS_URL: ${REDIS_URL:-redis://redis:6379/0}
      AGENT_PROVIDER: ${AGENT_PROVIDER:-pi}
      MODEL: ${MODEL:-}
      # Provider API keys — pass whichever you use
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
      OPENAI_API_KEY: ${OPENAI_API_KEY:-}
      GOOGLE_API_KEY: ${GOOGLE_API_KEY:-}
      # ... rest same as current
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - repo:/repo

  # leader and worker services follow same pattern
```

### Phase 7: .env.example Updates

```bash
# --- Core ---
REDIS_URL=redis://redis:6379/0
API_SECRET_KEY=change-me

# --- LLM Provider (pi-mono) ---
# Provider API keys — set whichever you use
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GOOGLE_API_KEY=

# Default model (pi-mono format: provider:model)
# Examples: anthropic:claude-sonnet-4-20250514, openai:gpt-4o, google:gemini-2.0-flash
MODEL=anthropic:claude-sonnet-4-20250514

# SDK mode
# - live: use pi-mono agent runtime
# - dry_run: skip LLM calls (useful for end-to-end wiring tests)
AGENT_SDK_MODE=live

# Agent defaults
AGENT_MAX_TURNS=30
AGENT_PLANNER_TIMEOUT=30

# Orchestrator
AGENTS_IMAGE=agentfleet-agents:latest
DOCKER_NETWORK=agentfleet_default
REPO_VOLUME=agentfleet_repo
MAX_WORKERS_PER_JOB=6
CLEANUP_DELAY_SECONDS=0
DRY_RUN_SLEEP_SECONDS=0
```

---

## Dependencies

### `@agent-fleet/shared`
```json
{
  "dependencies": {
    "ioredis": "^5.4",
    "zod": "^3.23"
  }
}
```

### `@agent-fleet/agents`
```json
{
  "dependencies": {
    "@agent-fleet/shared": "workspace:*",
    "@mariozechner/pi-ai": "latest",
    "@mariozechner/pi-agent-core": "latest",
    "dockerode": "^4.0"
  }
}
```

### `@agent-fleet/api`
```json
{
  "dependencies": {
    "@agent-fleet/shared": "workspace:*",
    "fastify": "^5.0",
    "@fastify/cors": "^10.0",
    "@fastify/websocket": "^11.0",
    "zod": "^3.23"
  }
}
```

---

## What Gets Removed

| Removed | Reason |
|---|---|
| `claude-agent-sdk` (Python) | Replaced by `@mariozechner/pi-agent-core` |
| `@anthropic-ai/claude-code` (Node.js CLI) | Replaced by `@mariozechner/pi` |
| Python 3.12 runtime | Replaced by Node.js 22 |
| FastAPI + Uvicorn | Replaced by Fastify |
| Pydantic | Replaced by Zod |
| redis-py | Replaced by ioredis |
| docker-py | Replaced by dockerode |
| `ANTHROPIC_API_KEY` as sole auth | Now one of many provider keys |
| `AGENT_PERMISSION_MODE` | pi-mono doesn't have permission modes (runs in full access) |
| `HookMatcher` / Claude hook system | Replaced by pi extension / tool wrapper pattern |

---

## What Gets Preserved (unchanged)

- Redis coordination architecture (streams, BRPOP inboxes, pub/sub, sorted sets, locks)
- Ephemeral crew model (orchestrator → leader → N workers)
- Git worktree isolation per worker
- API contract (POST/GET/DELETE /jobs, GET /agents, GET /tasks, WebSocket)
- Safety hook denylist patterns
- Directive parsing from prompts
- Job lifecycle (queued → planning → in_progress → completed/failed)

---

## Execution Order

| # | Phase | Effort | Risk |
|---|---|---|---|
| 1 | Monorepo scaffold + tsconfig + package.json files | Small | None |
| 2 | `@agent-fleet/shared` (protocol, redis, git, directives) | Medium | Low — pure logic port |
| 3 | `@agent-fleet/agents` provider abstraction + pi-mono integration | Large | Medium — pi-mono API surface |
| 4 | `@agent-fleet/agents` worker, leader, orchestrator, base-agent | Large | Low — async logic port |
| 5 | `@agent-fleet/api` Fastify gateway | Medium | Low — simple CRUD |
| 6 | Dockerfiles + docker-compose.yml | Small | Low |
| 7 | .env.example, README update | Small | None |
| 8 | End-to-end smoke test (dry-run mode) | Medium | Medium |

---

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| pi-agent-core API differs from expectations | Read pi-mono source; prototype executor first |
| ioredis XREADGROUP API differences | Validate with integration test against Redis |
| dockerode API surface vs docker-py | dockerode is well-documented; test container lifecycle early |
| pi-mono structured output across providers | Test planner with Claude, GPT-4o, Gemini; add prompt fallback |
| npm workspace build ordering | Use `--workspace` flag; shared must build first |

## Decision Record

- **Full TypeScript rewrite** — eliminates dual-runtime overhead, aligns with pi-mono ecosystem
- **pi-mono for LLM abstraction** — battle-tested unified API, 20+ providers, 300+ models
- **Direct imports over CLI wrapping** — TypeScript lets us import pi-mono packages directly instead of subprocess
- **Fastify over Express** — faster, built-in validation and WebSocket support
- **Zod over class-based schemas** — TypeScript-native, composable, similar API to Pydantic
- **Preserve Redis architecture** — the coordination layer is solid and provider-independent
- **Preserve API contract** — consumers of the REST API see no breaking changes
