# Gmail Setup via gog + OpenClaw

This repo now enables the base OpenClaw hook config needed for Gmail Pub/Sub:

```json
{
  "hooks": {
    "enabled": true,
    "token": "OPENCLAW_HOOK_TOKEN",
    "path": "/hooks",
    "presets": ["gmail"]
  }
}
```

Important limits for this pass:

- Do not edit `openclaw.runtime.json` directly. It is regenerated from `clawd_ops/openclaw_config.py` on every service start.
- Do not run `gog auth ...` from automation here. Those commands need user OAuth approval.
- Non-Gmail accounts (Exchange/IMAP/school/other providers) are out of scope here. To bring those into this flow later, forward them into a Gmail inbox first.
- Some school-managed inboxes may require provider-specific OAuth2 and may block plain IMAP or forwarding. Duke email should be treated as a separate OAuth2 integration, not assumed to fit the Gmail-forwarding fallback.

References:

- OpenClaw Gmail Pub/Sub doc: `node_modules/openclaw/docs/automation/gmail-pubsub.md`
- OpenClaw webhook doc: `node_modules/openclaw/docs/automation/webhook.md`
- OpenClaw CLI doc: `node_modules/openclaw/docs/cli/webhooks.md`
- gog skill: `node_modules/openclaw/skills/gog/SKILL.md`

## 1. Install gog

The task-provided command was checked first:

```bash
which gog || npm install -g gogcli
```

Result from this setup pass:

- `npm install -g gogcli` failed with `404 Not Found`; `gogcli` is not published on npm here.
- `gog` was then installed locally with `brew install gogcli`.
- `gog` was also installed on the EC2 host from the official `gogcli_0.12.0_linux_arm64.tar.gz` release archive.

Supported install paths from `https://gogcli.sh` are:

```bash
brew install gogcli
```

or build from source:

```bash
git clone https://github.com/steipete/gogcli.git
cd gogcli
make
./bin/gog --help
```

For Amazon Linux, building from source is the documented fallback if Homebrew is not present.

## 2. Check Tailscale

Tailscale Funnel is the supported public HTTPS endpoint for Gmail Pub/Sub pushes.

Current check result during this setup pass:

```bash
which tailscale
```

Result: no path returned locally, and the EC2 host also does not currently have `tailscale` installed.

Before Gmail Pub/Sub can work on the EC2 host, install Tailscale there, log in, and confirm Funnel prerequisites. OpenClaw's Gmail Pub/Sub docs explicitly call out Tailscale Funnel as the supported setup.

## 3. Create the Google Cloud project

1. Open Google Cloud Console.
2. Create or select a project dedicated to the Gmail integration.
3. Make sure this is the same project that owns the OAuth client used by `gog`.

Enable the required APIs:

- Gmail API
- Cloud Pub/Sub API

From CLI, that is:

```bash
gcloud auth login
gcloud config set project <project-id>
gcloud services enable gmail.googleapis.com pubsub.googleapis.com
```

## 4. Create OAuth credentials

1. Open Google Cloud Console -> APIs & Services -> Credentials.
2. Create an OAuth client.
3. Choose `Desktop app` as the application type.
4. Download the client JSON file.
5. Keep that file outside the repo, for example in `~/Downloads/client_secret.json`.

## 5. Store gog credentials

Run:

```bash
gog auth credentials /path/to/client_secret.json
```

Example:

```bash
gog auth credentials ~/Downloads/client_secret.json
```

## 6. Authorize each Gmail account

Run this once per Gmail account you want OpenClaw to use:

```bash
gog auth add <email> --services gmail,calendar
```

Examples:

```bash
gog auth add you@gmail.com --services gmail,calendar
gog auth add school@gmail.com --services gmail,calendar
gog auth add medschool@gmail.com --services gmail,calendar
```

Start with Gmail accounts only. Keep non-Gmail inboxes out of this flow until you confirm they support a workable forwarding or OAuth2 path.

## 7. Configure OpenClaw Gmail webhooks per account

After `gog` auth is complete for an account, run:

```bash
openclaw webhooks gmail setup --account <email>
```

Example:

```bash
openclaw webhooks gmail setup --account you@gmail.com
```

What this does, per the bundled OpenClaw docs:

- Writes `hooks.gmail` config for `openclaw webhooks gmail run`
- Enables the Gmail hook preset
- Uses the hook endpoint path under `/hooks`
- Assumes a Tailscale Funnel-backed public push endpoint by default

Repeat that command for each Gmail account you want wired into OpenClaw.

## 8. Notes for this repo

- Hook token env var: set `OPENCLAW_HOOK_TOKEN` in the real `.env` on the EC2 host.
- Placeholder only: `.env.example` now includes `OPENCLAW_HOOK_TOKEN=`.
- The generated config falls back to `change-me` if the hook token is unset. Replace that with a real secret before exposing hooks.
- The gateway will only auto-manage the Gmail watcher after `openclaw webhooks gmail setup` has written the `hooks.gmail` account config.

## 9. Duke email / SSO findings

Duke email is not Gmail. Duke OIT documents it as Microsoft 365 / Exchange Online.

What that means:

- `gog` is the right tool for Gmail accounts.
- `gog` is not the right tool for a Duke mailbox.
- Apple Mail works because it adds the account as `Exchange`, then redirects through Microsoft modern authentication and Duke's NetID + MFA login.

Confirmed Duke references:

- Microsoft 365 service page: `https://oit.duke.edu/service/microsoft-365-formerly-office-365/`
- Apple Mail setup: `https://oit.duke.edu/help/articles/kb0014579/`
- GNOME Evolution modern auth setup: `https://oit.duke.edu/help/articles/kb0032012/`

Useful details from Duke's Evolution guide:

- Protocol family: Exchange Web Services, not Gmail
- Host URL: `https://outlook.office365.com/EWS/Exchange.asmx`
- Auth mode: `OAuth2 (Office365)`
- Duke-documented Office365 OAuth application ID: `20460e5d-ce91-49af-a3a5-70b6be7486d1`
- Login then redirects to Duke Shibboleth / NetID auth

If you want OpenClaw automation for Duke mail later, the likely directions are:

1. Build a separate Microsoft 365 / Exchange integration.
2. Use Microsoft OAuth2 against Exchange Online or Microsoft Graph.
3. Treat Duke as a different project from the Gmail + `gog` integration.

For raw IMAP/SMTP OAuth2, Microsoft documents the required scopes here:

- `https://outlook.office.com/IMAP.AccessAsUser.All`
- `https://outlook.office.com/SMTP.Send`

Reference: `https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth`

Important caveat: Microsoft-style OAuth app registration or tenant consent may be required for a true programmatic Duke integration. That is separate from the Gmail setup in this document.

This repo now has a separate Duke-specific guide at `docs/duke-exchange-setup.md`.
