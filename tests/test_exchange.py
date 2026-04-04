from pathlib import Path

from clawd_ops import exchange


SYNC_RESPONSE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
               xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
  <soap:Body>
    <m:SyncFolderItemsResponse>
      <m:ResponseMessages>
        <m:SyncFolderItemsResponseMessage ResponseClass="Success">
          <m:ResponseCode>NoError</m:ResponseCode>
          <m:SyncState>state-123</m:SyncState>
          <m:IncludesLastItemInRange>true</m:IncludesLastItemInRange>
          <m:Changes>
            <t:Create>
              <t:Message>
                <t:ItemId Id="item-1" ChangeKey="ck-1" />
                <t:Subject>Hello</t:Subject>
                <t:DateTimeReceived>2026-04-04T10:00:00Z</t:DateTimeReceived>
                <t:IsRead>false</t:IsRead>
                <t:From>
                  <t:Mailbox>
                    <t:Name>Alice</t:Name>
                    <t:EmailAddress>alice@example.com</t:EmailAddress>
                  </t:Mailbox>
                </t:From>
              </t:Message>
            </t:Create>
          </m:Changes>
        </m:SyncFolderItemsResponseMessage>
      </m:ResponseMessages>
    </m:SyncFolderItemsResponse>
  </soap:Body>
</soap:Envelope>
"""


GET_ITEM_RESPONSE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages"
               xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types">
  <soap:Body>
    <m:GetItemResponse>
      <m:ResponseMessages>
        <m:GetItemResponseMessage ResponseClass="Success">
          <m:ResponseCode>NoError</m:ResponseCode>
          <m:Items>
            <t:Message>
              <t:ItemId Id="item-1" ChangeKey="ck-1" />
              <t:Subject>Hello</t:Subject>
              <t:DateTimeReceived>2026-04-04T10:00:00Z</t:DateTimeReceived>
              <t:IsRead>false</t:IsRead>
              <t:From>
                <t:Mailbox>
                  <t:Name>Alice</t:Name>
                  <t:EmailAddress>alice@example.com</t:EmailAddress>
                </t:Mailbox>
              </t:From>
              <t:TextBody>Line one\nLine two</t:TextBody>
            </t:Message>
          </m:Items>
        </m:GetItemResponseMessage>
      </m:ResponseMessages>
    </m:GetItemResponse>
  </soap:Body>
</soap:Envelope>
"""


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)
        self.request = type(
            "Req", (), {"method": "POST", "url": "https://example.com"}
        )()

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


def _config(tmp_path):
    return exchange.ExchangeConfig(
        email="netid@duke.edu",
        client_id="client-id",
        tenant="organizations",
        scope="offline_access https://outlook.office365.com/EWS.AccessAsUser.All",
        ews_url="https://outlook.office365.com/EWS/Exchange.asmx",
        token_path=tmp_path / "token.json",
        sync_state_path=tmp_path / "sync.json",
        hook_url="http://127.0.0.1:18789/hooks/agent",
        hook_token="secret",
        channel="telegram",
        to="1234",
        poll_seconds=60,
        max_changes=25,
        include_body=False,
        body_max_chars=1200,
        notify_mode="important",
        always_notify_senders=(),
        never_notify_senders=(),
        tracked_item_limit=200,
    )


def test_parse_sync_folder_items_response_extracts_created_messages():
    root = exchange.ET.fromstring(SYNC_RESPONSE)

    result = exchange._parse_sync_folder_items_response(root, body_max_chars=200)

    assert result.sync_state == "state-123"
    assert result.includes_last_item_in_range is True
    assert len(result.created) == 1
    assert result.created[0].subject == "Hello"
    assert result.created[0].sender_email == "alice@example.com"


def test_parse_get_item_response_extracts_body_and_sender():
    root = exchange.ET.fromstring(GET_ITEM_RESPONSE)

    items = exchange._parse_get_item_response(root, body_max_chars=200)

    assert len(items) == 1
    assert items[0].body == "Line one\nLine two"
    assert items[0].sender_name == "Alice"


def test_build_hook_message_respects_include_body(tmp_path):
    config = _config(tmp_path)
    message = exchange.ExchangeMessage(
        item_id="item-1",
        change_key="ck-1",
        subject="Hello",
        received_at="2026-04-04T10:00:00Z",
        sender_name="Alice",
        sender_email="alice@example.com",
        is_read=False,
        body="Sensitive body",
    )

    text = exchange._build_hook_message(config, message, reason="direct sender")
    assert "Body snippet" not in text

    config.include_body = True
    text = exchange._build_hook_message(config, message, reason="direct sender")
    assert "Body snippet" in text
    assert "Sensitive body" in text


def test_device_authorize_polls_until_success(monkeypatch, tmp_path):
    responses = iter(
        [
            FakeResponse(
                200,
                {
                    "message": "Visit https://microsoft.com/devicelogin and enter code ABCD.",
                    "device_code": "device-code",
                    "interval": 0,
                    "expires_in": 600,
                },
            ),
            FakeResponse(400, {"error": "authorization_pending"}),
            FakeResponse(
                200,
                {
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            ),
        ]
    )

    monkeypatch.setattr(
        exchange.requests, "post", lambda *args, **kwargs: next(responses)
    )
    monkeypatch.setattr(exchange.time, "sleep", lambda _: None)

    config = _config(tmp_path)

    result = exchange.device_authorize(config)

    assert result["email"] == "netid@duke.edu"
    saved = exchange._read_json(Path(result["token_path"]))
    assert saved["access_token"] == "access-token"
    assert saved["refresh_token"] == "refresh-token"


def test_should_notify_message_suppresses_bulk_opportunities_by_default(tmp_path):
    config = _config(tmp_path)
    message = exchange.ExchangeMessage(
        item_id="item-1",
        change_key="ck-1",
        subject="Summer internship funding applications are open at Duke",
        received_at="2026-04-04T10:00:00Z",
        sender_name="Student Affairs Newsletter",
        sender_email="newsletter@duke.edu",
        is_read=False,
    )

    should_notify, reason = exchange._should_notify_message(config, message)

    assert should_notify is False
    assert reason == "bulk or newsletter email"


def test_should_notify_message_keeps_direct_human_mail(tmp_path):
    config = _config(tmp_path)
    message = exchange.ExchangeMessage(
        item_id="item-1",
        change_key="ck-1",
        subject="Can we meet tomorrow about the lab deadline?",
        received_at="2026-04-04T10:00:00Z",
        sender_name="Prof Example",
        sender_email="prof.example@duke.edu",
        is_read=False,
    )

    should_notify, reason = exchange._should_notify_message(config, message)

    assert should_notify is True
    assert reason == "direct human or non-bulk sender"


def test_poll_recent_messages_bootstraps_without_emitting_old_mail(
    monkeypatch, tmp_path
):
    config = _config(tmp_path)
    messages = [
        exchange.ExchangeMessage(
            item_id="item-2",
            change_key="ck-2",
            subject="Older",
            received_at="",
            sender_name="Alice",
            sender_email="alice@example.com",
            is_read=False,
        ),
        exchange.ExchangeMessage(
            item_id="item-1",
            change_key="ck-1",
            subject="Newest",
            received_at="",
            sender_name="Bob",
            sender_email="bob@example.com",
            is_read=False,
        ),
    ]
    monkeypatch.setattr(
        exchange, "_fetch_recent_messages", lambda *args, **kwargs: messages
    )

    result = exchange._poll_recent_messages(config)

    assert result == {"bootstrap": True, "created": []}
    saved = exchange._read_json(config.sync_state_path)
    assert saved["known_item_ids"] == ["item-2", "item-1"]


def test_poll_recent_messages_only_returns_unseen_messages(monkeypatch, tmp_path):
    config = _config(tmp_path)
    exchange._write_json(
        config.sync_state_path, {"known_item_ids": ["item-2", "item-1"]}
    )
    messages = [
        exchange.ExchangeMessage(
            item_id="item-4",
            change_key="ck-4",
            subject="Newest",
            received_at="",
            sender_name="Bob",
            sender_email="bob@example.com",
            is_read=False,
        ),
        exchange.ExchangeMessage(
            item_id="item-3",
            change_key="ck-3",
            subject="Next",
            received_at="",
            sender_name="Bob",
            sender_email="bob@example.com",
            is_read=False,
        ),
        exchange.ExchangeMessage(
            item_id="item-2",
            change_key="ck-2",
            subject="Known",
            received_at="",
            sender_name="Bob",
            sender_email="bob@example.com",
            is_read=False,
        ),
    ]
    monkeypatch.setattr(
        exchange, "_fetch_recent_messages", lambda *args, **kwargs: messages
    )

    result = exchange._poll_recent_messages(config)

    assert result["bootstrap"] is False
    assert [message["item_id"] for message in result["created"]] == ["item-3", "item-4"]
