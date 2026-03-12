from datetime import datetime, timezone
from pathlib import Path

from clawd_ops.conflicts import clear_conflicts, report_conflict

from clawd_ops.conflicts import ConflictError
from clawd_ops.conflicts import _abort_merge, _commit_all_if_needed, _git, _run_git


def sync_app_repo(
    project_dir: str = "/home/ec2-user/clawd-bot",
    remote_name: str = "origin",
    remote_branch: str = "main",
) -> str:
    repo_path = Path(project_dir).expanduser().resolve()
    if not (repo_path / ".git").exists():
        return f"Skipped app repo sync because {repo_path} is not a git checkout."

    remote_check = _run_git(repo_path, "remote", "get-url", remote_name)
    if remote_check.returncode != 0:
        return f"Skipped app repo sync because remote {remote_name} is not configured."

    _git(repo_path, "fetch", remote_name, remote_branch)
    _commit_all_if_needed(
        repo_path,
        f"Workspace sync: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
    )

    remote_ref = f"{remote_name}/{remote_branch}"
    ff_only = _run_git(repo_path, "merge", "--ff-only", remote_ref)
    if ff_only.returncode != 0:
        merge = _run_git(repo_path, "merge", "--no-edit", "--autostash", remote_ref)
        if merge.returncode != 0:
            _abort_merge(repo_path)
            details = merge.stderr.strip() or merge.stdout.strip() or "merge failed"
            conflict_id = report_conflict(
                kind="app_repo_sync",
                summary=f"Could not merge app repo changes from {remote_ref}.",
                details=details,
                repo_path=str(repo_path),
                remote_name=remote_name,
                remote_branch=remote_branch,
            )
            raise ConflictError(conflict_id, "Clawd app repo")

    push = _run_git(repo_path, "push", remote_name, f"HEAD:{remote_branch}")
    if push.returncode != 0 and "Everything up-to-date" not in push.stdout:
        details = push.stderr.strip() or push.stdout.strip() or "push failed"
        conflict_id = report_conflict(
            kind="app_repo_sync",
            summary=f"Could not push app repo changes to {remote_ref}.",
            details=details,
            repo_path=str(repo_path),
            remote_name=remote_name,
            remote_branch=remote_branch,
        )
        raise ConflictError(conflict_id, "Clawd app repo")

    clear_conflicts("app_repo_sync", str(repo_path))
    return f"Synced app repo with {remote_ref}."
