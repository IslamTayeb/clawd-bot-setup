import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

from clawd_ops.vault import list_email_filters

# ---------------------------------------------------------------------------
# Signal tuples -- shared with exchange.py (same heuristics)
# ---------------------------------------------------------------------------

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
VERIFICATION_HINTS = (
    "verification code",
    "verify your",
    "confirm your email",
    "one-time code",
    "one-time password",
    "otp",
    "login code",
    "sign-in code",
    "auth code",
    "confirmation code",
)
MAILING_LIST_HINTS = (
    "list-unsubscribe",
    "mailing list",
    "listserv",
    "you are receiving this",
    "unsubscribe",
    "manage your subscription",
    "email preferences",
    "opt out",
)
CALENDAR_HINTS = (
    "invitation",
    "invite",
    "calendar event",
    "meeting request",
    "accepted:",
    "declined:",
    "tentative:",
    "updated invitation",
    "canceled event",
    "cancelled event",
    "event notification",
    "new event",
    "has invited you",
    "rsvp",
    "join with google meet",
    "join zoom meeting",
    "teams meeting",
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


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class GmailMessage:
    message_id: str
    sender_name: str
    sender_email: str
    subject: str
    snippet: str
    body: str
    received_at: str
    labels: str
    account: str

    def sender_display(self) -> str:
        if self.sender_name:
            return f"{self.sender_name} <{self.sender_email}>"
        return self.sender_email


@dataclass
class GmailWatcherConfig:
    accounts: tuple[str, ...]
    hook_url: str
    hook_token: str
    channel: str | None
    to: str | None
    poll_seconds: int
    max_results: int
    include_body: bool
    body_max_chars: int
    notify_mode: str
    always_notify_senders: tuple[str, ...]
    never_notify_senders: tuple[str, ...]
    tracked_item_limit: int
    gog_bin: str


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


def _state_root() -> Path:
    configured = os.environ.get("OPENCLAW_STATE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser() / "gmail-watcher"
    return Path.home() / ".openclaw" / "gmail-watcher"


def _state_path_for_account(account: str) -> Path:
    safe_name = account.replace("@", "_at_").replace(".", "_")
    return _state_root() / f"sync-state-{safe_name}.json"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


# ---------------------------------------------------------------------------
# gog gmail interaction
# ---------------------------------------------------------------------------


def _run_gog(
    config: GmailWatcherConfig, *args: str, timeout: int = 30
) -> subprocess.CompletedProcess[str]:
    command = [config.gog_bin, *args]
    env = {**os.environ, "GOG_NO_INPUT": "1"}
    return subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, env=env
    )


def _fetch_recent_messages(
    config: GmailWatcherConfig, account: str
) -> list[GmailMessage]:
    cmd: list[str] = [
        "gmail",
        "messages",
        "search",
        "newer_than:1d",
        "--account",
        account,
        "--max",
        str(config.max_results),
        "--json",
        "--results-only",
    ]
    if config.include_body:
        cmd.append("--include-body")
    result = _run_gog(config, *cmd)
    if result.returncode != 0:
        raise RuntimeError(
            f"gog gmail messages search failed for {account}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    raw = result.stdout.strip()
    if not raw:
        return []
    items = json.loads(raw)
    if not isinstance(items, list):
        return []

    messages: list[GmailMessage] = []
    for item in items:
        from_raw = item.get("from", "")
        sender_name = ""
        sender_email = from_raw
        if "<" in from_raw and ">" in from_raw:
            sender_name = from_raw[: from_raw.index("<")].strip().strip('"')
            sender_email = from_raw[from_raw.index("<") + 1 : from_raw.index(">")]

        snippet = item.get("snippet", "")
        body = item.get("body", "") if config.include_body else ""

        messages.append(
            GmailMessage(
                message_id=item.get("id", ""),
                sender_name=sender_name,
                sender_email=sender_email,
                subject=item.get("subject", ""),
                snippet=snippet,
                body=body[: config.body_max_chars] if body else snippet,
                received_at=item.get("date", ""),
                labels=item.get("labels", ""),
                account=account,
            )
        )
    return messages


# ---------------------------------------------------------------------------
# Filtering -- identical cascade to exchange.py
# ---------------------------------------------------------------------------


def _sender_matches_any(message: GmailMessage, patterns: tuple[str, ...]) -> bool:
    sender_blob = f"{message.sender_name} {message.sender_email}".lower()
    return any(pattern in sender_blob for pattern in patterns)


def _subject_matches_any(message: GmailMessage, patterns: tuple[str, ...]) -> bool:
    return any(pattern in message.subject.lower() for pattern in patterns)


def _body_matches_any(message: GmailMessage, patterns: tuple[str, ...]) -> bool:
    return any(pattern in message.body.lower() for pattern in patterns)


def _topic_matches_any(message: GmailMessage, patterns: tuple[str, ...]) -> bool:
    if _subject_matches_any(message, patterns):
        return True
    return _body_matches_any(message, patterns)


def _effective_filters(
    config: GmailWatcherConfig,
) -> dict[str, tuple[str, ...]]:
    memory_rules = list_email_filters(sync=False)

    def dedupe(*values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        ordered: list[str] = []
        seen: set[str] = set()
        for group in values:
            for value in group:
                if value not in seen:
                    ordered.append(value)
                    seen.add(value)
        return tuple(ordered)

    return {
        "allow_sender": dedupe(
            config.always_notify_senders, tuple(memory_rules.get("allow_sender", []))
        ),
        "suppress_sender": dedupe(
            config.never_notify_senders,
            tuple(memory_rules.get("suppress_sender", [])),
        ),
        "allow_topic": dedupe(tuple(memory_rules.get("allow_topic", []))),
        "suppress_topic": dedupe(tuple(memory_rules.get("suppress_topic", []))),
    }


def _is_direct_sender(message: GmailMessage) -> bool:
    sender = message.sender_email.lower()
    if not sender:
        return False
    return not _sender_matches_any(message, AUTOMATED_SENDER_HINTS)


def _should_notify_message(
    config: GmailWatcherConfig,
    message: GmailMessage,
    *,
    filters: dict[str, tuple[str, ...]] | None = None,
) -> tuple[bool, str]:
    filters = filters or _effective_filters(config)
    if config.notify_mode == "off":
        return False, "notifications disabled"
    if config.notify_mode == "all":
        return True, "notify-all mode"

    if filters["suppress_sender"] and _sender_matches_any(
        message, filters["suppress_sender"]
    ):
        return False, "sender blocklist"
    if filters["allow_sender"] and _sender_matches_any(
        message, filters["allow_sender"]
    ):
        return True, "sender allowlist"
    if filters["suppress_topic"] and _topic_matches_any(
        message, filters["suppress_topic"]
    ):
        return False, "topic blocklist"
    if filters["allow_topic"] and _topic_matches_any(message, filters["allow_topic"]):
        return True, "topic allowlist"

    has_security_signal = _subject_matches_any(
        message, SECURITY_HINTS
    ) or _body_matches_any(message, SECURITY_HINTS)
    has_verification_signal = _subject_matches_any(
        message, VERIFICATION_HINTS
    ) or _body_matches_any(message, VERIFICATION_HINTS)
    has_calendar_signal = _subject_matches_any(
        message, CALENDAR_HINTS
    ) or _body_matches_any(message, CALENDAR_HINTS)
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
    looks_mailing_list = _body_matches_any(message, MAILING_LIST_HINTS)
    looks_routine = _subject_matches_any(message, ROUTINE_HINTS)
    direct_sender = _is_direct_sender(message)

    if has_security_signal:
        return False, "security or sign-in alert"
    if has_verification_signal:
        return False, "verification or one-time code"
    if has_calendar_signal:
        return False, "calendar or meeting invite (already on calendar)"
    if looks_bulk:
        return False, "bulk or newsletter email"
    if looks_mailing_list:
        return False, "mailing list"
    if direct_sender and not looks_bulk and not looks_routine:
        return True, "direct human or non-bulk sender"
    if action_score >= 2 and not looks_bulk:
        return True, "deadline or action required"
    if _sender_matches_any(message, AUTOMATED_SENDER_HINTS):
        return False, "automated notification"
    if looks_routine and opportunity_score == 0:
        return False, "routine update"
    return False, "low-signal email"


# ---------------------------------------------------------------------------
# Hook posting
# ---------------------------------------------------------------------------


def _build_hook_message(
    config: GmailWatcherConfig, message: GmailMessage, *, reason: str
) -> str:
    lines = [
        "--- EMAIL CONTEXT (preserve for followup) ---",
        f"Account: {message.account}",
        f"Message-ID: {message.message_id}",
        f"From: {message.sender_display()}",
        f"Subject: {message.subject}",
    ]
    if message.received_at:
        lines.append(f"Received: {message.received_at}")
    if config.include_body and message.body:
        lines.extend(["", "Body:", message.body])
    lines.extend(
        [
            "--- END EMAIL CONTEXT ---",
            "",
            "Summarize this email in one sentence, then ask: want me to add anything to your todos?",
        ]
    )
    return "\n".join(lines)


def _post_agent_hook(
    config: GmailWatcherConfig, message: GmailMessage, *, reason: str
) -> None:
    if not config.hook_token:
        raise RuntimeError(
            "OPENCLAW_HOOK_TOKEN (or --hook-token) is required for watch mode."
        )

    payload: dict[str, Any] = {
        "message": _build_hook_message(config, message, reason=reason),
        "name": "Gmail",
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


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------


def _poll_account(config: GmailWatcherConfig, account: str) -> dict[str, Any]:
    messages = _fetch_recent_messages(config, account)
    state_path = _state_path_for_account(account)
    payload = _read_json(state_path)
    known_ids = payload.get("known_item_ids", [])

    if not known_ids:
        _write_json(
            state_path,
            {
                "known_item_ids": [
                    m.message_id
                    for m in messages[: config.tracked_item_limit]
                    if m.message_id
                ]
            },
        )
        return {"account": account, "bootstrap": True, "created": []}

    known_set = set(known_ids)
    new_messages: list[GmailMessage] = []
    for message in messages:
        if message.message_id in known_set:
            break
        new_messages.append(message)

    merged_ids = [m.message_id for m in messages if m.message_id]
    merged_ids.extend(mid for mid in known_ids if mid not in set(merged_ids))
    _write_json(
        state_path,
        {"known_item_ids": merged_ids[: config.tracked_item_limit]},
    )
    return {
        "account": account,
        "bootstrap": False,
        "created": [asdict(m) for m in reversed(new_messages)],
    }


def watch(config: GmailWatcherConfig, *, once: bool = False) -> dict[str, Any] | None:
    while True:
        total_delivered = 0
        total_suppressed = 0
        all_results: list[dict[str, Any]] = []

        try:
            filters = _effective_filters(config)
            for account in config.accounts:
                try:
                    cycle = _poll_account(config, account)
                except Exception as exc:
                    print(
                        f"Gmail watch cycle failed for {account}: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue

                created = [GmailMessage(**m) for m in cycle["created"]]
                delivered = 0
                suppressed = 0
                for message in created:
                    should_notify, reason = _should_notify_message(
                        config, message, filters=filters
                    )
                    if not should_notify:
                        suppressed += 1
                        continue
                    _post_agent_hook(config, message, reason=reason)
                    delivered += 1

                total_delivered += delivered
                total_suppressed += suppressed
                all_results.append(
                    {
                        "account": account,
                        "bootstrap": cycle["bootstrap"],
                        "created": cycle["created"],
                        "delivered": delivered,
                        "suppressed": suppressed,
                    }
                )

            result = {
                "accounts": all_results,
                "delivered": total_delivered,
                "suppressed": total_suppressed,
            }

            if once:
                return result

            bootstrapped = [r["account"] for r in all_results if r["bootstrap"]]
            if bootstrapped:
                print(
                    f"Bootstrapped Gmail sync state for {', '.join(bootstrapped)} without emitting existing messages.",
                    flush=True,
                )
            if total_delivered:
                print(
                    f"Delivered {total_delivered} filtered Gmail event(s) to OpenClaw hooks and suppressed {total_suppressed} routine email(s).",
                    flush=True,
                )
            elif total_suppressed:
                print(
                    f"Suppressed {total_suppressed} routine Gmail email(s).",
                    flush=True,
                )

        except Exception as exc:
            if once:
                raise
            print(f"Gmail watch cycle failed: {exc}", file=sys.stderr, flush=True)

        time.sleep(config.poll_seconds)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _config_from_args(args: argparse.Namespace) -> GmailWatcherConfig:
    accounts_raw = (
        args.accounts or os.environ.get("GMAIL_WATCHER_ACCOUNTS", "")
    ).strip()
    if not accounts_raw:
        raise RuntimeError(
            "GMAIL_WATCHER_ACCOUNTS (or --accounts) is required. "
            "Comma-separated list of Gmail addresses."
        )
    accounts = tuple(a.strip() for a in accounts_raw.split(",") if a.strip())

    hook_url = (
        args.hook_url or os.environ.get("OPENCLAW_HOOK_URL", "")
    ).strip() or "http://127.0.0.1:18789/hooks/agent"
    hook_token = (args.hook_token or os.environ.get("OPENCLAW_HOOK_TOKEN", "")).strip()
    channel = (
        args.channel or os.environ.get("GMAIL_WATCHER_CHANNEL", "")
    ).strip() or None
    to = (args.to or os.environ.get("GMAIL_WATCHER_TO", "")).strip() or None
    poll_seconds = int(
        args.poll_seconds or os.environ.get("GMAIL_WATCHER_POLL_SECONDS", "") or "60"
    )
    max_results = int(
        args.max_results or os.environ.get("GMAIL_WATCHER_MAX_RESULTS", "") or "20"
    )
    include_body = (
        args.include_body
        if args.include_body is not None
        else (
            os.environ.get("GMAIL_WATCHER_INCLUDE_BODY", "").lower()
            in ("1", "true", "yes")
        )
    )
    body_max_chars = int(
        args.body_max_chars
        or os.environ.get("GMAIL_WATCHER_BODY_MAX_CHARS", "")
        or "2000"
    )
    notify_mode = (
        args.notify_mode or os.environ.get("GMAIL_WATCHER_NOTIFY_MODE", "")
    ).strip() or "auto"
    always_raw = (
        args.always_notify_senders or os.environ.get("GMAIL_WATCHER_ALWAYS_NOTIFY", "")
    ).strip()
    always_notify = (
        tuple(s.strip().lower() for s in always_raw.split(",") if s.strip())
        if always_raw
        else ()
    )
    never_raw = (
        args.never_notify_senders or os.environ.get("GMAIL_WATCHER_NEVER_NOTIFY", "")
    ).strip()
    never_notify = (
        tuple(s.strip().lower() for s in never_raw.split(",") if s.strip())
        if never_raw
        else ()
    )
    tracked_item_limit = int(
        args.tracked_item_limit
        or os.environ.get("GMAIL_WATCHER_TRACKED_LIMIT", "")
        or "200"
    )
    gog_bin = os.environ.get("GOG_BIN", "gog")

    return GmailWatcherConfig(
        accounts=accounts,
        hook_url=hook_url,
        hook_token=hook_token,
        channel=channel,
        to=to,
        poll_seconds=poll_seconds,
        max_results=max_results,
        include_body=include_body,
        body_max_chars=body_max_chars,
        notify_mode=notify_mode,
        always_notify_senders=always_notify,
        never_notify_senders=never_notify,
        tracked_item_limit=tracked_item_limit,
        gog_bin=gog_bin,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gmail watcher -- polls Gmail accounts via gog and notifies OpenClaw"
    )
    sub = parser.add_subparsers(dest="command")

    watch_parser = sub.add_parser("watch", help="Poll Gmail accounts for new messages")
    watch_parser.add_argument(
        "--once", action="store_true", help="Run one cycle and exit"
    )
    watch_parser.add_argument("--accounts", help="Comma-separated Gmail addresses")
    watch_parser.add_argument("--hook-url", help="OpenClaw hook URL")
    watch_parser.add_argument("--hook-token", help="OpenClaw hook token")
    watch_parser.add_argument(
        "--channel", help="Delivery channel (telegram, whatsapp, etc.)"
    )
    watch_parser.add_argument("--to", help="Delivery target")
    watch_parser.add_argument("--poll-seconds", help="Seconds between polls")
    watch_parser.add_argument("--max-results", help="Max messages per account per poll")
    watch_parser.add_argument(
        "--include-body",
        action="store_true",
        default=None,
        help="Include body snippet in hook message",
    )
    watch_parser.add_argument(
        "--no-include-body",
        dest="include_body",
        action="store_false",
        help="Do not include body snippet",
    )
    watch_parser.add_argument("--body-max-chars", help="Max body characters to include")
    watch_parser.add_argument("--notify-mode", help="off | auto | all")
    watch_parser.add_argument(
        "--always-notify-senders", help="Comma-separated sender allowlist"
    )
    watch_parser.add_argument(
        "--never-notify-senders", help="Comma-separated sender blocklist"
    )
    watch_parser.add_argument(
        "--tracked-item-limit", help="Max tracked message IDs per account"
    )

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    try:
        config = _config_from_args(args)
    except RuntimeError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    try:
        if args.command == "watch":
            result = watch(config, once=getattr(args, "once", False))
            if result is not None:
                print(json.dumps(result, indent=2, default=str))
            return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# Bot-callable tools (used by brain.py, not the watcher loop)
# ---------------------------------------------------------------------------

_DEFAULT_GMAIL_ACCOUNTS = (
    "islam.moh.islamm@gmail.com",
    "miivii69@gmail.com",
    "miivii420@gmail.com",
    "maeviiss@gmail.com",
)


def _quick_config() -> GmailWatcherConfig:
    """Minimal config for one-shot tool calls (not the watcher loop)."""
    accounts_raw = os.environ.get("GMAIL_WATCHER_ACCOUNTS", "")
    accounts = (
        tuple(a.strip() for a in accounts_raw.split(",") if a.strip())
        if accounts_raw
        else _DEFAULT_GMAIL_ACCOUNTS
    )
    return GmailWatcherConfig(
        accounts=accounts,
        hook_url="",
        hook_token="",
        channel=None,
        to=None,
        poll_seconds=0,
        max_results=10,
        include_body=True,
        body_max_chars=3000,
        notify_mode="off",
        always_notify_senders=(),
        never_notify_senders=(),
        tracked_item_limit=200,
        gog_bin=os.environ.get("GOG_BIN", "gog"),
    )


def tool_check_latest_gmail(max_per_account: int = 5) -> str:
    """Fetch the latest emails from all Gmail accounts with full bodies."""
    config = _quick_config()
    all_lines: list[str] = []
    for account in config.accounts:
        try:
            cmd: list[str] = [
                "gmail",
                "messages",
                "search",
                "newer_than:2d",
                "--account",
                account,
                "--max",
                str(max_per_account),
                "--json",
                "--results-only",
                "--include-body",
            ]
            result = _run_gog(config, *cmd)
            if result.returncode != 0:
                all_lines.append(f"[{account}] Search failed: {result.stderr.strip()}")
                continue
            raw = result.stdout.strip()
            if not raw:
                all_lines.append(f"[{account}] No recent emails.")
                continue
            items = json.loads(raw)
            if not isinstance(items, list) or not items:
                all_lines.append(f"[{account}] No recent emails.")
                continue
            all_lines.append(f"[{account}] {len(items)} email(s):")
            for item in items:
                from_raw = item.get("from", "(unknown)")
                subject = item.get("subject", "(no subject)")
                date = item.get("date", "")
                body = item.get("body", item.get("snippet", ""))
                if body and len(body) > 3000:
                    body = body[:3000] + "..."
                all_lines.append(f"  From: {from_raw}")
                all_lines.append(f"  Subject: {subject}")
                all_lines.append(f"  Date: {date}")
                if body:
                    all_lines.append(f"  Body: {body}")
                all_lines.append("")
        except Exception as exc:
            all_lines.append(f"[{account}] Error: {exc}")
    return "\n".join(all_lines) if all_lines else "No emails found across any account."


def tool_search_gmail(query: str, account: str = "", max_results: int = 10) -> str:
    """Search Gmail by query. If account is empty, searches all accounts."""
    config = _quick_config()
    accounts = (account,) if account else config.accounts
    all_lines: list[str] = []
    for acct in accounts:
        try:
            cmd: list[str] = [
                "gmail",
                "messages",
                "search",
                query,
                "--account",
                acct,
                "--max",
                str(max_results),
                "--json",
                "--results-only",
                "--include-body",
            ]
            result = _run_gog(config, *cmd)
            if result.returncode != 0:
                all_lines.append(f"[{acct}] Search failed: {result.stderr.strip()}")
                continue
            raw = result.stdout.strip()
            if not raw:
                all_lines.append(f"[{acct}] No results for: {query}")
                continue
            items = json.loads(raw)
            if not isinstance(items, list) or not items:
                all_lines.append(f"[{acct}] No results for: {query}")
                continue
            all_lines.append(f"[{acct}] {len(items)} result(s):")
            for item in items:
                from_raw = item.get("from", "(unknown)")
                subject = item.get("subject", "(no subject)")
                date = item.get("date", "")
                body = item.get("body", item.get("snippet", ""))
                if body and len(body) > 2000:
                    body = body[:2000] + "..."
                all_lines.append(f"  From: {from_raw}")
                all_lines.append(f"  Subject: {subject}")
                all_lines.append(f"  Date: {date}")
                if body:
                    all_lines.append(f"  Body: {body}")
                all_lines.append("")
        except Exception as exc:
            all_lines.append(f"[{acct}] Error: {exc}")
    return "\n".join(all_lines) if all_lines else "No results found."
