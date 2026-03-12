import contextlib
import json
import os
import shlex
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import requests

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

CONFLICT_KINDS = {
    "app_repo_sync": "Clawd app repo",
    "vault_sync": "Obsidian vault",
}
STRATEGIES = {
    "retry_sync": "retry the sync",
    "keep_local": "keep local changes",
    "keep_remote": "keep GitHub changes",
}
NOTIFY_COOLDOWN = timedelta(minutes=15)


class ConflictError(RuntimeError):
    def __init__(self, conflict_id: str, repo_label: str):
        self.conflict_id = conflict_id
        super().__init__(
            f"{repo_label} sync hit a merge conflict (id {conflict_id}). "
            "I sent you a Telegram alert. Ask me to show the latest conflict, "
            "retry the latest conflict, keep local changes, or keep GitHub changes."
        )


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _state_root() -> Path:
    configured = os.environ.get("CLAWD_STATE_DIR", "").strip()
    base = Path(configured).expanduser() if configured else _project_root() / ".clawd-state"
    return base.resolve()


def _conflicts_dir() -> Path:
    path = _state_root() / "conflicts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _backups_dir() -> Path:
    path = _conflicts_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextlib.contextmanager
def _path_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def _conflict_lock():
    with _path_lock(_state_root() / "conflicts.lock"):
        yield


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp_label(timestamp: str) -> str:
    parsed = datetime.fromisoformat(timestamp)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _conflict_path(conflict_id: str) -> Path:
    return _conflicts_dir() / f"{conflict_id}.json"


def _record_key(kind: str, repo_path: str, remote_name: str, remote_branch: str) -> str:
    return "|".join([kind, str(Path(repo_path).resolve()), remote_name, remote_branch])


def _load_record(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_record(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _list_records() -> list[dict]:
    records = []
    for path in sorted(_conflicts_dir().glob("*.json")):
        records.append(_load_record(path))
    records.sort(key=lambda record: record["created_at"], reverse=True)
    return records


def _find_record(conflict_id: str | None = None, status: str = "open") -> dict | None:
    records = _list_records()
    if conflict_id and conflict_id != "latest":
        for record in records:
            if record["id"] == conflict_id and (status == "all" or record["status"] == status):
                return record
        return None

    for record in records:
        if status == "all" or record["status"] == status:
            return record
    return None


def _repo_label(kind: str) -> str:
    return CONFLICT_KINDS.get(kind, kind)


def _conflict_options(conflict_id: str) -> list[str]:
    return [
        f"show conflict {conflict_id}",
        f"retry conflict {conflict_id}",
        f"resolve conflict {conflict_id} by keeping local changes",
        f"resolve conflict {conflict_id} by keeping GitHub changes",
    ]


def _notification_text(record: dict) -> str:
    options = "\n".join(f"- {item}" for item in _conflict_options(record["id"]))
    return (
        f"Clawd hit a sync conflict in the {_repo_label(record['kind'])}.\n\n"
        f"Conflict ID: {record['id']}\n"
        f"Summary: {record['summary']}\n\n"
        f"Options:\n{options}\n\n"
        "No changes were discarded automatically."
    )


def _send_telegram_notice(text: str) -> bool:
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    user_id = os.environ.get("ALLOWED_USER_ID", "").strip()
    if not token or not user_id:
        return False

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": user_id,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=10,
    )
    response.raise_for_status()
    return True


def _run_git(repo_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def _git(repo_path: Path, *args: str) -> str:
    result = _run_git(repo_path, *args)
    if result.returncode != 0:
        command = shlex.join(["git", *args])
        details = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"{command} failed: {details}")
    return result.stdout.strip()


def _abort_merge(repo_path: Path) -> None:
    merge_head = repo_path / ".git" / "MERGE_HEAD"
    if not merge_head.exists():
        return

    result = _run_git(repo_path, "merge", "--abort")
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or "merge abort failed"
        raise RuntimeError(f"git merge --abort failed: {details}")


def _commit_all_if_needed(repo_path: Path, message: str) -> bool:
    _git(repo_path, "add", "-A")
    staged = _run_git(repo_path, "diff", "--cached", "--quiet", "--exit-code")
    if staged.returncode == 0:
        return False
    _git(repo_path, "commit", "-m", message)
    return True


def _create_backup_patch(repo_path: Path, conflict_id: str) -> str | None:
    diff = _run_git(repo_path, "diff", "--binary")
    staged = _run_git(repo_path, "diff", "--cached", "--binary")
    untracked = _run_git(repo_path, "ls-files", "--others", "--exclude-standard")

    parts = [part for part in (diff.stdout.strip(), staged.stdout.strip()) if part]
    if untracked.stdout.strip():
        parts.append(f"# Untracked files\n{untracked.stdout.strip()}")

    if not parts:
        return None

    backup_path = _backups_dir() / f"{conflict_id}.patch"
    backup_path.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    return str(backup_path)


def report_conflict(
    kind: str,
    summary: str,
    details: str,
    repo_path: str,
    remote_name: str = "origin",
    remote_branch: str = "main",
) -> str:
    repo = str(Path(repo_path).resolve())
    key = _record_key(kind, repo, remote_name, remote_branch)
    now = datetime.now(timezone.utc)

    with _conflict_lock():
        record = None
        previous_details = ""
        for candidate in _list_records():
            if candidate["status"] == "open" and candidate["key"] == key:
                record = candidate
                break

        if record is None:
            conflict_id = f"{kind}-{now.strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6]}"
            record = {
                "id": conflict_id,
                "key": key,
                "kind": kind,
                "repo_path": repo,
                "remote_name": remote_name,
                "remote_branch": remote_branch,
                "status": "open",
                "summary": summary,
                "details": details,
                "created_at": _timestamp(),
                "updated_at": _timestamp(),
                "last_notified_at": "",
            }
        else:
            previous_details = record.get("details", "")
            record["summary"] = summary
            record["details"] = details
            record["updated_at"] = _timestamp()

        last_notified = record.get("last_notified_at", "").strip()
        should_notify = True
        if last_notified:
            last_dt = datetime.fromisoformat(last_notified)
            should_notify = now - last_dt >= NOTIFY_COOLDOWN or details != previous_details

        if should_notify:
            try:
                _send_telegram_notice(_notification_text(record))
                record["last_notified_at"] = _timestamp()
            except Exception:
                record["last_notified_at"] = last_notified

        _write_record(_conflict_path(record["id"]), record)
        return record["id"]


def clear_conflicts(kind: str, repo_path: str, note: str = "Sync completed successfully.") -> None:
    repo = str(Path(repo_path).resolve())
    with _conflict_lock():
        changed = False
        for record in _list_records():
            if record["status"] != "open":
                continue
            if record["kind"] != kind or record["repo_path"] != repo:
                continue
            record["status"] = "resolved"
            record["resolved_at"] = _timestamp()
            record["resolution_strategy"] = "automatic_sync"
            record["resolution_result"] = note
            _write_record(_conflict_path(record["id"]), record)
            changed = True
        if changed:
            return


def list_conflicts(status: str = "open") -> str:
    if status not in {"open", "resolved", "all"}:
        raise ValueError("status must be one of: open, resolved, all")

    records = [record for record in _list_records() if status == "all" or record["status"] == status]
    if not records:
        if status == "open":
            return "No open conflicts."
        return "No conflicts found."

    lines = []
    for record in records:
        lines.append(
            f"- {record['id']} [{record['status']}] {_repo_label(record['kind'])}: "
            f"{record['summary']} ({_timestamp_label(record['created_at'])})"
        )
    return "\n".join(lines)


def read_conflict(conflict_id: str = "latest") -> str:
    record = _find_record(conflict_id, status="open")
    if record is None and conflict_id == "latest":
        return "No open conflicts."
    if record is None:
        raise ValueError(f"Conflict not found: {conflict_id}")

    lines = [
        f"Conflict ID: {record['id']}",
        f"Where: {_repo_label(record['kind'])}",
        f"Status: {record['status']}",
        f"Opened: {_timestamp_label(record['created_at'])}",
        f"Summary: {record['summary']}",
    ]

    details = record.get("details", "").strip()
    if details:
        lines.append(f"Details:\n{details}")

    backup_path = record.get("backup_path", "").strip()
    if backup_path:
        lines.append(f"Backup patch: {backup_path}")

    lines.append("Options:")
    lines.extend(f"- {item}" for item in _conflict_options(record["id"]))
    return "\n".join(lines)


def _resolve_retry(record: dict) -> str:
    repo_path = Path(record["repo_path"])
    remote_ref = f"{record['remote_name']}/{record['remote_branch']}"

    _abort_merge(repo_path)
    _git(repo_path, "fetch", record["remote_name"], record["remote_branch"])

    if record["kind"] == "app_repo_sync":
        _commit_all_if_needed(repo_path, f"Workspace sync: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        ff_only = _run_git(repo_path, "merge", "--ff-only", remote_ref)
        if ff_only.returncode != 0:
            merge = _run_git(repo_path, "merge", "--no-edit", "--autostash", remote_ref)
            if merge.returncode != 0:
                _abort_merge(repo_path)
                details = merge.stderr.strip() or merge.stdout.strip() or "merge failed"
                raise RuntimeError(details)
    else:
        merge = _run_git(repo_path, "merge", "--no-edit", "--autostash", "-X", "ours", remote_ref)
        if merge.returncode != 0:
            _abort_merge(repo_path)
            details = merge.stderr.strip() or merge.stdout.strip() or "merge failed"
            raise RuntimeError(details)

    push = _run_git(repo_path, "push", record["remote_name"], f"HEAD:{record['remote_branch']}")
    if push.returncode != 0 and "Everything up-to-date" not in push.stdout:
        details = push.stderr.strip() or push.stdout.strip() or "push failed"
        raise RuntimeError(details)
    return "Retried the sync successfully."


def _resolve_keep_local(record: dict) -> str:
    repo_path = Path(record["repo_path"])
    remote_ref = f"{record['remote_name']}/{record['remote_branch']}"

    _abort_merge(repo_path)
    _git(repo_path, "fetch", record["remote_name"], record["remote_branch"])
    _commit_all_if_needed(
        repo_path,
        f"Conflict recovery: keep local changes ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')})",
    )
    merge = _run_git(repo_path, "merge", "-s", "ours", "--no-edit", remote_ref)
    if merge.returncode != 0 and "Already up to date." not in (merge.stderr + merge.stdout):
        details = merge.stderr.strip() or merge.stdout.strip() or "merge failed"
        raise RuntimeError(details)
    _git(repo_path, "push", record["remote_name"], f"HEAD:{record['remote_branch']}")
    return "Resolved the conflict by keeping local changes."


def _resolve_keep_remote(record: dict) -> str:
    repo_path = Path(record["repo_path"])
    remote_ref = f"{record['remote_name']}/{record['remote_branch']}"

    backup_path = _create_backup_patch(repo_path, record["id"])
    _abort_merge(repo_path)
    _git(repo_path, "fetch", record["remote_name"], record["remote_branch"])
    _git(repo_path, "reset", "--hard", remote_ref)
    if backup_path:
        return f"Resolved the conflict by keeping GitHub changes. Local edits were saved to {backup_path}."
    return "Resolved the conflict by keeping GitHub changes."


def resolve_conflict(conflict_id: str = "latest", strategy: str = "retry_sync") -> str:
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of: {', '.join(sorted(STRATEGIES))}")

    with _conflict_lock():
        record = _find_record(conflict_id, status="open")
        if record is None and conflict_id == "latest":
            return "No open conflicts."
        if record is None:
            raise ValueError(f"Conflict not found: {conflict_id}")

    if strategy == "retry_sync":
        result = _resolve_retry(record)
    elif strategy == "keep_local":
        result = _resolve_keep_local(record)
    else:
        result = _resolve_keep_remote(record)

    updated = _find_record(record["id"], status="all")
    if updated is None:
        raise RuntimeError(f"Conflict disappeared while resolving: {record['id']}")

    updated["status"] = "resolved"
    updated["resolved_at"] = _timestamp()
    updated["resolution_strategy"] = strategy
    updated["resolution_result"] = result
    if "saved to " in result:
        updated["backup_path"] = result.rsplit("saved to ", 1)[-1].rstrip(".")

    with _conflict_lock():
        _write_record(_conflict_path(updated["id"]), updated)

    try:
        _send_telegram_notice(
            f"Clawd resolved conflict {updated['id']} in the {_repo_label(updated['kind'])} by "
            f"{STRATEGIES[strategy]}.\n\n{result}"
        )
    except Exception:
        pass

    return result
