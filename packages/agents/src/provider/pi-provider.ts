import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { checkBashSafety } from "../hooks/safety-hook.js";
import type {
  AgentProvider,
  ExecuteTaskOptions,
  ExecutionResult,
  PlanTasksOptions,
  PlannedTask,
} from "./types.js";

const execFileAsync = promisify(execFile);

/**
 * Provider that wraps the `pi` CLI from @mariozechner/pi-mono.
 *
 * In the future this can be replaced with direct imports of
 * @mariozechner/pi-agent-core and @mariozechner/pi-ai for tighter
 * integration. The CLI wrapper keeps the initial port simple and
 * mirrors how claude-agent-sdk wrapped the claude-code CLI.
 */
export class PiProvider implements AgentProvider {
  async executeTask(opts: ExecuteTaskOptions): Promise<ExecutionResult> {
    const args = ["--non-interactive", "--cwd", opts.cwd];
    if (opts.model) args.push("--model", opts.model);
    if (opts.maxTurns) args.push("--max-turns", String(opts.maxTurns));
    if (opts.tools.length > 0) args.push("--tools", opts.tools.join(","));
    args.push("--prompt", opts.prompt);

    try {
      const { stdout, stderr } = await execFileAsync("pi", args, {
        cwd: opts.cwd,
        timeout: 600_000,
        env: { ...process.env },
      });
      return {
        ok: true,
        resultText: stdout || stderr || "",
      };
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      return { ok: false, resultText: msg };
    }
  }

  async planTasks(opts: PlanTasksOptions): Promise<PlannedTask[]> {
    const maxItems =
      opts.maxTasks && opts.maxTasks > 0 ? opts.maxTasks : 12;

    let personaHint = "";
    if (opts.personas && opts.personas.length > 0) {
      personaHint = `\n\nPersonas: Assign each task a persona from this list (lowercase): ${opts.personas.join(", ")}.`;
    }
    let countHint = "";
    if (opts.maxTasks && opts.maxTasks > 0) {
      countHint = `\n\nTask count: Return at most ${opts.maxTasks} tasks.`;
    }

    const plannerPrompt =
      "Decompose the following job prompt into a small set of concrete engineering tasks. " +
      "Each task should be independently executable by a coding agent in a git worktree. " +
      "Return 3-7 tasks when possible. Use blocks only when a task must wait for another task." +
      countHint +
      personaHint +
      "\n\nRespond ONLY with a JSON object matching this schema: " +
      '{"tasks": [{"subject": "string", "description": "string", "activeForm": "string", ' +
      '"persona": "string", "blocks": ["string"], "agent_type": "string"}]}' +
      "\n\nJOB PROMPT:\n" +
      opts.prompt +
      "\n";

    const args = ["--non-interactive"];
    if (opts.model) args.push("--model", opts.model);
    args.push("--max-turns", "2");
    args.push("--tools", ""); // no tools for planning
    args.push("--prompt", plannerPrompt);

    const { stdout } = await execFileAsync("pi", args, {
      timeout: 60_000,
      env: { ...process.env },
    });

    // Extract JSON from response
    const jsonMatch = stdout.match(/\{[\s\S]*"tasks"[\s\S]*\}/);
    if (!jsonMatch) {
      throw new Error("Planner returned no valid JSON");
    }

    const data = JSON.parse(jsonMatch[0]) as {
      tasks?: PlannedTask[];
    };
    if (!data.tasks || !Array.isArray(data.tasks)) {
      throw new Error("Planner returned invalid tasks structure");
    }

    return data.tasks
      .filter(
        (t) =>
          t.subject && t.description && t.subject.trim() && t.description.trim(),
      )
      .slice(0, maxItems);
  }
}

// Re-export the safety check for use by consumers
export { checkBashSafety };
