# Clawd Bot

Telegram and Obsidian assistant running on OpenClaw, backed by Claude on Bedrock and an EC2 deploy flow. The repo contains the extracted `clawd_ops` core plus the OpenClaw bridge used by the live runtime.

## Current Architecture

- `clawd_ops/`: extracted business logic package for vault, memory, tasks, search, audio, and the Bedrock tool loop
- `.openclaw/extensions/clawd-obsidian/`: OpenClaw plugin that bridges tool calls into `python -m clawd_ops`
- `AGENTS.md` and `skills/obsidian-workflow/SKILL.md`: workspace behavior for the OpenClaw side
- `openclaw.example.json5`: starter OpenClaw config for Bedrock + Telegram
- `deploy.sh`, `setup_ec2.sh`, and `sync_app_repo.sh`: EC2 provisioning, bootstrap, and git-based app sync

## Behavior

- Handles text and voice Telegram messages through the OpenClaw gateway.
- Supports `/new`, `/reset`, and `/hardstop` to force a fresh session in Telegram.
- Uses Claude tool use through Bedrock for note reading, note writing, todos, memory, research, and web browsing.
- Syncs the Obsidian vault with fetch plus merge before reads and commit or push after writes.
- Stores durable assistant memory in `memory/clawd.md`, and that file is tracked in the app repo so durable preferences survive deploys and merge back through the normal git sync flow.
- Resolves dated task workflows like `today`, `yesterday`, and explicit dates into `tasks/YYMMDD.md`.
- Uses the Python audio bridge for transcription, with streaming for short notes and batch jobs for longer notes.
- Reports sync conflicts to Telegram with follow-up conflict tools for inspection and explicit resolution.

## Environment

Copy `.env.example` to `.env` and fill in the required values:

```env
TELEGRAM_TOKEN=
ALLOWED_USER_ID=
AWS_REGION=us-east-1
OBSIDIAN_VAULT=/home/ec2-user/obsidian-vault
```

Optional settings include:

- `BEDROCK_MODEL_ID`
- `CLAWD_MEMORY_PATH`
- `BOT_TIMEZONE`
- `TRANSCRIBE_MODE`
- `TRANSCRIBE_TIMEOUT_SECONDS`
- `TRANSCRIBE_BUCKET`
- `TRANSCRIBE_VOCABULARY_NAME`

## Local Development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
npm ci
npm test
python -c "import clawd_ops"
```

The Python contract tests cover task date resolution, durable memory rules, vault path safety, git sync behavior, CLI bridge envelopes, and audio normalization. The OpenClaw test layer checks plugin registration and the Python bridge wiring.

## OpenClaw Runtime

- OpenClaw owns Telegram, sessions, workspace prompting, and media ingestion.
- `clawd_ops` remains the side-effecting Python layer for vault, git sync, Bedrock, and AWS transcription.
- The OpenClaw plugin delegates tool calls through `python -m clawd_ops ... --json`.
- The runtime config is rendered by `python -m clawd_ops.openclaw_config`.

## Deploy

```bash
./deploy.sh
```

That script provisions or reuses the EC2 instance, pushes the local repo to GitHub, syncs the EC2 checkout from GitHub, configures the vault clone, installs dependencies, and restarts the bot service. The EC2 host also runs `sync_app_repo.sh` on a timer so tracked workflow changes made on the server can be pushed back to the repo.
