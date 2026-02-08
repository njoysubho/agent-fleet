import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { existsSync, mkdirSync, rmSync } from "node:fs";
import { resolve, dirname } from "node:path";

const execFileAsync = promisify(execFile);

async function run(
  cmd: string,
  args: string[],
  cwd?: string,
): Promise<string> {
  const { stdout } = await execFileAsync(cmd, args, { cwd });
  return stdout;
}

async function gitCommit(
  cwd: string,
  message: string,
  allowEmpty = false,
): Promise<string> {
  const args = [
    "-c",
    "user.name=agentfleet",
    "-c",
    "user.email=agentfleet@local",
    "commit",
  ];
  if (allowEmpty) args.push("--allow-empty");
  args.push("-m", message);
  return run("git", args, cwd);
}

function bareRepoRoot(): string {
  return resolve(process.env.BARE_REPO_ROOT ?? "/repo");
}

function worktreesRoot(): string {
  return resolve(process.env.WORKTREES_ROOT ?? "/worktrees");
}

export function bareRepoPath(jobId: string): string {
  return resolve(bareRepoRoot(), `${jobId}.git`);
}

async function ensureMainBranch(bare: string): Promise<void> {
  try {
    await run("git", [
      "--git-dir",
      bare,
      "rev-parse",
      "--verify",
      "refs/heads/main",
    ]);
    return;
  } catch {
    // main branch does not exist yet
  }

  const { mkdtemp } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const tmp = await mkdtemp(resolve(tmpdir(), "agentfleet-init-"));
  try {
    await run("git", ["init"], tmp);
    await run("git", ["checkout", "-b", "main"], tmp);
    await gitCommit(tmp, "Initialize", true);
    await run("git", ["remote", "add", "origin", bare], tmp);
    await run("git", ["push", "-u", "origin", "main"], tmp);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
}

export async function initBareRepo(
  jobId: string,
  workspaceUrl?: string,
): Promise<string> {
  const root = bareRepoRoot();
  mkdirSync(root, { recursive: true });
  const bare = bareRepoPath(jobId);

  if (existsSync(bare)) return bare;

  if (workspaceUrl) {
    await run("git", ["clone", "--bare", workspaceUrl, bare]);
  } else {
    await run("git", ["init", "--bare", bare]);
  }

  await ensureMainBranch(bare);
  return bare;
}

export async function createWorktree(
  jobId: string,
  agentId: string,
): Promise<string> {
  const bare = await initBareRepo(jobId);
  const wt = resolve(worktreesRoot(), jobId, agentId);
  mkdirSync(dirname(wt), { recursive: true });

  const branch = `job/${jobId}/${agentId}`;
  if (existsSync(wt)) {
    await cleanupWorktree(jobId, agentId);
  }

  await run("git", [
    "--git-dir",
    bare,
    "worktree",
    "add",
    "-B",
    branch,
    wt,
    "main",
  ]);
  return wt;
}

export async function commitAndPush(
  worktreePath: string,
  message: string,
): Promise<string> {
  await run("git", ["add", "-A"], worktreePath);
  try {
    return await gitCommit(worktreePath, message);
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes("nothing to commit")) return msg;
    throw err;
  }
}

export async function mergeBranch(
  jobId: string,
  branch: string,
): Promise<string> {
  const bare = await initBareRepo(jobId);
  const mergeWt = resolve(worktreesRoot(), jobId, "leader-merge");
  mkdirSync(dirname(mergeWt), { recursive: true });

  if (!existsSync(mergeWt)) {
    await run("git", [
      "--git-dir",
      bare,
      "worktree",
      "add",
      mergeWt,
      "main",
    ]);
  }

  return run("git", ["merge", "--no-edit", branch], mergeWt);
}

export async function cleanupWorktree(
  jobId: string,
  agentId: string,
): Promise<void> {
  const bare = bareRepoPath(jobId);
  const wt = resolve(worktreesRoot(), jobId, agentId);
  if (!existsSync(wt)) return;

  if (existsSync(bare)) {
    try {
      await run("git", [
        "--git-dir",
        bare,
        "worktree",
        "remove",
        "--force",
        wt,
      ]);
    } catch {
      // best effort
    }
  }
  rmSync(wt, { recursive: true, force: true });
}
