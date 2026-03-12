import os
import subprocess
from pathlib import Path

import pytest

from clawd_ops import vault


def run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def git_vault(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    worktree = tmp_path / "vault"
    workspace = tmp_path / "workspace"

    run_git(tmp_path, "init", "--bare", str(remote))
    run_git(tmp_path, "clone", str(remote), str(worktree))
    run_git(worktree, "config", "user.name", "Clawd Tests")
    run_git(worktree, "config", "user.email", "tests@example.com")
    run_git(worktree, "branch", "-M", "main")

    for folder in ("tasks", "personal", "research"):
        (worktree / folder).mkdir(parents=True, exist_ok=True)

    (worktree / "README.md").write_text("# test vault\n", encoding="utf-8")
    run_git(worktree, "add", "README.md", "tasks", "personal", "research")
    run_git(worktree, "commit", "-m", "Initial vault state")
    run_git(worktree, "push", "-u", "origin", "main")
    run_git(remote, "symbolic-ref", "HEAD", "refs/heads/main")

    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OBSIDIAN_VAULT", str(worktree))
    monkeypatch.setenv("BOT_TIMEZONE", "America/New_York")
    monkeypatch.delenv("CLAWD_MEMORY_PATH", raising=False)
    monkeypatch.setattr(vault, "_project_root", lambda: workspace)
    return worktree


@pytest.fixture
def fixed_env(monkeypatch):
    original = os.environ.copy()
    try:
        yield monkeypatch
    finally:
        os.environ.clear()
        os.environ.update(original)
