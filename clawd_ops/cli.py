import argparse
import asyncio
import json
import sys
from collections.abc import Callable

from clawd_ops.audio import transcribe_voice
from clawd_ops.app_repo import sync_app_repo
from clawd_ops.brain import process_message, tool_specs
from clawd_ops.conflicts import list_conflicts, read_conflict, resolve_conflict
from clawd_ops.search import browse_web, search_papers
from clawd_ops.vault import (
    add_todos,
    forget_memory,
    list_files,
    memory_path,
    read_memory,
    read_notes,
    read_task_list,
    remember_memory,
    save_research,
    task_file_path,
    write_note,
)


def _tool_manifest():
    return {
        "memory_path": memory_path(),
        "task_today": task_file_path("today"),
        "task_yesterday": task_file_path("yesterday"),
        "tools": tool_specs(),
    }


COMMANDS: dict[str, Callable[..., object]] = {
    "add_todos": add_todos,
    "browse_web": browse_web,
    "forget_memory": forget_memory,
    "list_conflicts": list_conflicts,
    "list_files": list_files,
    "memory_path": memory_path,
    "process_message": process_message,
    "read_conflict": read_conflict,
    "read_memory": read_memory,
    "read_notes": read_notes,
    "read_task_list": read_task_list,
    "remember_memory": remember_memory,
    "resolve_conflict": resolve_conflict,
    "save_research": save_research,
    "search_papers": search_papers,
    "sync_app_repo": sync_app_repo,
    "task_file_path": task_file_path,
    "tool_manifest": _tool_manifest,
    "write_note": write_note,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m clawd_ops")
    parser.add_argument("command", choices=sorted([*COMMANDS, "transcribe_voice"]))
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Read JSON payload from stdin or --payload and emit a JSON envelope.",
    )
    parser.add_argument(
        "--payload",
        help="Inline JSON payload. When omitted, --json mode reads from stdin if available.",
    )
    return parser


def _load_payload(args: argparse.Namespace) -> dict:
    if args.payload:
        return json.loads(args.payload)

    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            return json.loads(raw)

    return {}


def _invoke(command: str, payload: dict) -> object:
    if command == "transcribe_voice":
        return asyncio.run(transcribe_voice(**payload))

    handler = COMMANDS[command]
    return handler(**payload)


def _serialize_result(command: str, result: object) -> dict:
    return {"ok": True, "command": command, "result": result, "meta": {"version": 1}}


def _serialize_error(command: str, exc: Exception) -> dict:
    error = {
        "ok": False,
        "command": command,
        "error": {
            "type": exc.__class__.__name__,
            "message": str(exc),
        },
    }
    if hasattr(exc, "conflict_id"):
        error["error"]["conflict_id"] = getattr(exc, "conflict_id")
    return error


def _print_human(result: object) -> None:
    if isinstance(result, str):
        print(result)
        return
    print(json.dumps(result, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        payload = _load_payload(args) if args.json_output or args.payload else {}
        result = _invoke(args.command, payload)
        envelope = _serialize_result(args.command, result)
        if args.json_output:
            print(json.dumps(envelope))
        else:
            _print_human(result)
        return 0
    except Exception as exc:
        envelope = _serialize_error(args.command, exc)
        if args.json_output:
            print(json.dumps(envelope))
        else:
            print(f"{envelope['error']['type']}: {envelope['error']['message']}", file=sys.stderr)
        return 1
