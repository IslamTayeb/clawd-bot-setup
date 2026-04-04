from clawd_ops.openclaw_config import build_openclaw_config


def _build_config(tmp_path, extra_env=None):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    python_exec = workspace / ".venv" / "bin" / "python"
    python_exec.parent.mkdir(parents=True)
    python_exec.write_text("", encoding="utf-8")
    env = {
        "TELEGRAM_TOKEN": "123:abc",
        "ALLOWED_USER_ID": "8383879897",
        "AWS_REGION": "us-east-1",
    }
    if extra_env:
        env.update(extra_env)
    return build_openclaw_config(env, workspace=str(workspace), python_exec=str(python_exec)), str(python_exec)


def test_build_openclaw_config_preserves_model_id_and_bridges_python(tmp_path):
    config, python_exec = _build_config(tmp_path, {
        "BEDROCK_MODEL_ID": "us.anthropic.claude-opus-4-6-v1",
        "BOT_TIMEZONE": "America/New_York",
    })

    assert config["models"]["bedrockDiscovery"]["enabled"] is False
    assert config["agents"]["defaults"]["model"]["primary"] == "amazon-bedrock/us.anthropic.claude-opus-4-6-v1"
    assert config["tools"]["alsoAllow"] == ["clawd-obsidian", "exec"]
    assert config["agents"]["list"][0]["tools"]["alsoAllow"] == ["clawd-obsidian", "exec"]
    assert config["plugins"]["entries"]["clawd-obsidian"]["config"]["pythonExec"] == python_exec
    assert config["session"]["resetTriggers"] == ["/new", "/reset", "/hardstop"]
    assert config["tools"]["media"]["audio"]["echoTranscript"] is True
    assert config["tools"]["media"]["audio"]["echoFormat"] == "Heard:\n{transcript}"

    # Whisper is primary (index 0), CLI fallback is index 1
    models = config["tools"]["media"]["audio"]["models"]
    assert models[0]["provider"] == "openai"
    assert models[0]["model"] == "gpt-4o-mini-transcribe"
    assert models[1]["args"] == ["-m", "clawd_ops.openclaw_audio_cli", "{{MediaPath}}"]
    assert models[1]["timeoutSeconds"] == 1800


def test_build_openclaw_config_respects_transcribe_timeout_override(tmp_path):
    config, _ = _build_config(tmp_path, {"TRANSCRIBE_TIMEOUT_SECONDS": "2400"})

    # CLI fallback is index 1
    assert config["tools"]["media"]["audio"]["models"][1]["timeoutSeconds"] == 2400


def test_build_openclaw_config_has_tts(tmp_path):
    config, _ = _build_config(tmp_path)

    tts = config["messages"]["tts"]
    assert tts["auto"] == "tagged"
    assert tts["provider"] == "openai"
    assert tts["openai"]["model"] == "gpt-4o-mini-tts"
    assert tts["edge"]["enabled"] is True
