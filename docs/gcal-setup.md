# Google Calendar Setup via gog + OpenClaw

Calendar integration uses the same `gog` CLI and OAuth credentials as Gmail.
Unlike Gmail (which uses Pub/Sub push), calendar is accessed on-demand via
`gog calendar` commands through the exec tool, or scheduled via OpenClaw cron jobs.

## Prerequisites

- `gog` installed on the EC2 host (see `docs/gmail-setup.md` for install instructions)
- OAuth credentials already stored with `gog auth credentials` (same as Gmail setup)
- The `exec` tool enabled in OpenClaw config (already configured in `clawd_ops/openclaw_config.py`)

## How it works

OpenClaw discovers the bundled gog skill from `node_modules/openclaw/skills/gog/SKILL.md`.
The agent can then run `gog calendar` commands via the `exec` tool, which is already
in the `alsoAllow` list.

No webhook preset is needed for calendar. Gmail uses Pub/Sub for real-time push
notifications; calendar is queried on demand or on a schedule.

## 1. Authorize accounts with calendar scope

When you run `gog auth add`, include `calendar` in the services list.
The `google_auth.py` helper already defaults to `--services gmail,calendar`.

```bash
gog auth add you@gmail.com --services gmail,calendar
gog auth add school@gmail.com --services gmail,calendar
```

If you already authorized an account for Gmail only, re-run `gog auth add` with
the expanded services list. This starts a new OAuth flow that requests both scopes.

## 2. Verify calendar access

After OAuth completes, confirm gog can reach the calendar API:

```bash
gog calendar calendars --account you@gmail.com
gog calendar events --account you@gmail.com --today
```

## 3. Available calendar commands

These are the commands the agent can use (via exec tool):

```bash
# List calendars
gog calendar calendars --account you@gmail.com

# List events (today, this week, date range)
gog calendar events --account you@gmail.com --today
gog calendar events --account you@gmail.com --week
gog calendar events --account you@gmail.com --from 2026-04-06 --to 2026-04-13

# Create an event
gog calendar create primary --account you@gmail.com \
  --summary "Meeting" --from 2026-04-07T10:00:00 --to 2026-04-07T11:00:00

# Update an event
gog calendar update primary <eventId> --account you@gmail.com --summary "New Title"

# Delete an event
gog calendar delete primary <eventId> --account you@gmail.com

# Search events
gog calendar search "dentist" --account you@gmail.com

# Check free/busy
gog calendar freebusy --account you@gmail.com --from 2026-04-07 --to 2026-04-08

# Find conflicts
gog calendar conflicts --account you@gmail.com

# RSVP to an invitation
gog calendar respond primary <eventId> --account you@gmail.com --status accepted
```

## 4. Optional: daily calendar briefing via cron

Set up a recurring cron job to get a daily calendar summary delivered to Telegram:

```bash
openclaw cron add \
  --name "Morning calendar" \
  --cron "0 7 * * *" \
  --tz "America/New_York" \
  --session isolated \
  --message "List my calendar events for today using gog. Summarize what I have coming up." \
  --announce \
  --channel telegram \
  --to "<your-telegram-chat-id>"
```

## 5. Notes

- `gog` uses `--account` to select which authorized Google account to query.
  Set `GOG_ACCOUNT=you@gmail.com` in the environment to avoid repeating it.
- Calendar commands do not need Tailscale Funnel or any public endpoint.
- The same OAuth client (Google Cloud project) is shared with Gmail.
- For scripting, use `--json --results-only --no-input` flags.

## Adding more Gmail accounts to OpenClaw

Each Gmail account needs its own OAuth authorization. Run these steps per account:

1. Start the OAuth flow (on the EC2 host or locally):

   ```bash
   gog auth add newaccount@gmail.com --services gmail,calendar
   ```

2. Complete the browser-based OAuth consent.

3. Verify:

   ```bash
   gog auth list
   gog calendar calendars --account newaccount@gmail.com
   gog gmail messages search "newer_than:1d" --account newaccount@gmail.com --max 5
   ```

4. If you want Gmail push notifications for this account, also run:

   ```bash
   openclaw webhooks gmail setup --account newaccount@gmail.com
   ```

5. Repeat for each additional account.

Current `gog auth list` shows which accounts are already authorized.
Accounts authorized with `--services gmail,calendar` already have both scopes.
