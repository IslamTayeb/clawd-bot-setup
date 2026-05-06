import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from clawd_ops import vault
from tests.conftest import run_git


def test_task_file_path_resolves_relative_dates(monkeypatch):
    monkeypatch.setattr(
        vault,
        "_local_now",
        lambda: datetime(2026, 3, 10, 12, tzinfo=ZoneInfo("America/New_York")),
    )
    assert vault.task_file_path("today") == "tasks/W11-260309.md"
    assert vault.task_file_path("yesterday") == "tasks/W11-260309.md"
    assert vault.task_file_path("tomorrow") == "tasks/W11-260309.md"
    assert vault.task_file_path("March 2, 2026") == "tasks/W10-260302.md"


def test_add_todos_appends_to_existing_task_file(git_vault, monkeypatch):
    monkeypatch.setattr(
        vault,
        "_local_now",
        lambda: datetime(2026, 3, 10, 12, tzinfo=ZoneInfo("America/New_York")),
    )
    task_path = git_vault / "tasks" / "W11-260309.md"
    task_path.write_text("- [ ] existing\n", encoding="utf-8")
    run_git(git_vault, "add", "tasks/W11-260309.md")
    run_git(git_vault, "commit", "-m", "Seed task file")
    run_git(git_vault, "push")

    message = vault.add_todos(["buy milk"], "today")

    assert message == "Added 1 todo(s) to tasks/W11-260309.md."
    assert task_path.read_text(encoding="utf-8") == "- [ ] existing\n- [ ] buy milk\n"
    assert run_git(git_vault, "status", "--short") == ""


def test_add_todos_writes_weekly_and_preserves_legacy_daily_reads(
    git_vault, monkeypatch
):
    monkeypatch.setattr(
        vault,
        "_local_now",
        lambda: datetime(2026, 3, 10, 12, tzinfo=ZoneInfo("America/New_York")),
    )
    daily_path = git_vault / "tasks" / "260310.md"
    weekly_path = git_vault / "tasks" / "W11-260309.md"
    daily_path.write_text("- [ ] legacy daily\n", encoding="utf-8")
    run_git(git_vault, "add", "tasks/260310.md")
    run_git(git_vault, "commit", "-m", "Seed daily task file")
    run_git(git_vault, "push")

    message = vault.add_todos(["buy milk"], "today")

    assert message == "Added 1 todo(s) to tasks/W11-260309.md."
    assert daily_path.exists()
    assert weekly_path.read_text(encoding="utf-8") == "- [ ] buy milk\n"
    task_list = vault.read_task_list("today")
    assert "## tasks/W11-260309.md" in task_list
    assert "- [ ] buy milk" in task_list
    assert "## tasks/260310.md" in task_list
    assert "- [ ] legacy daily" in task_list
    assert run_git(git_vault, "status", "--short") == ""


def test_add_todos_carries_forward_unchecked_tasks_from_previous_week(
    git_vault, monkeypatch
):
    monkeypatch.setattr(
        vault,
        "_local_now",
        lambda: datetime(2026, 3, 10, 12, tzinfo=ZoneInfo("America/New_York")),
    )
    previous_path = git_vault / "tasks" / "W10-260302.md"
    current_path = git_vault / "tasks" / "W11-260309.md"
    previous_path.write_text(
        "- [ ] carry this\n- [x] done already\n  - [ ] nested carry\n",
        encoding="utf-8",
    )
    run_git(git_vault, "add", "tasks/W10-260302.md")
    run_git(git_vault, "commit", "-m", "Seed previous weekly tasks")
    run_git(git_vault, "push")

    message = vault.add_todos(["new task"], "today")

    assert message == "Added 1 todo(s) to tasks/W11-260309.md."
    assert current_path.read_text(encoding="utf-8") == (
        "- [ ] carry this\n  - [ ] nested carry\n- [ ] new task\n"
    )
    assert run_git(git_vault, "status", "--short") == ""
    assert run_git(git_vault, "status", "--short") == ""


def test_migrate_task_filenames_renames_legacy_files(git_vault):
    legacy_paths = {
        "031026.md": "alpha\n",
        "111925.md": "beta\n",
    }
    for filename, content in legacy_paths.items():
        (git_vault / "tasks" / filename).write_text(content, encoding="utf-8")
    run_git(git_vault, "add", "tasks")
    run_git(git_vault, "commit", "-m", "Seed legacy task files")
    run_git(git_vault, "push")

    message = vault.migrate_task_filenames(sync=True)

    assert "tasks/031026.md -> tasks/260310.md" in message
    assert "tasks/111925.md -> tasks/251119.md" in message
    assert not (git_vault / "tasks" / "031026.md").exists()
    assert not (git_vault / "tasks" / "111925.md").exists()
    assert (git_vault / "tasks" / "260310.md").read_text(encoding="utf-8") == "alpha\n"
    assert (git_vault / "tasks" / "251119.md").read_text(encoding="utf-8") == "beta\n"
    assert run_git(git_vault, "status", "--short") == ""


def test_add_world_breaking_idea_appends_entry_and_commits(git_vault, monkeypatch):
    monkeypatch.setattr(
        vault,
        "_local_now",
        lambda: datetime(2026, 3, 10, 12, tzinfo=ZoneInfo("America/New_York")),
    )

    result = vault.add_world_breaking_idea("AI tutor that audits lab habits")

    assert result == {
        "id": "2026-03-10-ai-tutor-that-audits-lab-habits",
        "idea_path": "world-breaking-ideas.md",
        "report_path": "research/world-breaking-ideas/2026-03-10-ai-tutor-that-audits-lab-habits.md",
    }
    text = (git_vault / "world-breaking-ideas.md").read_text(encoding="utf-8")
    assert "# World-Breaking Ideas" in text
    assert "AI tutor that audits lab habits" in text
    assert (
        "research/world-breaking-ideas/2026-03-10-ai-tutor-that-audits-lab-habits.md"
        in text
    )
    assert run_git(git_vault, "status", "--short") == ""


def test_memory_roundtrip(git_vault):
    stored = vault.remember_memory("Prefers concise replies")
    assert stored == "Stored memory in memory/clawd.md under Preferences."
    assert (vault._project_root() / "memory" / "clawd.md").exists()
    assert "Prefers concise replies" in vault.read_memory()
    removed = vault.forget_memory("concise replies")
    assert removed == "Removed 1 memory item(s) from memory/clawd.md."
    assert run_git(git_vault, "status", "--short") == ""


def test_memory_roundtrip_does_not_require_obsidian_vault(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(vault, "_project_root", lambda: workspace)
    monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)
    monkeypatch.delenv("CLAWD_MEMORY_PATH", raising=False)

    stored = vault.remember_memory("Prefers direct recommendations")

    assert stored == "Stored memory in memory/clawd.md under Preferences."
    assert "Prefers direct recommendations" in vault.read_memory(sync=False)
    assert (
        vault.forget_memory("direct recommendations")
        == "Removed 1 memory item(s) from memory/clawd.md."
    )


def test_email_filter_roundtrip(git_vault):
    stored = vault.add_email_filter("suppress_topic", "duke daily newsletter")

    assert stored == "Stored email filter in memory/clawd.md under Email Filters."
    rules = vault.list_email_filters()
    assert rules["suppress_topic"] == ["duke daily newsletter"]

    removed = vault.remove_email_filter("duke daily", kind="suppress_topic")
    assert removed == "Removed 1 email filter(s) from memory/clawd.md."
    assert vault.list_email_filters()["suppress_topic"] == []
    assert run_git(git_vault, "status", "--short") == ""


def test_write_note_rejects_path_escape(git_vault):
    try:
        vault.write_note("../escape.md", "nope")
    except ValueError as exc:
        assert "escapes the vault" in str(exc)
    else:
        raise AssertionError("expected path escape rejection")


def test_list_files_only_returns_readable_formats(git_vault):
    (git_vault / "personal" / "note.md").write_text("hi\n", encoding="utf-8")
    (git_vault / "personal" / "ignore.txt").write_text("hi\n", encoding="utf-8")
    files = vault.list_files("personal").splitlines()
    assert files == ["personal/note.md"]


def test_git_push_recovers_non_fast_forward(git_vault, tmp_path):
    local_note = git_vault / "personal" / "local.md"
    local_note.write_text("local\n", encoding="utf-8")

    other_clone = tmp_path / "other"
    run_git(tmp_path, "clone", str(tmp_path / "remote.git"), str(other_clone))
    run_git(other_clone, "config", "user.name", "Other User")
    run_git(other_clone, "config", "user.email", "other@example.com")
    (other_clone / "personal").mkdir(parents=True, exist_ok=True)
    (other_clone / "personal" / "remote.md").write_text("remote\n", encoding="utf-8")
    run_git(other_clone, "add", "personal/remote.md")
    run_git(other_clone, "commit", "-m", "Remote change")
    run_git(other_clone, "push")

    vault.git_push("Local change", [local_note])

    assert run_git(git_vault, "status", "--short") == ""
    remote_check = tmp_path / "check"
    run_git(tmp_path, "clone", str(tmp_path / "remote.git"), str(remote_check))
    assert (remote_check / "personal" / "local.md").read_text(
        encoding="utf-8"
    ) == "local\n"
    assert (remote_check / "personal" / "remote.md").read_text(
        encoding="utf-8"
    ) == "remote\n"


def test_sync_with_remote_uses_merge_only(monkeypatch):
    commands: list[tuple[str, ...]] = []

    def fake_git(*args: str) -> str:
        commands.append(args)
        return ""

    def fake_run_git(*args: str) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(vault, "_git", fake_git)
    monkeypatch.setattr(vault, "_run_git", fake_run_git)
    monkeypatch.setattr(vault, "_vault", lambda: Path("/tmp/fake-vault"))
    monkeypatch.setattr(vault, "clear_conflicts", lambda *args, **kwargs: None)

    vault._sync_with_remote("origin", "main")

    assert commands == [
        ("fetch", "origin", "main"),
        ("merge", "--no-edit", "--autostash", "-X", "ours", "origin/main"),
    ]
