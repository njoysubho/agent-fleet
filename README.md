# Agent Fleet

Model-agnostic, distributed agent team orchestration. A leader agent spawns teammates, assigns tasks, and merges results — coordinated across Docker containers via Redis.

Built on [pi-mono](https://github.com/badlogic/pi-mono) for model-agnostic LLM support (Anthropic, OpenAI, Google, Mistral, Bedrock, OpenRouter, Ollama, and more).

## Architecture

```
                         ┌────────────┐
                         │  Fastify   │
               POST /jobs│  Gateway   │
              ──────────►│  :8000     │
                         └─────┬──────┘
                               │ XADD stream:jobs
                               ▼
                         ┌─────────────┐
                         │ Orchestrator│  BRPOP queue:worker_requests
                         │             │◄─────────────────────────────┐
                         └─────┬───────┘                              │
                    spawn      │                                      │
                   leader      │                                      │
                               ▼                                      │
                         ┌─────────────┐   WorkerRequest              │
                         │   Leader    │──────────────────────────────►│
                         │  (plans +   │
                         │   assigns)  │
                         └──┬──┬──┬────┘
                            │  │  │  task assignments via Redis inboxes
                            ▼  ▼  ▼
                        ┌───┐┌───┐┌───┐
                        │ W ││ W ││ W │  Workers (pi-mono agent sessions)
                        └───┘└───┘└───┘
```

**Per-job lifecycle:** Orchestrator spawns a leader. Leader plans tasks, requests N workers. Orchestrator spawns workers. Workers execute via pi-mono agent runtime. When all tasks complete, every container self-exits and gets cleaned up.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | TypeScript (Node.js 22) |
| API | Fastify + WebSocket |
| Agent Runtime | pi-mono (`@mariozechner/pi-agent-core` + `@mariozechner/pi-ai`) |
| LLM Providers | Anthropic, OpenAI, Google, Mistral, OpenRouter, Ollama, vLLM, and more |
| Coordination | Redis 7 (Streams, Lists/BRPOP, pub/sub, sorted sets) |
| Containers | Docker Compose |
| Git isolation | Git worktrees per agent |
| Validation | Zod |

## Project Structure

```
agent-fleet/
├── packages/
│   ├── shared/               # @agent-fleet/shared
│   │   └── src/
│   │       ├── protocol.ts       # Zod schemas (messages, tasks, worker requests)
│   │       ├── redis-client.ts   # RedisCoordinator (all Redis patterns)
│   │       ├── git-helpers.ts    # Bare repo, worktree, merge operations
│   │       └── directives.ts     # Prompt directive parsing
│   │
│   ├── agents/               # @agent-fleet/agents
│   │   ├── Dockerfile
│   │   └── src/
│   │       ├── entrypoint.ts     # Role-based dispatch (leader/worker/orchestrator)
│   │       ├── base-agent.ts     # BaseAgent abstract class
│   │       ├── config/           # Settings, agent type definitions
│   │       ├── hooks/            # Safety hooks (block destructive commands)
│   │       ├── provider/         # AgentProvider interface + pi-mono implementation
│   │       ├── worker/           # Task execution
│   │       ├── leader/           # Job planning, task assignment, merge coordination
│   │       └── orchestrator/     # Container lifecycle management
│   │
│   └── api/                  # @agent-fleet/api
│       ├── Dockerfile
│       └── src/
│           ├── main.ts           # Fastify app + WebSocket
│           ├── middleware/        # Auth
│           ├── routes/           # Jobs, agents, tasks endpoints
│           ├── services/         # App state
│           └── schemas.ts        # Request/response validation
│
├── infra/redis/redis.conf
├── docker-compose.yml
└── .env.example
```

## Getting Started

### Prerequisites

- Docker and Docker Compose
- A provider API key (Anthropic, OpenAI, Google, etc.) — optional for dry-run mode

### Setup

```bash
# Clone the repo
git clone <repo-url> && cd agent-fleet

# Create your env file
cp .env.example .env
# Edit .env — set API_SECRET_KEY and optionally a provider API key

# Build images
docker compose build

# Start core services
docker compose up redis api orchestrator
```

### Submit a Job

```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "X-Api-Key: change-me" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Create a hello world Python script"}'
```

### Check Job Status

```bash
curl http://localhost:8000/api/v1/jobs/<job_id> -H "X-Api-Key: change-me"
```

### Dry-Run Mode

To test the full pipeline without calling any LLM API:

```bash
AGENT_SDK_MODE=dry_run docker compose up redis api orchestrator
```

### Model Selection

Set the `MODEL` env var using pi-mono's `provider:model` format:

```bash
# Claude
MODEL=anthropic:claude-sonnet-4-20250514 docker compose up

# GPT-4o
MODEL=openai:gpt-4o docker compose up

# Gemini
MODEL=google:gemini-2.0-flash docker compose up
```

## Development

### Local Setup

```bash
# Install dependencies
npm install

# Build all packages
npm run build

# Build individually
npm run build -w @agent-fleet/shared
npm run build -w @agent-fleet/agents
npm run build -w @agent-fleet/api
```

### Code Organization

- **`packages/shared/`** — Models and Redis coordination. Changes here affect both the API and agents.
- **`packages/agents/`** — Agent runtime. The `entrypoint.ts` dispatches based on `AGENT_ROLE` env var.
- **`packages/api/`** — HTTP gateway. Reads from Redis but never runs agent logic directly.

### Key Conventions

- **Zod for all data models.** No raw objects crossing module boundaries.
- **Redis as the coordination backbone.** Streams for job queue, Lists+BRPOP for agent inboxes, pub/sub for real-time events, SET NX for locks.
- **Ephemeral containers per job.** Named `agentfleet-{job_id[:8]}-{role}`, cleaned up automatically.
- **Provider abstraction.** `AgentProvider` interface in `packages/agents/src/provider/types.ts` — swap LLM backends without touching agent logic.
