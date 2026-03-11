import contextlib
import os
import re
import shlex
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_MEMORY_PATH = "personal/clawd.md"
DEFAULT_MEMORY_SECTIONS = (
    "Preferences",
    "Tone",
    "Projects",
    "Open Loops",
    "Reference",
)
MARKDOWN_SUFFIXES = {".md", ".markdown"}
LEGACY_TASK_FILE_RE = re.compile(r"^\d{6}\.md$")

try:
    import fcntl
except ImportError:  # pragma: no cover - only for unsupported platforms
    fcntl = None


def _vault() -> Path:
    vault_path = os.environ.get("OBSIDIAN_VAULT")
    if not vault_path:
        raise RuntimeError("OBSIDIAN_VAULT is not set.")

    path = Path(vault_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Obsidian vault not found: {path}")
    return path


def _resolve_in_vault(relative_path: str) -> Path:
    vault = _vault()
    clean_path = relative_path.strip().lstrip("/")
    candidate = (vault / clean_path).resolve()
    if not candidate.is_relative_to(vault):
        raise ValueError(f"Path escapes the vault: {relative_path}")
    return candidate

@contextlib.contextmanager
def _vault_lock():
    git_dir = _vault() / ".git"
    lock_path = git_dir / "clawd-bot.lock" if git_dir.exists() else _vault() / ".clawd-bot.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

def _git_env() -> dict[str, str]:
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

def _run_git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=_vault(),
        capture_output=True,
        text=True,
        timeout=30,
        env=_git_env(),
    )


def _git(*args: str) -> str:
    result = _run_git(*args)
    if result.returncode != 0:
        command = shlex.join(["git", *args])
        details = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"{command} failed: {details}")
    return result.stdout.strip()


def _git_upstream() -> tuple[str, str] | None:
    result = _run_git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if result.returncode != 0:
        return None

    upstream = result.stdout.strip()
    if "/" not in upstream:
        return None

    remote, branch = upstream.split("/", 1)
    return remote, branch


def _is_known_git_path(path: Path) -> bool:
    rel_path = str(path.resolve().relative_to(_vault()))
    if path.exists():
        return True
    result = _run_git("ls-files", "--error-unmatch", "--", rel_path)
    return result.returncode == 0


def _sync_with_remote(remote: str, branch: str) -> None:
    _git("fetch", remote, branch)

    rebase = _run_git("rebase", "--autostash", f"{remote}/{branch}")
    if rebase.returncode == 0:
        return

    _run_git("rebase", "--abort")
    merge = _run_git("merge", "--no-edit", "-X", "ours", f"{remote}/{branch}")
    if merge.returncode != 0:
        command = shlex.join(["git", "merge", "--no-edit", "-X", "ours", f"{remote}/{branch}"])
        details = merge.stderr.strip() or merge.stdout.strip() or f"exit code {merge.returncode}"
        raise RuntimeError(f"{command} failed after rebase conflict: {details}")


def git_pull() -> None:
    upstream = _git_upstream()
    if upstream is None:
        return

    remote, branch = upstream
    _sync_with_remote(remote, branch)


def git_push(message: str, paths: list[Path] | None = None) -> None:
    if paths:
        rel_paths = [str(path.resolve().relative_to(_vault())) for path in paths if _is_known_git_path(path)]
        if rel_paths:
            _git("add", "-A", "--", *rel_paths)
        else:
            _git("add", "-A")
    else:
        _git("add", "-A")

    staged_changes = _run_git("diff", "--cached", "--quiet", "--exit-code")
    if staged_changes.returncode == 0:
        return

    _git("commit", "-m", message)

    upstream = _git_upstream()
    if upstream is None:
        _git("push")
        return

    remote, branch = upstream
    push = _run_git("push", remote, f"HEAD:{branch}")
    if push.returncode == 0:
        return

    details = push.stderr.strip() or push.stdout.strip()
    if "non-fast-forward" not in details and "fetch first" not in details:
        command = shlex.join(["git", "push", remote, f"HEAD:{branch}"])
        raise RuntimeError(f"{command} failed: {details or 'push rejected'}")

    _sync_with_remote(remote, branch)
    _git("push", remote, f"HEAD:{branch}")


def _now_label() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _bot_timezone():
    timezone_name = os.environ.get("BOT_TIMEZONE", "America/New_York").strip() or "America/New_York"
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return timezone.utc


def _local_now() -> datetime:
    return datetime.now(_bot_timezone())


def _resolve_task_date(target_date: str = "today"):
    raw_value = " ".join(target_date.split()).strip()
    normalized = raw_value.lower().rstrip(".")
    normalized = normalized.removesuffix("'s")
    normalized = (
        normalized.removesuffix("s")
        if normalized in {"todays", "yesterdays", "tomorrows"}
        else normalized
    )
    raw_value = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", raw_value, flags=re.IGNORECASE)
    today = _local_now().date()

    if normalized in {"", "today"}:
        return today
    if normalized == "yesterday":
        return today - timedelta(days=1)
    if normalized == "tomorrow":
        return today + timedelta(days=1)

    for fmt in (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%m/%d",
        "%m-%d-%Y",
        "%m-%d-%y",
        "%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%B %d",
        "%b %d",
    ):
        try:
            parse_value = raw_value
            parse_fmt = fmt
            if "%Y" not in fmt and "%y" not in fmt:
                parse_value = f"{raw_value} {today.year}"
                parse_fmt = f"{fmt} %Y"
            parsed = datetime.strptime(parse_value, parse_fmt)
            return parsed.date()
        except ValueError:
            continue

    raise ValueError(f"Unsupported task date: {target_date}")


def task_file_path(target_date: str = "today") -> str:
    task_date = _resolve_task_date(target_date)
    return f"tasks/{task_date.strftime('%y%m%d')}.md"


def _legacy_task_file_path_for(task_date) -> str:
    return f"tasks/{task_date.strftime('%m%d%y')}.md"


def _task_paths(target_date: str = "today") -> tuple[Path, Path]:
    task_date = _resolve_task_date(target_date)
    preferred = _resolve_in_vault(task_file_path(target_date))
    legacy = _resolve_in_vault(_legacy_task_file_path_for(task_date))
    return preferred, legacy


def _coalesce_task_file(target_date: str = "today", migrate_legacy: bool = False) -> Path:
    preferred, legacy = _task_paths(target_date)
    if preferred.exists() and legacy.exists() and preferred != legacy:
        if not migrate_legacy:
            return preferred

        preferred_text = preferred.read_text(encoding="utf-8").strip()
        legacy_text = legacy.read_text(encoding="utf-8").strip()
        if preferred_text != legacy_text and legacy_text:
            merged_parts = [part for part in (preferred_text, legacy_text) if part]
            preferred.write_text("\n\n".join(merged_parts) + "\n", encoding="utf-8")
        legacy.unlink()
        return preferred

    if preferred.exists():
        return preferred
    if legacy.exists():
        if migrate_legacy and preferred != legacy:
            legacy.rename(preferred)
            return preferred
        return legacy
    return preferred


def _memory_relative_path() -> str:
    configured = os.environ.get("CLAWD_MEMORY_PATH", DEFAULT_MEMORY_PATH).strip()
    return configured or DEFAULT_MEMORY_PATH


def memory_path() -> str:
    return _memory_relative_path()


def _memory_file() -> Path:
    return _resolve_in_vault(_memory_relative_path())


def _normalize_memory_section(section: str) -> str:
    cleaned = " ".join(section.split()).strip()
    return cleaned.title() if cleaned else "Preferences"


def _normalize_memory_item(item: str) -> str:
    return re.sub(r"\s+", " ", item).strip()


def _parse_memory_sections(text: str) -> dict[str, list[str]]:
    sections = {section: [] for section in DEFAULT_MEMORY_SECTIONS}
    current_section: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("## "):
            current_section = _normalize_memory_section(line[3:])
            sections.setdefault(current_section, [])
            continue
        if line.startswith("- ") and current_section:
            item = _normalize_memory_item(line[2:])
            if item:
                sections[current_section].append(item)

    return sections


def _render_memory_sections(sections: dict[str, list[str]]) -> str:
    ordered_sections = list(DEFAULT_MEMORY_SECTIONS)
    ordered_sections.extend(section for section in sections if section not in ordered_sections)

    lines = [
        "# Clawd Memory",
        "",
        "Persistent preferences and long-lived context for the Telegram assistant.",
        "",
    ]

    has_items = False
    for section in ordered_sections:
        items = sections.get(section, [])
        if not items:
            continue
        has_items = True
        lines.append(f"## {section}")
        lines.extend(f"- {item}" for item in items)
        lines.append("")

    if not has_items:
        lines.extend(
            [
                "## Preferences",
                "- Add memories by asking Clawd to remember something for later.",
                "",
            ]
        )

    lines.append(f"_Last updated: {_now_label()}_")
    return "\n".join(lines).strip() + "\n"


def _read_memory_file(sync: bool) -> tuple[Path, str]:
    if sync:
        git_pull()

    path = _memory_file()
    if not path.exists():
        return path, ""
    return path, path.read_text(encoding="utf-8")


def read_memory(sync: bool = True) -> str:
    with _vault_lock():
        path, text = _read_memory_file(sync=sync)
        rel_path = path.relative_to(_vault())
        if not text.strip():
            return f"Memory file: {rel_path}\n\nNo persistent memory stored yet."
        return f"Memory file: {rel_path}\n\n{text.strip()}"


def memory_context(sync: bool = True) -> str:
    with _vault_lock():
        try:
            _, text = _read_memory_file(sync=sync)
        except Exception:
            return "No persistent memory stored yet."

        return text.strip() or "No persistent memory stored yet."


def remember_memory(memory: str, section: str = "Preferences") -> str:
    with _vault_lock():
        item = _normalize_memory_item(memory)
        if not item:
            raise ValueError("memory must not be empty")

        path, existing_text = _read_memory_file(sync=True)
        path.parent.mkdir(parents=True, exist_ok=True)

        normalized_section = _normalize_memory_section(section)
        sections = _parse_memory_sections(existing_text)
        target_items = sections.setdefault(normalized_section, [])

        if any(existing.casefold() == item.casefold() for existing in target_items):
            return f"Memory already stored in {path.relative_to(_vault())}."

        target_items.append(item)
        path.write_text(_render_memory_sections(sections), encoding="utf-8")
        git_push(f"Update Clawd memory: {normalized_section}", [path])
        return f"Stored memory in {path.relative_to(_vault())} under {normalized_section}."


def forget_memory(query: str, section: str = "") -> str:
    with _vault_lock():
        cleaned_query = _normalize_memory_item(query)
        if not cleaned_query:
            raise ValueError("query must not be empty")

        path, existing_text = _read_memory_file(sync=True)
        if not existing_text.strip():
            return f"No memory stored in {path.relative_to(_vault())}."

        target_section = _normalize_memory_section(section) if section.strip() else ""
        sections = _parse_memory_sections(existing_text)
        removed_count = 0

        for section_name, items in sections.items():
            if target_section and section_name != target_section:
                continue
            kept_items = []
            for item in items:
                if cleaned_query.casefold() in item.casefold():
                    removed_count += 1
                    continue
                kept_items.append(item)
            sections[section_name] = kept_items

        if removed_count == 0:
            return f"No memory matched '{cleaned_query}' in {path.relative_to(_vault())}."

        path.write_text(_render_memory_sections(sections), encoding="utf-8")
        git_push("Prune Clawd memory", [path])
        return f"Removed {removed_count} memory item(s) from {path.relative_to(_vault())}."


def _note_commit_message(action: str, path: Path) -> str:
    rel_path = path.relative_to(_vault())
    return f"{action}: {str(rel_path)[:60]}"


def write_note(path: str, content: str, mode: str = "overwrite") -> str:
    with _vault_lock():
        git_pull()

        target = _resolve_in_vault(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        normalized_mode = mode.strip().lower() or "overwrite"
        existing = target.read_text(encoding="utf-8") if target.exists() else ""

        if normalized_mode == "overwrite":
            updated = content
        elif normalized_mode == "append":
            separator = "\n" if existing and not existing.endswith("\n") else ""
            updated = f"{existing}{separator}{content}"
        elif normalized_mode == "prepend":
            separator = "\n" if content and not content.endswith("\n") and existing else ""
            updated = f"{content}{separator}{existing}"
        else:
            raise ValueError("mode must be one of: overwrite, append, prepend")

        if updated and not updated.endswith("\n"):
            updated += "\n"

        target.write_text(updated, encoding="utf-8")
        git_push(_note_commit_message("Update note", target), [target])
        return f"Updated {target.relative_to(_vault())} using {normalized_mode} mode."


def read_task_list(target_date: str = "today") -> str:
    with _vault_lock():
        git_pull()

        target = _coalesce_task_file(target_date)
        if not target.exists():
            return f"Task file not found: {task_file_path(target_date)}"
        return target.read_text(encoding="utf-8")


def add_todos(items: list[str], target_date: str = "today") -> str:
    with _vault_lock():
        git_pull()

        preferred, legacy = _task_paths(target_date)
        target = _coalesce_task_file(target_date, migrate_legacy=True)
        target.parent.mkdir(parents=True, exist_ok=True)

        lines = []
        for item in items:
            if item.startswith("\t"):
                indent = item[: len(item) - len(item.lstrip("\t"))]
                lines.append(f"{indent}- [ ] {_normalize_memory_item(item.lstrip(chr(9)))}")
            else:
                lines.append(f"- [ ] {_normalize_memory_item(item)}")

        addition = "\n".join(lines).strip()
        if not addition:
            raise ValueError("items must contain at least one todo")

        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        separator = "\n" if existing and not existing.endswith("\n") else ""
        updated = f"{existing}{separator}{addition}\n"
        target.write_text(updated, encoding="utf-8")

        commit_paths = [target]
        if legacy != target:
            commit_paths.append(legacy)
        if preferred != target and preferred not in commit_paths:
            commit_paths.append(preferred)

        git_push(f"Add todos for {target.stem}", commit_paths)
        return f"Added {len(items)} todo(s) to {target.relative_to(_vault())}."


def migrate_task_filenames(sync: bool = True) -> str:
    with _vault_lock():
        if sync:
            git_pull()

        tasks_dir = _resolve_in_vault("tasks")
        if not tasks_dir.exists():
            return "No tasks directory found."

        migrated = []
        staged_paths: list[Path] = []
        for path in sorted(tasks_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() != ".md":
                continue
            if not LEGACY_TASK_FILE_RE.match(path.name):
                continue

            try:
                task_date = datetime.strptime(path.stem, "%m%d%y").date()
            except ValueError:
                continue

            target = path.with_name(task_date.strftime("%y%m%d") + ".md")
            if target == path:
                continue

            if target.exists():
                existing_text = target.read_text(encoding="utf-8").strip()
                legacy_text = path.read_text(encoding="utf-8").strip()
                if existing_text != legacy_text and legacy_text:
                    merged_parts = [part for part in (existing_text, legacy_text) if part]
                    target.write_text("\n\n".join(merged_parts) + "\n", encoding="utf-8")
                path.unlink()
            else:
                path.rename(target)

            migrated.append((path.relative_to(_vault()), target.relative_to(_vault())))
            staged_paths.extend([path, target])

        if not migrated:
            return "No legacy task files needed migration."

        git_push("Migrate task filenames to YYMMDD", staged_paths)
        lines = ["Migrated task files to YYMMDD:"]
        lines.extend(f"- {old_path} -> {new_path}" for old_path, new_path in migrated)
        return "\n".join(lines)


def save_research(title: str, content: str) -> str:
    with _vault_lock():
        git_pull()

        research_dir = _resolve_in_vault("research")
        research_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "research-note"
        target = research_dir / f"{slug}.md"

        if target.exists():
            existing = target.read_text(encoding="utf-8")
            updated = f"{existing.rstrip()}\n\n---\n\n{content.strip()}\n"
        else:
            updated = f"# {title}\n\n{content.strip()}\n"

        target.write_text(updated, encoding="utf-8")
        git_push(f"Research: {title[:50]}", [target])
        return f"Saved research to {target.relative_to(_vault())}."


def read_notes(path: str) -> str:
    with _vault_lock():
        git_pull()

        target = _resolve_in_vault(path)
        if not target.exists() or not target.is_file():
            return f"File not found: {path}"
        return target.read_text(encoding="utf-8")


def list_files(folder: str = "") -> str:
    with _vault_lock():
        git_pull()

        target = _resolve_in_vault(folder)
        if not target.exists() or not target.is_dir():
            return f"Folder not found: {folder}"

        files = []
        for path in sorted(target.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in MARKDOWN_SUFFIXES:
                continue
            files.append(str(path.relative_to(_vault())))

        return "\n".join(files) if files else "No markdown files found."
