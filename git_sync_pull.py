from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from runtime_env import ROOT


def run_git(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT}", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed")
    return proc


def is_git_repo() -> bool:
    return run_git(["rev-parse", "--is-inside-work-tree"], check=False).returncode == 0


def ensure_clean_worktree() -> None:
    status = run_git(["status", "--porcelain"]).stdout.strip()
    if status:
        raise RuntimeError(
            "Git worktree is not clean. Commit, push, or discard local changes before pulling.\n"
            + status
        )


def main() -> int:
    if not is_git_repo():
        print("Git repository is not initialized yet; skipping pull.")
        return 0
    ensure_clean_worktree()
    run_git(["fetch", "origin"])
    run_git(["pull", "--ff-only"])
    print("Git pull completed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Git pull failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
