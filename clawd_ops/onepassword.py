import json
import os
import shlex
import subprocess
from typing import Any


OP_BIN = os.environ.get("OP_BIN", "op")


def _run_op(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    command = [OP_BIN, *args]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "OP_FORMAT": "json"},
    )
    if result.returncode != 0:
        details = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit code {result.returncode}"
        )
        raise RuntimeError(f"{shlex.join(command)} failed: {details}")
    return result


def _parse_json_output(text: str) -> Any:
    payload = text.strip()
    if not payload:
        return None
    return json.loads(payload)


def _failure_details(result: subprocess.CompletedProcess[str]) -> str:
    return (
        result.stderr.strip()
        or result.stdout.strip()
        or f"exit code {result.returncode}"
    )


def list_1password_accounts() -> Any:
    result = _run_op("account", "list")
    return _parse_json_output(result.stdout)


def whoami_1password() -> Any:
    result = subprocess.run(
        [OP_BIN, "whoami"],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "OP_FORMAT": "json"},
    )
    if result.returncode != 0:
        return {"signed_in": False, "error": _failure_details(result)}
    return _parse_json_output(result.stdout)


def list_1password_vaults() -> Any:
    result = subprocess.run(
        [OP_BIN, "vault", "list"],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "OP_FORMAT": "json"},
    )
    if result.returncode != 0:
        details = _failure_details(result).lower()
        if "no accounts configured" in details or "not signed in" in details:
            return []
        raise RuntimeError(
            f"{shlex.join([OP_BIN, 'vault', 'list'])} failed: {_failure_details(result)}"
        )
    return _parse_json_output(result.stdout)


def get_1password_item(item: str, vault: str = "") -> Any:
    cleaned_item = item.strip()
    if not cleaned_item:
        raise ValueError("item must not be empty")
    command = ["item", "get", cleaned_item]
    if vault.strip():
        command.extend(["--vault", vault.strip()])
    result = _run_op(*command)
    return _parse_json_output(result.stdout)


def read_1password_secret(secret_reference: str) -> str:
    cleaned_reference = secret_reference.strip()
    if not cleaned_reference:
        raise ValueError("secret_reference must not be empty")
    result = _run_op("read", cleaned_reference)
    return result.stdout.strip()
