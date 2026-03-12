from pathlib import Path

from clawd_ops import conflicts
from tests.conftest import run_git


def test_report_conflict_records_and_formats(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWD_STATE_DIR", str(tmp_path / "state"))
    sent_messages = []
    monkeypatch.setattr(conflicts, "_send_telegram_notice", lambda text: sent_messages.append(text) or True)

    conflict_id = conflicts.report_conflict(
        kind="vault_sync",
        summary="Could not merge vault changes from origin/main.",
        details="git merge failed: CONFLICT (content): Merge conflict in tasks/260312.md",
        repo_path=str(tmp_path / "vault"),
    )

    assert sent_messages
    assert conflict_id in sent_messages[0]
    assert conflict_id in conflicts.list_conflicts()
    details = conflicts.read_conflict(conflict_id)
    assert "Options:" in details
    assert "keeping GitHub changes" in details


def test_resolve_conflict_keep_remote_creates_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWD_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(conflicts, "_send_telegram_notice", lambda text: True)

    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    other = tmp_path / "other"

    run_git(tmp_path, "init", "--bare", str(remote))
    run_git(tmp_path, "clone", str(remote), str(repo))
    run_git(repo, "config", "user.name", "Clawd Tests")
    run_git(repo, "config", "user.email", "tests@example.com")
    run_git(repo, "branch", "-M", "main")

    note = repo / "note.md"
    note.write_text("initial\n", encoding="utf-8")
    run_git(repo, "add", "note.md")
    run_git(repo, "commit", "-m", "Initial")
    run_git(repo, "push", "-u", "origin", "main")
    run_git(remote, "symbolic-ref", "HEAD", "refs/heads/main")

    run_git(tmp_path, "clone", str(remote), str(other))
    run_git(other, "config", "user.name", "Other User")
    run_git(other, "config", "user.email", "other@example.com")
    (other / "note.md").write_text("remote version\n", encoding="utf-8")
    run_git(other, "add", "note.md")
    run_git(other, "commit", "-m", "Remote change")
    run_git(other, "push")

    note.write_text("local version\n", encoding="utf-8")

    conflict_id = conflicts.report_conflict(
        kind="app_repo_sync",
        summary="Could not merge app repo changes from origin/main.",
        details="git merge failed: local and remote changes differ",
        repo_path=str(repo),
    )

    result = conflicts.resolve_conflict(conflict_id, "keep_remote")

    assert "keeping GitHub changes" in result
    assert note.read_text(encoding="utf-8") == "remote version\n"
    backup_path = Path(conflicts._find_record(conflict_id, status="all")["backup_path"])
    assert backup_path.exists()
