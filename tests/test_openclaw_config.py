from clawd_ops.openclaw_config import build_openclaw_config


def test_build_openclaw_config_preserves_model_id_and_bridges_python(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    python_exec = workspace / ".venv" / "bin" / "python"
    python_exec.parent.mkdir(parents=True)
    python_exec.write_text("", encoding="utf-8")

    config = build_openclaw_config(
        {
            "TELEGRAM_TOKEN": "123:abc",
            "ALLOWED_USER_ID": "8383879897",
            "AWS_REGION": "us-east-1",
            "BEDROCK_MODEL_ID": "us.anthropic.claude-opus-4-6-v1",
            "BOT_TIMEZONE": "America/New_York",
        },
        workspace=str(workspace),
        python_exec=str(python_exec),
    )

    assert config["models"]["bedrockDiscovery"]["enabled"] is False
    assert config["agents"]["defaults"]["model"]["primary"] == "amazon-bedrock/us.anthropic.claude-opus-4-6-v1"
    assert config["tools"]["alsoAllow"] == ["clawd-obsidian"]
    assert config["agents"]["list"][0]["tools"]["alsoAllow"] == ["clawd-obsidian"]
    assert config["plugins"]["entries"]["clawd-obsidian"]["config"]["pythonExec"] == str(
        python_exec
    )
    assert config["session"]["resetTriggers"] == ["/new", "/reset", "/hardstop"]
    assert config["tools"]["media"]["audio"]["echoTranscript"] is True
    assert config["tools"]["media"]["audio"]["echoFormat"] == "Heard:\n{transcript}"
    assert config["tools"]["media"]["audio"]["models"][0]["args"] == [
        "-m",
        "clawd_ops.openclaw_audio_cli",
        "{{MediaPath}}",
    ]
