# Agent Team

Inspired by [Claude Code's agent teams](https://docs.anthropic.com/en/docs/claude-code) — where a leader agent spawns teammates, assigns tasks, and merges results — but rebuilt as a **remote, distributed system**. Claude Code's native teams run locally on a single machine. This project replicates that coordination model across Docker containers, using Redis as the backbone instead of local IPC, so agent crews can run on remote servers, scale horizontally, and be triggered via API.

Each job gets an isolated crew of a leader + workers coordinated through Redis, with git worktrees for workspace isolation.

## Architecture

```
                         ┌────────────┐
                         │  FastAPI    │
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
                        │ W ││ W ││ W │  Workers (Claude SDK sessions)
                        └───┘└───┘└───┘
```

**Per-job lifecycle:** Orchestrator spawns a leader. Leader plans tasks, requests N workers. Orchestrator spawns workers. Workers execute via Claude SDK. When all tasks complete, every container self-exits and gets cleaned up.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12 |
| API | FastAPI + Uvicorn |
| Agent SDK | `@anthropic-ai/claude-code` (Node.js CLI wrapped by Python) |
| Coordination | Redis 7 (Streams, Lists/BRPOP, pub/sub, sorted sets) |
| Database | PostgreSQL 16 (AsyncPG + SQLAlchemy) |
| Containers | Docker Compose |
| Git isolation | Git worktrees per agent |

## Project Structure

```
agentfleet/
├── api/                    # FastAPI gateway
│   ├── routers/            #   jobs, agents, tasks endpoints
│   ├── services/           #   Redis + Postgres state access
│   └── models/             #   Request/response schemas
├── agents/
│   ├── leader/             # Job planning, task assignment, merge coordination
│   ├── worker/             # Task execution via Claude SDK sessions
│   ├── orchestrator/       # Spawns leader/worker containers per job
│   ├── config/             # Settings, agent type definitions
│   ├── hooks/              # Safety hooks (block destructive commands)
│   └── tools/              # Agent tool implementations
├── shared/
│   ├── protocol.py         # Pydantic models (messages, tasks, worker requests)
│   ├── redis_client.py     # RedisCoordinator (all Redis patterns)
│   ├── directives.py       # Prompt directive parsing
│   ├── git_helpers.py      # Bare repo, worktree, merge operations
│   └── db/                 # SQLAlchemy ORM models + async connection
├── infra/
│   ├── redis/redis.conf    # AOF persistence config
│   └── postgres/init.sql   # Schema initialization
├── docker-compose.yml      # 5 services: redis, postgres, api, orchestrator, leader/worker
└── .env.example            # Environment variable template
```

## Getting Started

### Prerequisites

- Docker and Docker Compose
- An Anthropic API key (optional for dry-run mode)

### Setup

```bash
# Clone the repo
git clone <repo-url> && cd agentfleet

# Create your env file
cp .env.example .env
# Edit .env — at minimum set API_SECRET_KEY and optionally ANTHROPIC_API_KEY

# Build images
docker compose build

# Start core services
docker compose up redis postgres api orchestrator
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

To test the full pipeline without calling the Anthropic API:

```bash
AGENT_SDK_MODE=dry_run docker compose up redis postgres api orchestrator
```

## Contributing

### Development Setup

1. **Fork and clone** the repository.

2. **Install Python dependencies** for local development (outside Docker):

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r agents/requirements.txt -r api/requirements.txt
   ```

3. **Copy the env template:**

   ```bash
   cp .env.example .env
   ```

4. **Start infrastructure** for local testing:

   ```bash
   docker compose up redis postgres
   ```

### Code Organization

- **`shared/`** contains all models and Redis coordination logic. Changes here affect both the API and agents.
- **`agents/`** is the agent runtime. The `entrypoint.py` dispatches based on `AGENT_ROLE` env var (`leader`, `worker`, or `orchestrator`).
- **`api/`** is the HTTP gateway. It reads from Redis/Postgres but never runs agent logic directly.
- Both the API and agents Dockerfiles copy `shared/` into their images — keep `shared/` free of agent-specific or API-specific imports.

### Making Changes

1. **Create a feature branch** from `main`:
   ```bash
   git checkout -b feature/your-change
   ```

2. **Follow existing patterns.** Look at how similar code works before adding new functionality:
   - New Redis keys go in `RedisKeys` dataclass (`shared/redis_client.py`)
   - New message types go in `shared/protocol.py` as Pydantic models
   - New API endpoints go in `api/routers/` and get wired in `api/main.py`
   - Agent behavior changes go in the relevant agent module under `agents/`

3. **Keep `shared/protocol.py` as the single source of truth** for all inter-agent message types.

4. **Test end-to-end with dry-run mode** before testing with live API calls:
   ```bash
   docker compose build
   AGENT_SDK_MODE=dry_run docker compose up redis postgres api orchestrator
   # Submit a test job and verify logs
   ```

5. **Check container behavior:**
   ```bash
   # Watch orchestrator/leader/worker logs
   docker compose logs -f orchestrator

   # Verify containers spawn and clean up
   docker ps --filter "label=agentteam.job_id"
   ```

### Key Conventions

- **Pydantic for all data models.** No raw dicts crossing module boundaries.
- **Redis as the coordination backbone.** Streams for job queue, Lists+BRPOP for agent inboxes, pub/sub for real-time events, SET NX for locks.
- **Ephemeral containers per job.** Containers are named `agentfleet-{job_id[:8]}-{role}` and cleaned up automatically when the job reaches a terminal state.
- **Pool mode** (leader/worker services with `profiles: ["pool"]`) is an alternative to orchestrated mode — used when you want long-lived agents instead of per-job containers.
- **`JOB_ID` env var** distinguishes ephemeral (set) from pool (unset) mode in leader/worker code.

### Areas for Contribution

- **Tests** — no test suite exists yet. Unit tests for `shared/redis_client.py` and `shared/protocol.py`, integration tests for the job lifecycle, would be valuable.
- **CI/CD** — GitHub Actions for linting, type checking, and integration tests.
- **Linting config** — the project uses Ruff but has no committed config file. Adding a `pyproject.toml` with Ruff + mypy settings would help standardize.
- **Error recovery** — handling leader crashes mid-job, stale worker detection, task reassignment.
- **Observability** — structured logging, metrics (Prometheus), tracing.
- **Task dependencies** — the planner creates independent tasks; adding dependency resolution would enable complex multi-step workflows.

### Submitting a PR

1. Make sure `docker compose build` succeeds.
2. Test your change end-to-end in dry-run mode.
3. Keep PRs focused — one feature or fix per PR.
4. Describe what changed and why in the PR description.
