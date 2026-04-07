from dataclasses import asdict
from pathlib import Path

from clawd_ops import gmail_watcher


def _config(tmp_path: Path) -> gmail_watcher.GmailWatcherConfig:
    return gmail_watcher.GmailWatcherConfig(
        accounts=("test@gmail.com",),
        hook_url="http://127.0.0.1:18789/hooks/agent",
        hook_token="test-token",
        channel=None,
        to=None,
        poll_seconds=60,
        max_results=20,
        include_body=True,
        body_max_chars=2000,
        notify_mode="auto",
        always_notify_senders=(),
        never_notify_senders=(),
        tracked_item_limit=200,
        gog_bin="gog",
    )


def _message(**overrides) -> gmail_watcher.GmailMessage:
    defaults = dict(
        message_id="msg-1",
        sender_name="",
        sender_email="",
        subject="",
        snippet="",
        body="",
        received_at="",
        labels="",
        account="test@gmail.com",
    )
    defaults.update(overrides)
    return gmail_watcher.GmailMessage(**defaults)


def test_should_notify_suppresses_bulk_newsletters(tmp_path):
    config = _config(tmp_path)
    message = _message(
        subject="Summer internship funding applications are open",
        sender_name="Student Affairs Newsletter",
        sender_email="newsletter@example.com",
    )

    should_notify, reason = gmail_watcher._should_notify_message(config, message)

    assert should_notify is False
    assert reason == "bulk or newsletter email"


def test_should_notify_keeps_direct_human_mail(tmp_path):
    config = _config(tmp_path)
    message = _message(
        subject="Can we meet tomorrow about the lab deadline?",
        sender_name="Prof Example",
        sender_email="prof.example@duke.edu",
    )

    should_notify, reason = gmail_watcher._should_notify_message(config, message)

    assert should_notify is True
    assert reason == "direct human or non-bulk sender"


def test_should_notify_uses_memory_backed_topic_filters(monkeypatch, tmp_path):
    config = _config(tmp_path)
    monkeypatch.setattr(
        gmail_watcher,
        "list_email_filters",
        lambda sync=False: {
            "allow_sender": [],
            "suppress_sender": [],
            "allow_topic": [],
            "suppress_topic": ["duke daily"],
        },
    )
    message = _message(
        subject="Duke Daily newsletter: campus updates",
        sender_name="Duke Daily",
        sender_email="newsletter@duke.edu",
    )

    should_notify, reason = gmail_watcher._should_notify_message(config, message)

    assert should_notify is False
    assert reason == "topic blocklist"


def test_poll_account_bootstraps_without_emitting(monkeypatch, tmp_path):
    config = _config(tmp_path)
    messages = [
        _message(message_id="msg-2", subject="Older"),
        _message(message_id="msg-1", subject="Newest"),
    ]
    monkeypatch.setattr(
        gmail_watcher, "_fetch_recent_messages", lambda *a, **kw: messages
    )
    monkeypatch.setattr(
        gmail_watcher,
        "_state_path_for_account",
        lambda account: tmp_path / f"state-{account}.json",
    )

    result = gmail_watcher._poll_account(config, "test@gmail.com")

    assert result["bootstrap"] is True
    assert result["created"] == []
    state_path = tmp_path / "state-test@gmail.com.json"
    saved = gmail_watcher._read_json(state_path)
    assert saved["known_item_ids"] == ["msg-2", "msg-1"]


def test_poll_account_only_returns_unseen_messages(monkeypatch, tmp_path):
    config = _config(tmp_path)
    state_path = tmp_path / "state-test@gmail.com.json"
    gmail_watcher._write_json(state_path, {"known_item_ids": ["msg-1"]})

    messages = [
        _message(message_id="msg-3", subject="Brand new"),
        _message(message_id="msg-2", subject="Also new"),
        _message(message_id="msg-1", subject="Already seen"),
    ]
    monkeypatch.setattr(
        gmail_watcher, "_fetch_recent_messages", lambda *a, **kw: messages
    )
    monkeypatch.setattr(
        gmail_watcher,
        "_state_path_for_account",
        lambda account: state_path,
    )

    result = gmail_watcher._poll_account(config, "test@gmail.com")

    assert result["bootstrap"] is False
    created_ids = [m["message_id"] for m in result["created"]]
    # oldest-first order
    assert created_ids == ["msg-2", "msg-3"]


def test_build_hook_message_includes_account(tmp_path):
    config = _config(tmp_path)
    message = _message(
        subject="Test subject",
        sender_name="Alice",
        sender_email="alice@example.com",
        account="miivii69@gmail.com",
    )

    text = gmail_watcher._build_hook_message(config, message, reason="test reason")

    assert "miivii69@gmail.com" in text
    assert "Test subject" in text
    assert "Alice" in text
    assert "test reason" in text


def test_security_alert_always_notifies(tmp_path):
    config = _config(tmp_path)
    message = _message(
        subject="Security alert: new sign-in from Chrome",
        sender_name="Google",
        sender_email="no-reply@accounts.google.com",
    )

    should_notify, reason = gmail_watcher._should_notify_message(config, message)

    assert should_notify is True
    assert reason == "security-sensitive"
