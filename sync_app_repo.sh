#!/bin/bash
set -euo pipefail

PROJECT_DIR="${1:-/home/ec2-user/clawd-bot}"
PYTHON_EXEC="${PYTHON_EXEC:-$PROJECT_DIR/.venv/bin/python}"

if [ ! -x "$PYTHON_EXEC" ]; then
    PYTHON_EXEC="$(command -v python3)"
fi

exec "$PYTHON_EXEC" -m clawd_ops sync_app_repo --json --payload "{\"project_dir\": \"$PROJECT_DIR\"}"
