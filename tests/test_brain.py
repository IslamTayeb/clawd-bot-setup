from datetime import datetime
from zoneinfo import ZoneInfo

from clawd_ops import brain
from clawd_ops import vault


def test_build_converse_request_uses_tool_config(git_vault, monkeypatch):
    monkeypatch.setattr(vault, "_local_now", lambda: datetime(2026, 3, 10, 12, tzinfo=ZoneInfo("America/New_York")))
    request = brain.build_converse_request([{"role": "user", "content": [{"text": "hi"}]}])
    assert request["toolConfig"]["tools"] == brain.tool_specs()
    assert request["modelId"] == brain.BEDROCK_MODEL_ID
    assert "Persistent memory file path: personal/clawd.md" in request["system"][0]["text"]
    assert "Today's task note path is tasks/031026.md." in request["system"][0]["text"]


def test_memory_write_requires_explicit_request(git_vault):
    try:
        brain._execute_tool(
            "remember_memory",
            {"memory": "Prefers concise replies"},
            "what do you remember about me?",
        )
    except ValueError as exc:
        assert "explicitly asks to remember" in str(exc)
    else:
        raise AssertionError("expected explicit memory guard to block the write")


def test_memory_forget_requires_explicit_request(git_vault):
    try:
        brain._execute_tool(
            "forget_memory",
            {"query": "concise replies"},
            "what do you remember about me?",
        )
    except ValueError as exc:
        assert "explicitly asks to forget" in str(exc)
    else:
        raise AssertionError("expected explicit forget guard to block the removal")
