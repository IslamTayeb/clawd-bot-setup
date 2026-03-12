import json
import os
import subprocess
import sys


def run_cli(command: str, payload: dict | None = None, env: dict | None = None):
    return subprocess.run(
        [sys.executable, "-m", "clawd_ops", command, "--json"],
        input=json.dumps(payload or {}),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_cli_json_envelope_success(git_vault):
    result = run_cli("task_file_path", {"target_date": "today"}, os.environ.copy())
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["result"].startswith("tasks/")


def test_cli_json_envelope_error(git_vault):
    result = run_cli("remember_memory", {"memory": ""}, os.environ.copy())
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "ValueError"


def test_cli_list_conflicts_no_open(git_vault):
    env = os.environ.copy()
    env["CLAWD_STATE_DIR"] = str(git_vault.parent / "state")
    result = run_cli("list_conflicts", {"status": "open"}, env)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["result"] == "No open conflicts."
