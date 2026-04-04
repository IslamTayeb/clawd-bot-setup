import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import requests

SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
EWS_MESSAGES_NS = "http://schemas.microsoft.com/exchange/services/2006/messages"
EWS_TYPES_NS = "http://schemas.microsoft.com/exchange/services/2006/types"
NS = {
    "soap": SOAP_NS,
    "m": EWS_MESSAGES_NS,
    "t": EWS_TYPES_NS,
}

DEFAULT_DUKE_CLIENT_ID = "20460e5d-ce91-49af-a3a5-70b6be7486d1"
DEFAULT_TENANT = "organizations"
DEFAULT_SCOPE = "offline_access https://outlook.office365.com/EWS.AccessAsUser.All"
DEFAULT_EWS_URL = "https://outlook.office365.com/EWS/Exchange.asmx"
DEFAULT_HOOK_URL = "http://127.0.0.1:18789/hooks/agent"


class ExchangeError(RuntimeError):
    pass


class InvalidSyncStateError(ExchangeError):
    pass


@dataclass
class ExchangeItemRef:
    item_id: str
    change_key: str


@dataclass
class ExchangeMessage:
    item_id: str
    change_key: str
    subject: str
    received_at: str
    sender_name: str
    sender_email: str
    is_read: bool | None
    body: str = ""

    def sender_display(self) -> str:
        if (
            self.sender_name
            and self.sender_email
            and self.sender_name != self.sender_email
        ):
            return f"{self.sender_name} <{self.sender_email}>"
        if self.sender_email:
            return self.sender_email
        if self.sender_name:
            return self.sender_name
        return "(unknown sender)"


@dataclass
class SyncResult:
    sync_state: str
    includes_last_item_in_range: bool
    created: list[ExchangeMessage]
    bootstrap: bool = False


@dataclass
class ExchangeConfig:
    email: str
    client_id: str
    tenant: str
    scope: str
    ews_url: str
    token_path: Path
    sync_state_path: Path
    hook_url: str
    hook_token: str
    channel: str | None
    to: str | None
    poll_seconds: int
    max_changes: int
    include_body: bool
    body_max_chars: int
    notify_mode: str
    always_notify_senders: tuple[str, ...]
    never_notify_senders: tuple[str, ...]
    tracked_item_limit: int


NEWSLETTER_HINTS = (
    "newsletter",
    "digest",
    "roundup",
    "daily",
    "weekly",
    "monthly",
    "bulletin",
    "community engagement",
    "eco-update",
    "announcement",
    "campus events",
    "student life",
)
AUTOMATED_SENDER_HINTS = (
    "no-reply",
    "noreply",
    "do-not-reply",
    "donotreply",
    "notifications",
    "notification",
    "digest",
    "newsletter",
    "listserv",
    "support",
    "unlock",
)
ROUTINE_HINTS = (
    "receipt",
    "confirmation",
    "welcome",
    "newsletter",
    "digest",
    "roundup",
    "announcement",
)
SECURITY_HINTS = (
    "security alert",
    "suspicious",
    "password reset",
    "sign-in",
    "sign in",
    "mfa",
    "multi-factor",
    "two-factor",
    "2fa",
    "account locked",
)
ACTION_HINTS = (
    "deadline",
    "due",
    "respond",
    "reply",
    "rsvp",
    "complete",
    "submit",
    "review",
    "register",
    "appointment",
    "meeting",
    "interview",
    "offer",
    "decision",
    "approval",
    "signature",
    "sign this",
)
OPPORTUNITY_HINTS = (
    "apply",
    "application",
    "applications open",
    "internship",
    "funding",
    "fellowship",
    "scholarship",
    "grant",
    "research",
    "opportunity",
    "career",
    "job",
)


def _state_root() -> Path:
    configured = os.environ.get("OPENCLAW_STATE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser() / "duke-exchange"
    return Path.home() / ".openclaw" / "duke-exchange"


def _default_token_path() -> Path:
    return _state_root() / "token.json"


def _default_sync_state_path() -> Path:
    return _state_root() / "sync-state.json"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _csv_values(raw: str) -> tuple[str, ...]:
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _ensure_parent(path)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _text(node: ET.Element | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _state_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"known_item_ids": []}
    payload = _read_json(path)
    known_item_ids = payload.get("known_item_ids", [])
    if isinstance(known_item_ids, list):
        payload["known_item_ids"] = [
            str(item_id) for item_id in known_item_ids if item_id
        ]
        return payload

    # Older sync-state files can be discarded safely because the user wants to start from now.
    return {"known_item_ids": []}


def _oauth_token_url(tenant: str) -> str:
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


def _oauth_device_url(tenant: str) -> str:
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode"


def _response_json(response: requests.Response) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError as exc:
        raise ExchangeError(
            f"Expected JSON from {response.request.method} {response.request.url}, got: {response.text[:200]}"
        ) from exc


def _save_tokens(
    path: Path,
    *,
    email: str,
    client_id: str,
    tenant: str,
    scope: str,
    tokens: dict[str, Any],
) -> dict[str, Any]:
    saved = {
        "email": email,
        "client_id": client_id,
        "tenant": tenant,
        "scope": scope,
        "access_token": tokens.get("access_token", ""),
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_at": int(time.time()) + int(tokens.get("expires_in", 3600)),
        "token_type": tokens.get("token_type", "Bearer"),
    }
    _write_json(path, saved)
    return saved


def device_authorize(config: ExchangeConfig) -> dict[str, Any]:
    response = requests.post(
        _oauth_device_url(config.tenant),
        data={"client_id": config.client_id, "scope": config.scope},
        timeout=20,
    )
    response.raise_for_status()
    payload = _response_json(response)
    message = payload.get("message", "")
    if message:
        print(message, file=sys.stderr, flush=True)

    interval = max(int(payload.get("interval", 5)), 1)
    deadline = time.time() + int(payload.get("expires_in", 900))
    token_url = _oauth_token_url(config.tenant)

    while time.time() < deadline:
        token_response = requests.post(
            token_url,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": config.client_id,
                "device_code": payload["device_code"],
            },
            timeout=20,
        )
        token_payload = _response_json(token_response)
        if token_response.status_code == 200:
            saved = _save_tokens(
                config.token_path,
                email=config.email,
                client_id=config.client_id,
                tenant=config.tenant,
                scope=config.scope,
                tokens=token_payload,
            )
            return {
                "email": config.email,
                "token_path": str(config.token_path),
                "expires_at": saved["expires_at"],
                "scope": saved["scope"],
            }

        error_code = token_payload.get("error", "")
        if error_code == "authorization_pending":
            time.sleep(interval)
            continue
        if error_code == "slow_down":
            interval += 5
            time.sleep(interval)
            continue
        raise ExchangeError(
            token_payload.get("error_description", error_code or token_response.text)
        )

    raise ExchangeError("Device authorization expired before sign-in completed.")


def _refresh_access_token(
    config: ExchangeConfig, cached: dict[str, Any]
) -> dict[str, Any]:
    refresh_token = cached.get("refresh_token", "").strip()
    if not refresh_token:
        raise ExchangeError(f"No refresh token available in {config.token_path}.")

    response = requests.post(
        _oauth_token_url(config.tenant),
        data={
            "grant_type": "refresh_token",
            "client_id": config.client_id,
            "refresh_token": refresh_token,
            "scope": config.scope,
        },
        timeout=20,
    )
    payload = _response_json(response)
    if response.status_code != 200:
        raise ExchangeError(payload.get("error_description", response.text))
    if not payload.get("refresh_token"):
        payload["refresh_token"] = refresh_token
    return _save_tokens(
        config.token_path,
        email=config.email,
        client_id=config.client_id,
        tenant=config.tenant,
        scope=config.scope,
        tokens=payload,
    )


def get_access_token(config: ExchangeConfig) -> str:
    if not config.token_path.exists():
        raise ExchangeError(
            f"Missing token cache at {config.token_path}. Run auth-device first."
        )

    cached = _read_json(config.token_path)
    if (
        cached.get("access_token")
        and int(cached.get("expires_at", 0)) > int(time.time()) + 60
    ):
        return str(cached["access_token"])

    refreshed = _refresh_access_token(config, cached)
    return str(refreshed["access_token"])


def _soap_envelope(inner_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<soap:Envelope xmlns:soap="{SOAP_NS}" xmlns:m="{EWS_MESSAGES_NS}" xmlns:t="{EWS_TYPES_NS}">'
        "<soap:Body>"
        f"{inner_xml}"
        "</soap:Body>"
        "</soap:Envelope>"
    )


def _post_ews(config: ExchangeConfig, access_token: str, inner_xml: str) -> ET.Element:
    response = requests.post(
        config.ews_url,
        data=_soap_envelope(inner_xml).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "text/xml; charset=utf-8",
            "Accept": "text/xml",
            "X-AnchorMailbox": config.email,
        },
        timeout=30,
    )
    response.raise_for_status()
    try:
        return ET.fromstring(response.text)
    except ET.ParseError as exc:
        raise ExchangeError(f"Invalid EWS XML response: {response.text[:400]}") from exc


def _parse_message_node(node: ET.Element, *, body_max_chars: int) -> ExchangeMessage:
    item_id_node = node.find("t:ItemId", NS)
    subject = _text(node.find("t:Subject", NS)) or "(no subject)"
    sender_mailbox = node.find("t:From/t:Mailbox", NS)
    sender_name = _text(
        sender_mailbox.find("t:Name", NS) if sender_mailbox is not None else None
    )
    sender_email = _text(
        sender_mailbox.find("t:EmailAddress", NS)
        if sender_mailbox is not None
        else None
    )
    is_read_raw = _text(node.find("t:IsRead", NS)).lower()
    body = _text(node.find("t:TextBody", NS))
    if body and body_max_chars > 0 and len(body) > body_max_chars:
        body = body[:body_max_chars].rstrip() + "..."
    return ExchangeMessage(
        item_id=item_id_node.attrib.get("Id", "") if item_id_node is not None else "",
        change_key=item_id_node.attrib.get("ChangeKey", "")
        if item_id_node is not None
        else "",
        subject=subject,
        received_at=_text(node.find("t:DateTimeReceived", NS)),
        sender_name=sender_name,
        sender_email=sender_email,
        is_read=(is_read_raw == "true") if is_read_raw else None,
        body=body,
    )


def _parse_sync_folder_items_response(
    root: ET.Element, *, body_max_chars: int
) -> SyncResult:
    response = root.find(".//m:SyncFolderItemsResponseMessage", NS)
    if response is None:
        raise ExchangeError(
            "EWS sync response did not include SyncFolderItemsResponseMessage."
        )
    code = _text(response.find("m:ResponseCode", NS))
    if code == "ErrorInvalidSyncStateData":
        raise InvalidSyncStateError(
            _text(response.find("m:MessageText", NS)) or "Invalid EWS sync state."
        )
    if code != "NoError":
        raise ExchangeError(_text(response.find("m:MessageText", NS)) or code)

    created = [
        _parse_message_node(node, body_max_chars=body_max_chars)
        for node in response.findall(".//m:Changes/t:Create/*", NS)
    ]
    return SyncResult(
        sync_state=_text(response.find("m:SyncState", NS)),
        includes_last_item_in_range=_text(
            response.find("m:IncludesLastItemInRange", NS)
        ).lower()
        == "true",
        created=created,
    )


def _parse_get_item_response(
    root: ET.Element, *, body_max_chars: int
) -> list[ExchangeMessage]:
    response_messages = root.findall(".//m:GetItemResponseMessage", NS)
    if not response_messages:
        raise ExchangeError(
            "EWS get-item response did not include GetItemResponseMessage."
        )

    items: list[ExchangeMessage] = []
    for message in response_messages:
        code = _text(message.find("m:ResponseCode", NS))
        if code != "NoError":
            raise ExchangeError(_text(message.find("m:MessageText", NS)) or code)
        for node in message.findall("m:Items/*", NS):
            items.append(_parse_message_node(node, body_max_chars=body_max_chars))
    return items


def _parse_find_item_refs_response(root: ET.Element) -> list[ExchangeItemRef]:
    response = root.find(".//m:FindItemResponseMessage", NS)
    if response is None:
        raise ExchangeError(
            "EWS find-item response did not include FindItemResponseMessage."
        )
    code = _text(response.find("m:ResponseCode", NS))
    if code != "NoError":
        raise ExchangeError(_text(response.find("m:MessageText", NS)) or code)

    refs: list[ExchangeItemRef] = []
    for node in response.findall(".//t:Items/*", NS):
        item_id_node = node.find("t:ItemId", NS)
        if item_id_node is None:
            continue
        refs.append(
            ExchangeItemRef(
                item_id=item_id_node.attrib.get("Id", ""),
                change_key=item_id_node.attrib.get("ChangeKey", ""),
            )
        )
    return refs


def _parse_find_item_response(
    root: ET.Element, *, body_max_chars: int
) -> list[ExchangeMessage]:
    response = root.find(".//m:FindItemResponseMessage", NS)
    if response is None:
        raise ExchangeError(
            "EWS probe response did not include FindItemResponseMessage."
        )
    code = _text(response.find("m:ResponseCode", NS))
    if code != "NoError":
        raise ExchangeError(_text(response.find("m:MessageText", NS)) or code)
    return [
        _parse_message_node(node, body_max_chars=body_max_chars)
        for node in response.findall(".//t:Items/*", NS)
    ]


def _build_sync_request(sync_state: str, *, max_changes: int) -> str:
    sync_state_xml = (
        f"<m:SyncState>{escape(sync_state)}</m:SyncState>" if sync_state else ""
    )
    return f"""
<m:SyncFolderItems>
  <m:ItemShape>
    <t:BaseShape>Default</t:BaseShape>
    <t:AdditionalProperties>
      <t:FieldURI FieldURI="item:Subject" />
      <t:FieldURI FieldURI="item:DateTimeReceived" />
      <t:FieldURI FieldURI="message:IsRead" />
      <t:FieldURI FieldURI="message:From" />
    </t:AdditionalProperties>
  </m:ItemShape>
  <m:SyncFolderId>
    <t:DistinguishedFolderId Id="inbox" />
  </m:SyncFolderId>
  {sync_state_xml}
  <m:MaxChangesReturned>{max_changes}</m:MaxChangesReturned>
</m:SyncFolderItems>
""".strip()


def _build_get_item_request(items: list[ExchangeMessage], *, include_body: bool) -> str:
    body_field = '<t:FieldURI FieldURI="item:TextBody" />' if include_body else ""
    item_ids = "".join(
        f'<t:ItemId Id="{escape(item.item_id)}" ChangeKey="{escape(item.change_key)}" />'
        for item in items
        if item.item_id
    )
    return f"""
<m:GetItem>
  <m:ItemShape>
    <t:BaseShape>IdOnly</t:BaseShape>
    <t:AdditionalProperties>
      <t:FieldURI FieldURI="item:Subject" />
      <t:FieldURI FieldURI="item:DateTimeReceived" />
      <t:FieldURI FieldURI="message:IsRead" />
      <t:FieldURI FieldURI="message:From" />
      {body_field}
    </t:AdditionalProperties>
  </m:ItemShape>
  <m:ItemIds>{item_ids}</m:ItemIds>
</m:GetItem>
""".strip()


def _build_find_item_request(limit: int) -> str:
    return f"""
<m:FindItem Traversal="Shallow">
  <m:ItemShape>
    <t:BaseShape>IdOnly</t:BaseShape>
  </m:ItemShape>
  <m:IndexedPageItemView MaxEntriesReturned="{limit}" Offset="0" BasePoint="Beginning" />
  <m:SortOrder>
    <t:FieldOrder Order="Descending">
      <t:FieldURI FieldURI="item:DateTimeReceived" />
    </t:FieldOrder>
  </m:SortOrder>
  <m:ParentFolderIds>
    <t:DistinguishedFolderId Id="inbox" />
  </m:ParentFolderIds>
</m:FindItem>
""".strip()


def _fetch_recent_messages(
    config: ExchangeConfig, *, limit: int
) -> list[ExchangeMessage]:
    access_token = get_access_token(config)
    root = _post_ews(config, access_token, _build_find_item_request(limit))
    refs = _parse_find_item_refs_response(root)
    if not refs:
        return []

    ref_messages = [
        ExchangeMessage(
            item_id=ref.item_id,
            change_key=ref.change_key,
            subject="",
            received_at="",
            sender_name="",
            sender_email="",
            is_read=None,
        )
        for ref in refs
    ]
    details_root = _post_ews(
        config,
        access_token,
        _build_get_item_request(ref_messages, include_body=config.include_body),
    )
    detailed_messages = _parse_get_item_response(
        details_root, body_max_chars=config.body_max_chars
    )
    by_id = {message.item_id: message for message in detailed_messages}
    return [by_id[ref.item_id] for ref in refs if ref.item_id in by_id]


def sync_once(config: ExchangeConfig) -> dict[str, Any]:
    access_token = get_access_token(config)
    previous_sync_state = ""
    if config.sync_state_path.exists():
        previous_sync_state = _read_json(config.sync_state_path).get("sync_state", "")

    sync_root = _post_ews(
        config,
        access_token,
        _build_sync_request(previous_sync_state, max_changes=config.max_changes),
    )
    sync_result = _parse_sync_folder_items_response(
        sync_root, body_max_chars=config.body_max_chars
    )
    _write_json(config.sync_state_path, {"sync_state": sync_result.sync_state})

    if not previous_sync_state:
        sync_result.bootstrap = True
        sync_result.created = []
        return {
            "bootstrap": True,
            "sync_state_path": str(config.sync_state_path),
            "created": [],
        }

    created = sync_result.created
    if created:
        details_root = _post_ews(
            config,
            access_token,
            _build_get_item_request(created, include_body=config.include_body),
        )
        created = _parse_get_item_response(
            details_root, body_max_chars=config.body_max_chars
        )

    return {
        "bootstrap": False,
        "sync_state_path": str(config.sync_state_path),
        "created": [asdict(message) for message in created],
    }


def probe_inbox(config: ExchangeConfig, *, limit: int) -> dict[str, Any]:
    items = _fetch_recent_messages(config, limit=limit)
    return {
        "email": config.email,
        "messages": [asdict(item) for item in items],
    }


def _sender_matches_any(message: ExchangeMessage, patterns: tuple[str, ...]) -> bool:
    sender_blob = f"{message.sender_name} {message.sender_email}".lower()
    return any(pattern in sender_blob for pattern in patterns)


def _subject_matches_any(message: ExchangeMessage, patterns: tuple[str, ...]) -> bool:
    subject = message.subject.lower()
    return any(pattern in subject for pattern in patterns)


def _body_matches_any(message: ExchangeMessage, patterns: tuple[str, ...]) -> bool:
    body = message.body.lower()
    return any(pattern in body for pattern in patterns)


def _is_direct_sender(message: ExchangeMessage) -> bool:
    sender = message.sender_email.lower()
    if not sender:
        return False
    return not _sender_matches_any(message, AUTOMATED_SENDER_HINTS)


def _should_notify_message(
    config: ExchangeConfig, message: ExchangeMessage
) -> tuple[bool, str]:
    if config.notify_mode == "off":
        return False, "notifications disabled"
    if config.notify_mode == "all":
        return True, "notify-all mode"

    if config.never_notify_senders and _sender_matches_any(
        message, config.never_notify_senders
    ):
        return False, "sender blocklist"
    if config.always_notify_senders and _sender_matches_any(
        message, config.always_notify_senders
    ):
        return True, "sender allowlist"

    has_security_signal = _subject_matches_any(
        message, SECURITY_HINTS
    ) or _body_matches_any(message, SECURITY_HINTS)
    action_score = sum(
        (
            _subject_matches_any(message, ACTION_HINTS),
            _body_matches_any(message, ACTION_HINTS),
        )
    )
    opportunity_score = sum(
        (
            _subject_matches_any(message, OPPORTUNITY_HINTS),
            _body_matches_any(message, OPPORTUNITY_HINTS),
        )
    )
    looks_bulk = _sender_matches_any(message, NEWSLETTER_HINTS) or _subject_matches_any(
        message, NEWSLETTER_HINTS
    )
    looks_routine = _subject_matches_any(message, ROUTINE_HINTS)
    direct_sender = _is_direct_sender(message)

    if has_security_signal:
        return True, "security-sensitive"
    if looks_bulk:
        return False, "bulk or newsletter email"
    if direct_sender and not looks_bulk and not looks_routine:
        return True, "direct human or non-bulk sender"
    if action_score >= 2 and not looks_bulk:
        return True, "deadline or action required"
    if _sender_matches_any(message, AUTOMATED_SENDER_HINTS) and not has_security_signal:
        return False, "automated notification"
    if looks_routine and opportunity_score == 0:
        return False, "routine update"
    return False, "low-signal email"


def _build_hook_message(
    config: ExchangeConfig, message: ExchangeMessage, *, reason: str
) -> str:
    lines = [
        "A new Duke email passed the local importance filter.",
        f"Why it passed: {reason}.",
        "Reply in at most 3 short lines:",
        "1. Why it matters",
        "2. Deadline or action item if any",
        '3. One suggested next step as a question, like "Want me to draft a reply?" or "Want me to add a todo?"',
        "If it looks like an application, internship, funding, or opportunity, you may ask whether to apply or review requirements.",
        "",
        f"Account: {config.email}",
        f"From: {message.sender_display()}",
        f"Subject: {message.subject}",
    ]
    if message.received_at:
        lines.append(f"Received: {message.received_at}")
    if config.include_body and message.body:
        lines.extend(["", "Body snippet:", message.body])
    return "\n".join(lines)


def _post_agent_hook(
    config: ExchangeConfig, message: ExchangeMessage, *, reason: str
) -> None:
    if not config.hook_token:
        raise ExchangeError(
            "OPENCLAW_HOOK_TOKEN (or --hook-token) is required for watch mode."
        )

    payload: dict[str, Any] = {
        "message": _build_hook_message(config, message, reason=reason),
        "name": "Duke Mail",
        "wakeMode": "now",
        "deliver": True,
    }
    if config.channel:
        payload["channel"] = config.channel
    if config.to:
        payload["to"] = config.to

    response = requests.post(
        config.hook_url,
        headers={
            "Authorization": f"Bearer {config.hook_token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=20,
    )
    response.raise_for_status()


def _poll_recent_messages(config: ExchangeConfig) -> dict[str, Any]:
    messages = _fetch_recent_messages(config, limit=config.max_changes)
    payload = _state_payload(config.sync_state_path)
    known_item_ids = payload.get("known_item_ids", [])
    if not known_item_ids:
        _write_json(
            config.sync_state_path,
            {
                "known_item_ids": [
                    message.item_id
                    for message in messages[: config.tracked_item_limit]
                    if message.item_id
                ]
            },
        )
        return {"bootstrap": True, "created": []}

    known_item_set = set(known_item_ids)
    new_messages: list[ExchangeMessage] = []
    for message in messages:
        if message.item_id in known_item_set:
            break
        new_messages.append(message)

    merged_known_ids = [message.item_id for message in messages if message.item_id]
    merged_known_ids.extend(
        item_id for item_id in known_item_ids if item_id not in set(merged_known_ids)
    )
    _write_json(
        config.sync_state_path,
        {"known_item_ids": merged_known_ids[: config.tracked_item_limit]},
    )
    return {
        "bootstrap": False,
        "created": [asdict(message) for message in reversed(new_messages)],
    }


def watch(config: ExchangeConfig, *, once: bool = False) -> dict[str, Any] | None:
    while True:
        try:
            cycle = _poll_recent_messages(config)
            created = [ExchangeMessage(**message) for message in cycle["created"]]
            delivered = 0
            suppressed = 0
            for message in created:
                should_notify, reason = _should_notify_message(config, message)
                if not should_notify:
                    suppressed += 1
                    continue
                _post_agent_hook(config, message, reason=reason)
                delivered += 1

            result = {
                "bootstrap": cycle["bootstrap"],
                "created": cycle["created"],
                "delivered": delivered,
                "suppressed": suppressed,
            }
            if once:
                return result

            if result["bootstrap"]:
                print(
                    "Bootstrapped Duke Exchange sync state without emitting existing messages.",
                    flush=True,
                )
            elif delivered:
                print(
                    f"Delivered {delivered} filtered Duke Exchange event(s) to OpenClaw hooks and suppressed {suppressed} routine email(s).",
                    flush=True,
                )
            elif suppressed:
                print(
                    f"Suppressed {suppressed} routine Duke email(s).",
                    flush=True,
                )
        except InvalidSyncStateError as exc:
            if config.sync_state_path.exists():
                config.sync_state_path.unlink()
            print(
                f"Resetting Duke Exchange sync state after EWS rejected it: {exc}",
                file=sys.stderr,
                flush=True,
            )
        except Exception as exc:
            if once:
                raise
            print(
                f"Duke Exchange watch cycle failed: {exc}", file=sys.stderr, flush=True
            )
        time.sleep(config.poll_seconds)


def _config_from_args(args: argparse.Namespace) -> ExchangeConfig:
    email = (args.email or os.environ.get("DUKE_EXCHANGE_EMAIL", "")).strip()
    if not email:
        raise ExchangeError("DUKE_EXCHANGE_EMAIL (or --email) is required.")

    token_path = Path(
        (args.token_path or os.environ.get("DUKE_EXCHANGE_TOKEN_PATH", "")).strip()
        or _default_token_path()
    ).expanduser()
    sync_state_path = Path(
        (
            args.sync_state_path or os.environ.get("DUKE_EXCHANGE_SYNC_STATE_PATH", "")
        ).strip()
        or _default_sync_state_path()
    ).expanduser()

    deliver_to = (
        args.to
        or os.environ.get("DUKE_EXCHANGE_DELIVER_TO", "")
        or os.environ.get("ALLOWED_USER_ID", "")
    ).strip()
    deliver_channel = (
        args.channel or os.environ.get("DUKE_EXCHANGE_DELIVER_CHANNEL", "")
    ).strip()
    if not deliver_channel and deliver_to:
        deliver_channel = "telegram"

    include_body = (
        args.include_body
        if args.include_body is not None
        else _env_flag("DUKE_EXCHANGE_INCLUDE_BODY", False)
    )
    notify_mode = (
        (
            args.notify_mode
            or os.environ.get("DUKE_EXCHANGE_NOTIFY_MODE", "")
            or "important"
        )
        .strip()
        .lower()
    )
    if notify_mode not in {"important", "all", "off"}:
        raise ExchangeError(
            "DUKE_EXCHANGE_NOTIFY_MODE must be one of: important, all, off."
        )

    return ExchangeConfig(
        email=email,
        client_id=(
            args.client_id
            or os.environ.get("DUKE_EXCHANGE_CLIENT_ID", "")
            or DEFAULT_DUKE_CLIENT_ID
        ).strip(),
        tenant=(
            args.tenant or os.environ.get("DUKE_EXCHANGE_TENANT", "") or DEFAULT_TENANT
        ).strip(),
        scope=(
            args.scope or os.environ.get("DUKE_EXCHANGE_SCOPE", "") or DEFAULT_SCOPE
        ).strip(),
        ews_url=(
            args.ews_url
            or os.environ.get("DUKE_EXCHANGE_EWS_URL", "")
            or DEFAULT_EWS_URL
        ).strip(),
        token_path=token_path,
        sync_state_path=sync_state_path,
        hook_url=(
            args.hook_url
            or os.environ.get("DUKE_EXCHANGE_HOOK_URL", "")
            or DEFAULT_HOOK_URL
        ).strip(),
        hook_token=(
            args.hook_token or os.environ.get("OPENCLAW_HOOK_TOKEN", "")
        ).strip(),
        channel=deliver_channel or None,
        to=deliver_to or None,
        poll_seconds=int(
            (
                str(args.poll_seconds)
                if args.poll_seconds is not None
                else os.environ.get("DUKE_EXCHANGE_POLL_SECONDS", "")
            ).strip()
            or "60"
        ),
        max_changes=int(
            (
                str(args.max_changes)
                if args.max_changes is not None
                else os.environ.get("DUKE_EXCHANGE_MAX_CHANGES", "")
            ).strip()
            or "25"
        ),
        include_body=include_body,
        body_max_chars=int(
            (
                str(args.body_max_chars)
                if args.body_max_chars is not None
                else os.environ.get("DUKE_EXCHANGE_BODY_MAX_CHARS", "")
            ).strip()
            or "1200"
        ),
        notify_mode=notify_mode,
        always_notify_senders=_csv_values(
            args.always_notify_senders
            or os.environ.get("DUKE_EXCHANGE_ALWAYS_NOTIFY_SENDERS", "")
        ),
        never_notify_senders=_csv_values(
            args.never_notify_senders
            or os.environ.get("DUKE_EXCHANGE_NEVER_NOTIFY_SENDERS", "")
        ),
        tracked_item_limit=int(
            (
                str(args.tracked_item_limit)
                if args.tracked_item_limit is not None
                else os.environ.get("DUKE_EXCHANGE_TRACKED_ITEM_LIMIT", "")
            ).strip()
            or "200"
        ),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m clawd_ops.exchange")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--email")
    common.add_argument("--client-id")
    common.add_argument("--tenant")
    common.add_argument("--scope")
    common.add_argument("--ews-url")
    common.add_argument("--token-path")
    common.add_argument("--sync-state-path")
    common.add_argument("--hook-url")
    common.add_argument("--hook-token")
    common.add_argument("--channel")
    common.add_argument("--to")
    common.add_argument("--poll-seconds", type=int)
    common.add_argument("--max-changes", type=int)
    common.add_argument("--body-max-chars", type=int)
    common.add_argument("--notify-mode")
    common.add_argument("--always-notify-senders")
    common.add_argument("--never-notify-senders")
    common.add_argument("--tracked-item-limit", type=int)
    common.add_argument("--include-body", dest="include_body", action="store_true")
    common.add_argument("--no-include-body", dest="include_body", action="store_false")
    common.set_defaults(include_body=None)

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("auth-device", parents=[common])
    probe = subparsers.add_parser("probe", parents=[common])
    probe.add_argument("--limit", type=int, default=5)
    watch_parser = subparsers.add_parser("watch", parents=[common])
    watch_parser.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        config = _config_from_args(args)
        if args.command == "auth-device":
            print(json.dumps(device_authorize(config), indent=2, sort_keys=True))
        elif args.command == "probe":
            print(
                json.dumps(
                    probe_inbox(config, limit=args.limit), indent=2, sort_keys=True
                )
            )
        else:
            result = watch(config, once=args.once)
            if result is not None:
                print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"{exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
