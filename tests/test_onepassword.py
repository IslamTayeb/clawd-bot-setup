import subprocess

from clawd_ops import onepassword


def test_list_1password_accounts_parses_json(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, stdout='[{"url":"my.1password.com"}]', stderr=""
        )

    monkeypatch.setattr(onepassword.subprocess, "run", fake_run)

    result = onepassword.list_1password_accounts()

    assert result == [{"url": "my.1password.com"}]


def test_read_1password_secret_returns_plaintext(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, stdout="secret-value\n", stderr=""
        )

    monkeypatch.setattr(onepassword.subprocess, "run", fake_run)

    result = onepassword.read_1password_secret("op://Private/example/password")

    assert result == "secret-value"


def test_whoami_1password_returns_signed_out_state(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="account is not signed in"
        )

    monkeypatch.setattr(onepassword.subprocess, "run", fake_run)

    result = onepassword.whoami_1password()

    assert result == {"signed_in": False, "error": "account is not signed in"}


def test_list_1password_vaults_returns_empty_when_no_accounts(monkeypatch):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="No accounts configured for use with 1Password CLI.",
        )

    monkeypatch.setattr(onepassword.subprocess, "run", fake_run)

    result = onepassword.list_1password_vaults()

    assert result == []


def test_get_1password_item_requires_item_name():
    try:
        onepassword.get_1password_item("")
    except ValueError as exc:
        assert "item must not be empty" in str(exc)
    else:
        raise AssertionError("expected item validation to fail")
