import argparse
import json
import os
from collections.abc import Mapping
from pathlib import Path

DEFAULT_BEDROCK_MODEL_ID = "us.anthropic.claude-opus-4-6-v1"


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise RuntimeError(f"{key} is required to render the OpenClaw config.")
    return value


def build_openclaw_config(
    env: Mapping[str, str],
    *,
    workspace: str | None = None,
    python_exec: str | None = None,
) -> dict[str, object]:
    workspace_dir = (
        workspace or env.get("CLAWD_BRIDGE_CWD", "").strip() or str(Path.cwd())
    )
    python_path = python_exec or env.get("CLAWD_PYTHON_EXEC", "").strip()
    if not python_path:
        python_path = str(Path(workspace_dir) / ".venv" / "bin" / "python")

    region = env.get("AWS_REGION", "us-east-1").strip() or "us-east-1"
    timezone_name = (
        env.get("BOT_TIMEZONE", "America/New_York").strip() or "America/New_York"
    )
    model_id = (
        env.get("BEDROCK_MODEL_ID", DEFAULT_BEDROCK_MODEL_ID).strip()
        or DEFAULT_BEDROCK_MODEL_ID
    )
    transcribe_timeout_seconds = int(
        env.get("TRANSCRIBE_TIMEOUT_SECONDS", "1800").strip() or "1800"
    )
    model_ref = f"amazon-bedrock/{model_id}"
    # Keep the agent on the minimal tool profile, but allow the workspace bridge
    # plus shell execution so bundled CLI-based skills like GitHub can run.
    additional_tools = ["clawd-obsidian", "exec"]

    config = {
        "gateway": {
            "mode": "local",
            "bind": "loopback",
            "auth": {"mode": "none"},
        },
        "logging": {
            "level": "info",
            "consoleLevel": "info",
            "consoleStyle": "pretty",
            "redactSensitive": "tools",
        },
        "session": {
            "scope": "per-sender",
            "reset": {
                "mode": "daily",
                "atHour": 4,
                "idleMinutes": 240,
            },
            "resetTriggers": ["/new", "/reset", "/hardstop"],
            "typingIntervalSeconds": 5,
        },
        "channels": {
            "telegram": {
                "enabled": True,
                "botToken": _required(env, "TELEGRAM_TOKEN"),
                "dmPolicy": "allowlist",
                "allowFrom": [_required(env, "ALLOWED_USER_ID")],
                "groupPolicy": "disabled",
            }
        },
        "models": {
            "bedrockDiscovery": {
                "enabled": False,
                "region": region,
                "providerFilter": ["anthropic"],
                "refreshInterval": 3600,
                "defaultContextWindow": 200000,
                "defaultMaxTokens": 8192,
            },
            "providers": {
                "amazon-bedrock": {
                    "baseUrl": f"https://bedrock-runtime.{region}.amazonaws.com",
                    "api": "bedrock-converse-stream",
                    "auth": "aws-sdk",
                    "models": [
                        {
                            "id": model_id,
                            "name": "Claude Opus 4.6 (Bedrock)",
                            "reasoning": True,
                            "input": ["text", "image"],
                            "cost": {
                                "input": 0,
                                "output": 0,
                                "cacheRead": 0,
                                "cacheWrite": 0,
                            },
                            "contextWindow": 200000,
                            "maxTokens": 8192,
                        }
                    ],
                }
            },
        },
        "plugins": {
            "enabled": True,
            "allow": ["clawd-obsidian"],
            "slots": {"memory": "none"},
            "entries": {
                "clawd-obsidian": {
                    "enabled": True,
                    "config": {
                        "bridgeCwd": workspace_dir,
                        "pythonExec": python_path,
                        "timeoutMs": 60000,
                    },
                }
            },
        },
        "tools": {
            "profile": "minimal",
            "alsoAllow": additional_tools.copy(),
            "media": {
                "audio": {
                    "enabled": True,
                    "maxBytes": 20971520,
                    "echoTranscript": True,
                    "echoFormat": "Heard:\n{transcript}",
                    "models": [
                        {
                            "provider": "openai",
                            "model": "gpt-4o-mini-transcribe",
                        },
                    ],
                }
            },
        },
        "messages": {
            "tts": {
                "auto": "tagged",
                "provider": "openai",
                "openai": {
                    "model": "gpt-4o-mini-tts",
                    "voice": "alloy",
                },
                "edge": {
                    "enabled": True,
                    "voice": "en-US-GuyNeural",
                    "lang": "en-US",
                },
                "modelOverrides": {
                    "enabled": True,
                },
            }
        },
        "agents": {
            "defaults": {
                "workspace": workspace_dir,
                "repoRoot": workspace_dir,
                "userTimezone": timezone_name,
                "model": {"primary": model_ref},
            },
            "list": [
                {
                    "id": "main",
                    "default": True,
                    "name": "Clawd",
                    "tools": {
                        "alsoAllow": additional_tools.copy(),
                    },
                    "identity": {
                        "name": "Clawd",
                        "theme": "concise Obsidian workflow assistant",
                        "emoji": "🦞",
                    },
                }
            ],
        },
    }

    hooks = dict(config.get("hooks", {}))
    hooks.update(
        {
            "enabled": True,
            "token": env.get("OPENCLAW_HOOK_TOKEN", "").strip() or "change-me",
            "path": "/hooks",
            "presets": ["gmail"],
        }
    )
    config["hooks"] = hooks
    return config


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m clawd_ops.openclaw_config")
    parser.add_argument("--output")
    parser.add_argument("--workspace")
    parser.add_argument("--python-exec")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = build_openclaw_config(
        os.environ,
        workspace=args.workspace,
        python_exec=args.python_exec,
    )
    rendered = json.dumps(config, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
