import json
import os
import re
import shlex
import subprocess
from typing import Any


GOG_BIN = os.environ.get("GOG_BIN", "gog")


def _run_gog(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    command = [GOG_BIN, *args]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "NO_COLOR": "1"},
    )
    if result.returncode != 0:
        details = (
            result.stderr.strip()
            or result.stdout.strip()
            or f"exit code {result.returncode}"
        )
        raise RuntimeError(f"{shlex.join(command)} failed: {details}")
    return result


def _maybe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text.strip()


def _extract_first_url(text: str) -> str:
    match = re.search(r"https?://\S+", text)
    return match.group(0) if match else ""


def list_google_auth_accounts() -> Any:
    result = _run_gog("auth", "list", "--json", "--results-only")
    return _maybe_json(result.stdout)


def list_google_auth_credentials() -> Any:
    result = _run_gog("auth", "credentials", "list", "--json", "--results-only")
    return _maybe_json(result.stdout)


def set_google_auth_credentials(credentials_path: str) -> str:
    cleaned_path = credentials_path.strip()
    if not cleaned_path:
        raise ValueError("credentials_path must not be empty")
    result = _run_gog("auth", "credentials", "set", cleaned_path, timeout=180)
    return (
        result.stdout.strip() or f"Stored Google OAuth credentials from {cleaned_path}."
    )


def start_google_auth(
    email: str,
    services: str = "gmail,calendar",
    readonly: bool = False,
    client: str = "",
) -> dict[str, Any]:
    cleaned_email = email.strip()
    if not cleaned_email:
        raise ValueError("email must not be empty")

    command = [
        "auth",
        "add",
        cleaned_email,
        "--services",
        services,
        "--remote",
        "--step=1",
        "--timeout",
        "10m",
    ]
    if readonly:
        command.append("--readonly")
    if client.strip():
        command.extend(["--client", client.strip()])
    result = _run_gog(*command, timeout=180)
    output = result.stdout.strip() or result.stderr.strip()
    return {
        "email": cleaned_email,
        "services": services,
        "url": _extract_first_url(output),
        "output": output,
    }


def finish_google_auth(
    email: str,
    auth_url: str,
    services: str = "gmail,calendar",
    readonly: bool = False,
    client: str = "",
) -> str:
    cleaned_email = email.strip()
    cleaned_auth_url = auth_url.strip()
    if not cleaned_email:
        raise ValueError("email must not be empty")
    if not cleaned_auth_url:
        raise ValueError("auth_url must not be empty")

    command = [
        "auth",
        "add",
        cleaned_email,
        "--services",
        services,
        "--remote",
        "--step=2",
        "--auth-url",
        cleaned_auth_url,
    ]
    if readonly:
        command.append("--readonly")
    if client.strip():
        command.extend(["--client", client.strip()])
    result = _run_gog(*command, timeout=180)
    return result.stdout.strip() or f"Stored Google auth for {cleaned_email}."
