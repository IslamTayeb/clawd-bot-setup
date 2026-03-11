# Clawd Bot

Telegram assistant for an Obsidian vault, backed by Claude on Bedrock, AWS Transcribe, and a one-command EC2 deploy flow.

## What It Does

- Handles text and voice messages from Telegram.
- Uses Claude tool use through Bedrock for note reading, note writing, todos, memory, research, and web browsing.
- Syncs an Obsidian vault with Git pull before reads and Git commit/push after writes.
- Stores durable assistant memory in `personal/clawd.md`.
- Uses AWS Transcribe with streaming for short notes and batch jobs for longer notes.

## Files

- `bot.py`: Telegram entrypoint and handlers
- `brain.py`: Bedrock Converse loop and tool wiring
- `obsidian.py`: vault operations, task workflow, memory, and git sync
- `transcribe.py`: audio conversion and AWS Transcribe
- `search.py`: arXiv, Scholar, and web fetch helpers
- `deploy.sh`: AWS provisioning and deploy
- `setup_ec2.sh`: instance bootstrap

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
- `TRANSCRIBE_BUCKET`
- `TRANSCRIBE_VOCABULARY_NAME`

## Local Smoke Checks

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -c "import bot, brain, obsidian, search, transcribe"
```

## Deploy

```bash
./deploy.sh
```

That script provisions or reuses the EC2 instance, uploads the project, configures the vault clone, installs dependencies, and restarts the bot service.
