# Agent Team — Task List

> Persistent task tracker. Update status as work progresses.
> Status: `[ ]` pending, `[~]` in progress, `[x]` done, `[!]` blocked

## Phase 1: Infrastructure
- [x] 1.1 Create `docker-compose.yml` (redis, api, leader, worker services)
- [x] 1.2 Create `.env.example` with all env vars
- [x] 1.3 Create `infra/redis/redis.conf` (AOF persistence)
- [x] 1.4 All data storage uses Redis (postgres removed)
- [x] 1.5 Verify `docker compose up` — all 4 services healthy

## Phase 2: Shared Library
- [x] 2.1 Create `shared/protocol.py` — Pydantic models (AgentMessage, Task, TaskAssignment, IdleNotification, ShutdownRequest, PlanApprovalRequest)
- [x] 2.2 Create `shared/redis_client.py` — `RedisCoordinator` class (job queue, inboxes, tasks, locks, agent registry, pub/sub)
- [x] 2.3 Create `shared/git_helpers.py` — init_bare_repo, create_worktree, commit_and_push, merge_branch, cleanup_worktree
- [x] 2.4 Create `shared/db/models.py` — SQLAlchemy ORM models
- [x] 2.5 Create `shared/db/connection.py` — async Postgres connection pool
- [ ] 2.6 Test RedisCoordinator against running Redis (manual smoke test)

## Phase 3: Base Agent
- [x] 3.1 Create `agents/entrypoint.py` — AGENT_ROLE dispatch, hostname-based agent_id
- [~] 3.2 Create `agents/base_agent.py` — BaseAgent ABC (Redis connect, heartbeat, MCP tools, shutdown)
- [x] 3.3 Create `agents/config/settings.py` — Settings from env vars
- [x] 3.4 Create `agents/config/agent_types.py` — tool sets (general, explore, plan, bash)
- [~] 3.5 Create `agents/Dockerfile` — Python 3.12 + Node.js + claude-code CLI + deps

## Phase 4: Custom MCP Tools
- [ ] 4.1 Create `agents/tools/messaging.py` — send_message, broadcast, get_messages
- [ ] 4.2 Create `agents/tools/task_management.py` — create_task, update_task, list_tasks, get_task, claim_task
- [ ] 4.3 Create `agents/tools/coordination.py` — report_status, get_agent_list, request_plan_approval, shutdown_response
- [ ] 4.4 Verify MCP tools register correctly with `create_sdk_mcp_server()`

## Phase 5: Worker Agent
- [ ] 5.1 Create `agents/worker/worker_agent.py` — inbox loop (BRPOP), task execution, git commit
- [~] 5.1 Create `agents/worker/worker_agent.py` — inbox loop (BRPOP), task execution, git commit
- [x] 5.2 Create `agents/worker/executor.py` — ClaudeSDKClient session lifecycle
- [ ] 5.3 Test: worker starts, registers, receives task assignment, executes, reports completion

## Phase 6: Leader Agent
- [ ] 6.1 Create `agents/leader/leader_agent.py` — job intake (XREADGROUP), completion listener, monitor loop
- [~] 6.1 Create `agents/leader/leader_agent.py` — job intake (XREADGROUP), completion listener, monitor loop
- [~] 6.2 Create `agents/leader/planner.py` — Claude SDK structured output for task decomposition
- [ ] 6.3 Create `agents/leader/assigner.py` — priority scheduler (unblocked tasks → idle workers)
- [ ] 6.4 Test: leader reads job from stream, plans tasks, assigns to workers, merges on completion

## Phase 7: API Gateway
- [x] 7.1 Create `api/main.py` — FastAPI app with lifespan (Redis pool)
- [x] 7.2 Create `api/auth.py` — X-Api-Key middleware
- [x] 7.3 Create `api/models/schemas.py` — request/response Pydantic models
- [~] 7.4 Create `api/routers/jobs.py` — POST/GET/DELETE jobs, WebSocket
- [~] 7.5 Create `api/routers/agents.py` — GET agents, POST scale
- [~] 7.6 Create `api/routers/tasks.py` — GET task details
- [~] 7.7 Create `api/services/state.py` — Redis + Postgres state access
- [x] 7.8 Create `api/Dockerfile`
- [ ] 7.9 Test: submit job via curl, check status, verify WebSocket updates

## Phase 8: Hooks + Safety
- [ ] 8.1 Create `agents/hooks/safety_hook.py` — block destructive Bash commands
- [ ] 8.2 Create `agents/hooks/audit_hook.py` — log tool usage to PostgreSQL
- [ ] 8.3 Create `agents/hooks/cost_hook.py` — per-task budget tracking + abort

## Phase 9: Integration Testing
- [ ] 9.1 End-to-end: submit job → leader plans → workers execute → result returned
- [ ] 9.2 Failure: kill worker mid-task → task reassigned
- [ ] 9.3 Scale: add workers dynamically → pick up pending tasks
- [ ] 9.4 Multi-agent messaging: workers exchange messages during complex task
- [ ] 9.5 Git merge: verify all worker branches merged into main

---

## Notes
- Plan file: `~/.claude/plans/humble-riding-crayon.md`
- Redis-only coordination (no NATS)
- Git worktrees per agent for workspace isolation
- Claude Agent SDK (`claude-agent-sdk` Python package) wraps CLI (needs Node.js in Docker image)
