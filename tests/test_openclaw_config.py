from pathlib import Path

from clawd_ops.openclaw_config import build_openclaw_config


def _build_config(tmp_path, extra_env=None):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    python_exec = workspace / ".venv" / "bin" / "python"
    python_exec.parent.mkdir(parents=True, exist_ok=True)
    python_exec.write_text("", encoding="utf-8")
    env = {
        "TELEGRAM_TOKEN": "123:abc",
        "ALLOWED_USER_ID": "8383879897",
        "AWS_REGION": "us-east-1",
    }
    if extra_env:
        env.update(extra_env)
    return build_openclaw_config(
        env, workspace=str(workspace), python_exec=str(python_exec)
    ), str(python_exec)


def test_build_openclaw_config_preserves_model_id_and_bridges_python(tmp_path):
    config, python_exec = _build_config(
        tmp_path,
        {
            "BEDROCK_MODEL_ID": "us.anthropic.claude-opus-4-7",
            "BOT_TIMEZONE": "America/New_York",
        },
    )

    assert config["models"]["bedrockDiscovery"]["enabled"] is False
    assert (
        config["agents"]["defaults"]["model"]["primary"]
        == "amazon-bedrock/us.anthropic.claude-opus-4-7"
    )
    assert config["tools"]["alsoAllow"] == ["clawd-obsidian", "exec"]
    assert config["agents"]["list"][0]["tools"]["alsoAllow"] == [
        "clawd-obsidian",
        "exec",
    ]
    assert (
        config["plugins"]["entries"]["clawd-obsidian"]["config"]["pythonExec"]
        == python_exec
    )
    assert config["session"]["resetTriggers"] == ["/new", "/reset", "/hardstop"]
    assert config["tools"]["media"]["audio"]["echoTranscript"] is True
    assert config["tools"]["media"]["audio"]["echoFormat"] == "Heard:\n{transcript}"

    # Whisper only (no CLI fallback)
    models = config["tools"]["media"]["audio"]["models"]
    assert len(models) == 1
    assert models[0]["provider"] == "openai"
    assert models[0]["model"] == "gpt-4o-mini-transcribe"


def test_build_openclaw_config_whisper_only(tmp_path):
    config, _ = _build_config(tmp_path)

    # Only Whisper, no CLI fallback
    models = config["tools"]["media"]["audio"]["models"]
    assert len(models) == 1
    assert models[0]["provider"] == "openai"


def test_build_openclaw_config_has_tts(tmp_path):
    config, _ = _build_config(tmp_path)

    tts = config["messages"]["tts"]
    assert tts["auto"] == "inbound"
    assert tts["provider"] == "openai"
    assert tts["prefsPath"] == str(
        Path(config["agents"]["defaults"]["workspace"])
        / ".openclaw"
        / "settings"
        / "tts.json"
    )
    assert tts["maxTextLength"] == 1000000
    assert tts["timeoutMs"] == 120000
    assert tts["openai"]["model"] == "gpt-4o-mini-tts"
    assert tts["edge"]["enabled"] is True


def test_build_openclaw_config_openai_provider_is_conditional(tmp_path):
    config, _ = _build_config(tmp_path)
    assert "openai" not in config["models"]["providers"]

    config, _ = _build_config(tmp_path, {"OPENAI_API_KEY": "sk-test"})
    assert config["models"]["providers"]["openai"] == {
        "baseUrl": "https://api.openai.com/v1",
        "apiKey": "sk-test",
        "models": [],
    }


def test_build_openclaw_config_has_gmail_hooks(tmp_path):
    config, _ = _build_config(tmp_path)

    assert config["hooks"] == {
        "enabled": True,
        "token": "change-me",
        "path": "/hooks",
        "presets": ["gmail"],
    }

    config, _ = _build_config(tmp_path, {"OPENCLAW_HOOK_TOKEN": "  hook-secret  "})
    assert config["hooks"]["token"] == "hook-secret"
