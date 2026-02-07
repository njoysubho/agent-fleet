from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def _run(cmd: list[str], cwd: str | Path | None = None) -> str:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc.stdout


def _git_commit(cwd: Path, message: str, allow_empty: bool = False) -> str:
    cmd = [
        "git",
        "-c",
        "user.name=agentfleet",
        "-c",
        "user.email=agentfleet@local",
        "commit",
    ]
    if allow_empty:
        cmd.append("--allow-empty")
    cmd.extend(["-m", message])
    return _run(cmd, cwd=cwd)


def _bare_repo_root() -> Path:
    return Path(os.environ.get("BARE_REPO_ROOT", "/repo")).resolve()


def _worktrees_root() -> Path:
    return Path(os.environ.get("WORKTREES_ROOT", "/worktrees")).resolve()


def bare_repo_path(job_id: str) -> Path:
    return _bare_repo_root() / f"{job_id}.git"


def init_bare_repo(job_id: str, workspace_url: str | None = None) -> Path:
    """Ensure a bare repository exists for this job.

    If workspace_url is provided, attempts to clone it as a bare repo.
    Otherwise initializes an empty bare repo and creates an initial main branch.
    """

    root = _bare_repo_root()
    root.mkdir(parents=True, exist_ok=True)
    bare = bare_repo_path(job_id)

    if bare.exists():
        return bare

    if workspace_url:
        _run(["git", "clone", "--bare", workspace_url, str(bare)])
    else:
        _run(["git", "init", "--bare", str(bare)])

    _ensure_main_branch(bare)
    return bare


def _ensure_main_branch(bare: Path) -> None:
    try:
        _run(
            ["git", "--git-dir", str(bare), "rev-parse", "--verify", "refs/heads/main"]
        )
        return
    except subprocess.CalledProcessError:
        pass

    with tempfile.TemporaryDirectory(prefix="agentfleet-init-") as tmp:
        tmp_path = Path(tmp)
        _run(["git", "init"], cwd=tmp_path)
        _run(["git", "checkout", "-b", "main"], cwd=tmp_path)
        _git_commit(tmp_path, "Initialize", allow_empty=True)
        _run(["git", "remote", "add", "origin", str(bare)], cwd=tmp_path)
        _run(["git", "push", "-u", "origin", "main"], cwd=tmp_path)


def create_worktree(job_id: str, agent_id: str) -> Path:
    bare = init_bare_repo(job_id)
    root = _worktrees_root() / job_id / agent_id
    root.parent.mkdir(parents=True, exist_ok=True)

    branch = f"job/{job_id}/{agent_id}"
    if root.exists():
        # If rerunning the same agent, clean up and recreate.
        cleanup_worktree(job_id, agent_id)

    _run(
        [
            "git",
            "--git-dir",
            str(bare),
            "worktree",
            "add",
            "-B",
            branch,
            str(root),
            "main",
        ]
    )
    return root


def commit_and_push(worktree_path: str | Path, message: str) -> str:
    worktree_path = Path(worktree_path)
    _run(["git", "add", "-A"], cwd=worktree_path)
    try:
        out = _git_commit(worktree_path, message)
    except subprocess.CalledProcessError as e:
        # "nothing to commit" should not fail the workflow.
        text = e.stdout or ""
        if "nothing to commit" in text:
            return text
        raise
    # Worktrees share the same bare repo; no explicit push required.
    return out


def merge_branch(job_id: str, branch: str) -> str:
    """Merge a completed branch into main.

    Uses a dedicated leader merge worktree per job.
    """

    bare = init_bare_repo(job_id)
    merge_wt = _worktrees_root() / job_id / "leader-merge"
    merge_wt.parent.mkdir(parents=True, exist_ok=True)

    if not merge_wt.exists():
        _run(["git", "--git-dir", str(bare), "worktree", "add", str(merge_wt), "main"])

    out = _run(["git", "merge", "--no-edit", branch], cwd=merge_wt)
    return out


def cleanup_worktree(job_id: str, agent_id: str) -> None:
    bare = bare_repo_path(job_id)
    wt = _worktrees_root() / job_id / agent_id
    if not wt.exists():
        return

    if bare.exists():
        try:
            _run(
                [
                    "git",
                    "--git-dir",
                    str(bare),
                    "worktree",
                    "remove",
                    "--force",
                    str(wt),
                ]
            )
        except Exception:
            pass

    shutil.rmtree(wt, ignore_errors=True)
