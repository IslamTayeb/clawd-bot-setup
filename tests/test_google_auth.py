import subprocess

from clawd_ops import google_auth


def test_start_google_auth_extracts_remote_url(monkeypatch):
    def fake_run(command, **kwargs):
        assert command[:4] == [google_auth.GOG_BIN, "auth", "add", "user@gmail.com"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Open this URL: https://example.com/auth/start\n",
            stderr="",
        )

    monkeypatch.setattr(google_auth.subprocess, "run", fake_run)

    result = google_auth.start_google_auth("user@gmail.com")

    assert result["url"] == "https://example.com/auth/start"
    assert result["email"] == "user@gmail.com"


def test_finish_google_auth_runs_remote_step_two(monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(google_auth.subprocess, "run", fake_run)

    result = google_auth.finish_google_auth(
        "user@gmail.com",
        "https://example.com/oauth2/callback?code=abc",
    )

    assert "--step=2" in seen["command"]
    assert result == "ok"


def test_list_google_auth_accounts_parses_json(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, stdout='[{"email":"user@gmail.com"}]', stderr=""
        )

    monkeypatch.setattr(google_auth.subprocess, "run", fake_run)

    result = google_auth.list_google_auth_accounts()

    assert result == [{"email": "user@gmail.com"}]
