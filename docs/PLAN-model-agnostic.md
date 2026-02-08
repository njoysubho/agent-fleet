# Plan: Model-Agnostic Agent Fleet

> Status: **Proposed** | Branch: `claude/model-agnostic-exploration-H3Qhb`

## Problem

agent-fleet is tightly coupled to Anthropic's Claude via `claude-agent-sdk` (Python)
and `@anthropic-ai/claude-code` (Node.js CLI). This limits provider choice, increases
cost risk, and prevents using best-fit models per task type.

## Coupling Assessment

**Only 2 files import the Claude SDK directly:**

| File | Imports | Features Used |
|---|---|---|
| `agents/worker/executor.py` | `ClaudeSDKClient`, `HookMatcher`, `ResultMessage` | Agent loop, tool execution, hooks |
| `agents/leader/planner.py` | `ClaudeSDKClient`, `ResultMessage` | Structured output (JSON schema) |

**Everything else is already provider-agnostic:**
- Redis coordination (`shared/redis_client.py`) — no LLM coupling
- Orchestrator (`agents/orchestrator/`) — spawns containers, no LLM calls
- API gateway (`api/`) — HTTP/WebSocket, no LLM coupling
- Protocol models (`shared/protocol.py`) — pure data classes
- Git helpers (`shared/git_helpers.py`) — pure git operations
- Safety hook (`agents/hooks/safety_hook.py`) — regex pattern matching, provider-independent logic

## Proposed Solution: pi-mono as Agent Runtime

Replace `claude-agent-sdk` with [pi-mono](https://github.com/badlogic/pi-mono), specifically:

- **`@mariozechner/pi-ai`** — Unified LLM API supporting 20+ providers
  (Anthropic, OpenAI, Google, Mistral, Bedrock, OpenRouter, Ollama, vLLM, etc.)
- **`@mariozechner/pi-agent-core`** — Agent loop with tool execution, `Agent` class
  with `prompt()`, `steer()`, `followUp()`, `abort()`

### Why pi-mono?

| Requirement | pi-mono capability |
|---|---|
| Multi-provider LLM calls | `pi-ai` stream() — unified API for 20+ providers |
| Agent loop with tools | `pi-agent-core` — Agent class, agentLoop() |
| Built-in coding tools | read, write, edit, bash (matches our tool set) |
| Structured output | Supported across providers via pi-ai |
| Tool restriction per agent type | `--tools` flag, extension-based restriction |
| Safety hooks | Tool overrides via extensions |
| Custom providers (Ollama, vLLM) | `~/.pi/agent/models.json` config |
| MIT license | Yes |
| Active maintenance | Yes (2026, 2.9k+ stars) |

### Why not just raw API calls?

Building our own unified LLM abstraction means maintaining provider adapters for
OpenAI, Anthropic, Google, etc. pi-mono already does this with 300+ model definitions
and handles streaming, tool calls, aborts, and structured output across all of them.

## Architecture Changes

### Layer 1: Provider Abstraction (Python side)

```
agents/
  providers/
    __init__.py
    base.py          # AgentProvider ABC
    pi_provider.py   # pi-mono CLI wrapper (subprocess)
    claude_provider.py  # Current claude-agent-sdk (kept as option)
    config.py        # Provider selection from env vars
```

```python
# base.py
class AgentProvider(ABC):
    @abstractmethod
    async def execute_task(
        self, prompt: str, cwd: str, tools: list[str],
        model: str | None, max_turns: int | None,
    ) -> ExecutionResult: ...

    @abstractmethod
    async def plan_tasks(
        self, prompt: str, model: str | None,
        max_tasks: int | None,
    ) -> list[PlannedTask]: ...
```

### Layer 2: pi-mono Integration

The `PiProvider` wraps the `pi` CLI as a subprocess — same pattern as how
`claude-agent-sdk` wraps `@anthropic-ai/claude-code` today.

```python
# pi_provider.py
class PiProvider(AgentProvider):
    async def execute_task(self, prompt, cwd, tools, model, max_turns):
        # Invoke: pi --model <model> --tools <tools> --non-interactive
        proc = await asyncio.create_subprocess_exec(
            "pi", "--model", model, "--cwd", cwd, ...
        )
        ...
```

### Layer 3: Configuration

```bash
# .env
AGENT_PROVIDER=pi              # or "claude" for backwards compat
AGENT_MODEL=anthropic:claude-sonnet-4-20250514  # pi-mono model format
# Provider-specific keys
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
GOOGLE_API_KEY=...
```

### Layer 4: Dockerfile Changes

```dockerfile
# Current: installs claude-code CLI
RUN npm install -g @anthropic-ai/claude-code

# New: install pi CLI (or both for dual-provider support)
RUN npm install -g @mariozechner/pi
```

## Migration Phases

### Phase 0: Provider Abstraction (no behavior change)

1. Create `agents/providers/base.py` — `AgentProvider` ABC with `execute_task()` and `plan_tasks()`
2. Create `agents/providers/claude_provider.py` — Extract current `executor.py` and `planner.py` logic into this class
3. Create `agents/providers/config.py` — Factory that reads `AGENT_PROVIDER` env var, defaults to `"claude"`
4. Update `executor.py` and `planner.py` to delegate to the provider
5. **Result**: Zero behavior change, but the abstraction boundary exists

### Phase 1: pi-mono Provider

1. Create `agents/providers/pi_provider.py` — Wraps `pi` CLI as subprocess
2. Map agent-fleet tool names to pi tool names:
   - `Read` → `read`, `Write` → `write`, `Edit` → `edit`, `Bash` → `bash`
   - `Glob`/`Grep` → pi extensions or bash-based fallback
3. Implement structured output for planner (pi supports JSON output)
4. Port safety hook logic to pi extension or pre-execution validation
5. Update `agents/Dockerfile` to install `@mariozechner/pi`
6. **Result**: `AGENT_PROVIDER=pi MODEL=openai:gpt-4o` works

### Phase 2: Per-Agent Model Selection

1. Extend `agents/config/agent_types.py` to include preferred model per type:
   ```python
   AGENT_TYPE_CONFIG = {
       "general": {
           "tools": ["read", "write", "edit", "bash"],
           "model": "anthropic:claude-sonnet-4-20250514",
       },
       "explore": {
           "tools": ["read"],
           "model": "openai:gpt-4o-mini",  # cheaper for read-only exploration
       },
       "plan": {
           "tools": ["read"],
           "model": "anthropic:claude-sonnet-4-20250514",
       },
   }
   ```
2. Allow model override at job submission time via API
3. **Result**: Mixed-model fleet — cheap models for simple tasks, powerful ones for complex

### Phase 3: Local/Self-Hosted Models

1. Add Ollama/vLLM provider config via `~/.pi/agent/models.json`
2. Test with Code Llama, DeepSeek Coder, Qwen 2.5 Coder
3. Document model requirements (tool use support, context window)
4. **Result**: Fully air-gapped deployment possible

## Tool Name Mapping

| agent-fleet (current) | pi-mono equivalent | Notes |
|---|---|---|
| `Read` | `read` | Direct match |
| `Write` | `write` | Direct match |
| `Edit` | `edit` | Direct match |
| `Bash` | `bash` | Direct match |
| `Glob` | bash + `find` | Or custom pi extension |
| `Grep` | bash + `grep`/`rg` | Or custom pi extension |
| `WebFetch` | Custom extension | Not built-in to pi |

## Safety Hook Migration

Current `safety_hook.py` uses Claude SDK's `HookMatcher` + `PreToolUse` event.
The regex logic itself is provider-independent. Migration options:

1. **Pre-execution wrapper**: Validate bash commands before passing to pi's bash tool
2. **Pi extension**: Override the `bash` tool with a wrapped version that checks patterns
3. **Provider-level hook**: Add pre/post execution hooks to `AgentProvider` ABC

Option 1 is simplest and keeps safety logic in Python.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| pi CLI interface changes | Pin version, wrap in adapter |
| Tool behavior differences across models | Test matrix per provider, model capability flags |
| Structured output not supported by all models | Fallback to prompt-based JSON extraction |
| pi subprocess overhead vs SDK integration | Benchmark; consider direct `pi-ai` npm integration if needed |
| Weaker models produce poor task plans | Set minimum model requirements for planner role |

## Decision Record

- **Keep Claude as a first-class provider** — don't remove, just make it one of many
- **CLI wrapper pattern** — subprocess invocation matches existing architecture
- **Python stays as orchestration language** — no rewrite to TypeScript
- **pi-mono for LLM abstraction** — avoids building our own multi-provider layer
