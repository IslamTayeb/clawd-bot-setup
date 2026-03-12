#!/bin/bash
set -euo pipefail

PROJECT_DIR="${1:-/home/ec2-user/clawd-bot}"
REMOTE_NAME="${GIT_REMOTE_NAME:-origin}"
REMOTE_BRANCH="${GIT_REMOTE_BRANCH:-main}"

cd "$PROJECT_DIR"

if [ ! -d .git ]; then
    exit 0
fi

if ! git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
    exit 0
fi

git fetch "$REMOTE_NAME" "$REMOTE_BRANCH"

git add -A
if ! git diff --cached --quiet --exit-code; then
    git commit -m "Workspace sync: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
fi

if ! git merge --ff-only "$REMOTE_NAME/$REMOTE_BRANCH" >/dev/null 2>&1; then
    if ! git merge --no-edit --autostash "$REMOTE_NAME/$REMOTE_BRANCH"; then
        git merge --abort >/dev/null 2>&1 || true
        exit 1
    fi
fi

git push "$REMOTE_NAME" "HEAD:$REMOTE_BRANCH"
