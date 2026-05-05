# Duke Exchange Setup

This is the Duke-specific path for email integration when Gmail + `gog` is not the right fit.

## Why this exists

- Duke email is Microsoft 365 / Exchange Online, not Gmail.
- Duke's own docs show third-party clients working through Exchange modern auth, not Gmail APIs.
- IMAP is not the integration target here.
- Duke Health / med-school access can add mobile device management and policy constraints, so this path avoids plain IMAP entirely.

References:

- Duke Apple Mail setup: `https://oit.duke.edu/help/articles/kb0014579/`
- Duke Evolution modern auth setup: `https://oit.duke.edu/help/articles/kb0032012/`
- Duke Microsoft 365 service page: `https://oit.duke.edu/service/microsoft-365-formerly-office-365/`
- Microsoft EWS OAuth: `https://learn.microsoft.com/en-us/exchange/client-developer/exchange-web-services/how-to-authenticate-an-ews-application-by-using-oauth`

## What this repo now provides

This repo now includes a small Exchange sidecar at `clawd_ops/exchange.py`.

Flow:

1. OAuth2 device login against Microsoft 365 / Exchange Online.
2. Poll the Duke inbox through EWS.
3. Post new-message events into local OpenClaw hooks.

This avoids Gmail, avoids IMAP, and follows the same Exchange modern-auth family Duke documents for Apple Mail and Evolution.

## Default Duke OAuth settings

The sidecar defaults to the application ID Duke publishes for Evolution modern auth:

```text
20460e5d-ce91-49af-a3a5-70b6be7486d1
```

Default tenant hint:

```text
organizations
```

Default scope:

```text
offline_access https://outlook.office365.com/EWS.AccessAsUser.All
```

If Duke or Microsoft policy blocks that flow for your account, the fallback is a custom Entra app registration with delegated `EWS.AccessAsUser.All` consent.

## Safety default

By default this sidecar does not include email body text in hook payloads.

Why:

- Duke Health / med-school mail may include sensitive content.
- Sending raw body text into Telegram or other chat delivery paths may be a policy or privacy problem.

Body forwarding is opt-in with:

```bash
DUKE_EXCHANGE_INCLUDE_BODY=true
```

## Environment variables

Placeholders live in `.env.example` only. Real values go in the EC2 host `.env`.

Core settings:

```bash
DUKE_EXCHANGE_EMAIL=
DUKE_EXCHANGE_ENABLED=false
DUKE_EXCHANGE_CLIENT_ID=20460e5d-ce91-49af-a3a5-70b6be7486d1
DUKE_EXCHANGE_TENANT=organizations
DUKE_EXCHANGE_POLL_SECONDS=60
DUKE_EXCHANGE_INCLUDE_BODY=false
DUKE_EXCHANGE_BODY_MAX_CHARS=1200
DUKE_EXCHANGE_NOTIFY_MODE=important
```

Optional overrides:

```bash
DUKE_EXCHANGE_TOKEN_PATH=
DUKE_EXCHANGE_SYNC_STATE_PATH=
DUKE_EXCHANGE_DELIVER_CHANNEL=telegram
DUKE_EXCHANGE_DELIVER_TO=
DUKE_EXCHANGE_HOOK_URL=http://127.0.0.1:18789/hooks/agent
DUKE_EXCHANGE_ALWAYS_NOTIFY_SENDERS=
DUKE_EXCHANGE_NEVER_NOTIFY_SENDERS=
DUKE_EXCHANGE_TRACKED_ITEM_LIMIT=200
```

Notes:

- Hook auth uses `OPENCLAW_HOOK_TOKEN`.
- If `DUKE_EXCHANGE_DELIVER_TO` is unset, the sidecar falls back to `ALLOWED_USER_ID`.
- If no explicit delivery target exists, OpenClaw hook delivery falls back to the last route.
- `DUKE_EXCHANGE_NOTIFY_MODE=important` is the conservative default. Bulk mail, newsletters, digests, routine confirmations, and generic opportunity blasts are suppressed unless you explicitly allowlist a sender.
- Use `DUKE_EXCHANGE_ALWAYS_NOTIFY_SENDERS` and `DUKE_EXCHANGE_NEVER_NOTIFY_SENDERS` as comma-separated sender substrings for ad hoc tuning.

## One-time auth

Run on the EC2 host:

```bash
/home/ec2-user/clawd-bot/.venv/bin/python -m clawd_ops.exchange auth-device \
  --email your-netid@duke.edu
```

The command prints a Microsoft device-login URL and code.

Complete the sign-in in a browser:

- Use your Duke account.
- Complete NetID + MFA.
- Accept any consent screen if presented.

On success, the token cache is written under:

```text
~/.openclaw/duke-exchange/token.json
```

unless overridden by `DUKE_EXCHANGE_TOKEN_PATH`.

## Probe mailbox access

After auth, test read access:

```bash
/home/ec2-user/clawd-bot/.venv/bin/python -m clawd_ops.exchange probe \
  --email your-netid@duke.edu
```

That returns the most recent inbox items through EWS.

## Run the watcher manually

One cycle:

```bash
/home/ec2-user/clawd-bot/.venv/bin/python -m clawd_ops.exchange watch \
  --email your-netid@duke.edu \
  --once
```

Long-running watcher:

```bash
/home/ec2-user/clawd-bot/.venv/bin/python -m clawd_ops.exchange watch \
  --email your-netid@duke.edu
```

First run behavior:

- The watcher starts from the current inbox head and records a recent-message watermark.
- Existing messages are not emitted on bootstrap.
- Only later messages trigger OpenClaw hook events.
- Older `sync_state` files from the earlier EWS sync approach are treated as stale and replaced by the recent-message watermark automatically.

## Systemd service

`setup_ec2.sh` now defines a `clawd-bot-duke-exchange.service` unit.

Enable it only after:

1. `DUKE_EXCHANGE_EMAIL` is set in `.env`
2. `OPENCLAW_HOOK_TOKEN` is set in `.env`
3. `auth-device` has completed successfully
4. You intentionally want this mailbox wired into OpenClaw

Then:

```bash
sudo systemctl enable --now clawd-bot-duke-exchange
```

Status:

```bash
sudo systemctl status clawd-bot-duke-exchange --no-pager
sudo journalctl -u clawd-bot-duke-exchange -n 100 --no-pager
```

## Likely blockers

These are the main Duke-specific failure modes to expect:

1. Consent blocked by tenant policy
2. Device code flow blocked for the published client ID
3. Duke Health / med-school conditional access restrictions
4. EWS disabled for the mailbox

If that happens, the next escalation path is not Gmail or IMAP. It is:

1. Register a Duke-approved public client in Entra
2. Grant delegated `EWS.AccessAsUser.All`
3. Re-run `auth-device` with `DUKE_EXCHANGE_CLIENT_ID=<new-client-id>`

If Duke eventually prefers Microsoft Graph instead of EWS for your account, this repo can add a Graph watcher later. This first pass is intentionally using EWS because it matches Duke's published modern-auth client guidance most closely.

## Fallback: forward Duke email to Gmail

If the Exchange/EWS integration is blocked by policy, Duke University students can forward their email to one Gmail account as a workaround. Duke Health users cannot use this option.

Steps:

1. Log into `https://mail.duke.edu`.
2. Go to Settings > View all Outlook settings > Mail > Forwarding.
3. Enter one of your Gmail addresses (e.g. `islam.moh.islamm@gmail.com`).
4. Check "Keep a copy" so Duke mail is still available in Outlook.
5. Save.

Caveats:

- Only one external forwarding address is supported by Duke OIT.
- Delivery is not guaranteed: some messages may be forwarded but rejected by Gmail's spam filters or the sender's DMARC policy.
- Forwarded mail will appear in the Gmail watcher, not the Duke Exchange watcher. The `from:` address is preserved, so filtering still works.
- This is a workaround, not a replacement for the direct Exchange integration.

Reference: `https://oit.duke.edu/help/articles/kb0015771/`
