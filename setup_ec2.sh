#!/bin/bash
# EC2 setup script for Amazon Linux 2023 ARM
set -euo pipefail

PROJECT_DIR="/home/ec2-user/clawd-bot"
VENV_DIR="$PROJECT_DIR/.venv"
STATE_DIR="$PROJECT_DIR/.setup-state"
PYTHON_DEPS_HASH_FILE="$STATE_DIR/python-deps.sha256"
NODE_LOCK_HASH_FILE="$STATE_DIR/package-lock.sha256"
PYTHON_BIN="/usr/bin/python3.11"
FFMPEG_URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linuxarm64-gpl.tar.xz"
NODE_DIST_URL="https://nodejs.org/dist/latest-v22.x"
NODE_DIR="/opt/node-v22"
GOG_VERSION="0.12.0"
GOG_URL="https://github.com/steipete/gogcli/releases/download/v${GOG_VERSION}/gogcli_${GOG_VERSION}_linux_arm64.tar.gz"
NODE_MODULES_BACKUP_DIR=""
TMP_DIR=""

run_as_ec2_user() {
    sudo -u ec2-user HOME=/home/ec2-user "$@"
}

ensure_state_dir() {
    mkdir -p "$STATE_DIR"
    chown ec2-user:ec2-user "$STATE_DIR"
}

sha256_file() {
    sha256sum "$1" | awk '{print $1}'
}

python_deps_hash() {
    {
        sha256_file "$PROJECT_DIR/requirements.txt"
        if [ -f "$PROJECT_DIR/requirements-dev.txt" ]; then
            sha256_file "$PROJECT_DIR/requirements-dev.txt"
        fi
    } | sha256sum | awk '{print $1}'
}

python_runtime_ready() {
    [ -x "$VENV_DIR/bin/python" ] || return 1
    run_as_ec2_user "$VENV_DIR/bin/python" -c "import boto3, clawd_ops" >/dev/null 2>&1
}

install_python_dependencies() {
    local desired_hash current_hash=""
    desired_hash="$(python_deps_hash)"
    if [ -f "$PYTHON_DEPS_HASH_FILE" ]; then
        current_hash="$(cat "$PYTHON_DEPS_HASH_FILE")"
    fi

    if python_runtime_ready && [ "$desired_hash" = "$current_hash" ]; then
        echo "=== Reusing Python dependencies ==="
        return
    fi

    echo "=== Installing project dependencies ==="
    run_as_ec2_user "$PYTHON_BIN" -m venv "$VENV_DIR"
    run_as_ec2_user "$VENV_DIR/bin/python" -m pip install --upgrade pip
    run_as_ec2_user "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
    if [ -f "$PROJECT_DIR/requirements-dev.txt" ]; then
        run_as_ec2_user "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements-dev.txt"
    fi

    printf '%s\n' "$desired_hash" > "$PYTHON_DEPS_HASH_FILE"
    chown ec2-user:ec2-user "$PYTHON_DEPS_HASH_FILE"
}

node_modules_ready() {
    [ -f "$PROJECT_DIR/node_modules/openclaw/openclaw.mjs" ]
}

package_lock_hash() {
    sha256_file "$PROJECT_DIR/package-lock.json"
}

restore_node_modules_backup() {
    if [ -z "${NODE_MODULES_BACKUP_DIR:-}" ] || [ ! -d "$NODE_MODULES_BACKUP_DIR" ]; then
        return
    fi

    if node_modules_ready; then
        rm -rf "$NODE_MODULES_BACKUP_DIR"
        NODE_MODULES_BACKUP_DIR=""
        return
    fi

    rm -rf "$PROJECT_DIR/node_modules"
    mv "$NODE_MODULES_BACKUP_DIR" "$PROJECT_DIR/node_modules"
    NODE_MODULES_BACKUP_DIR=""
}

cleanup() {
    restore_node_modules_backup
    if [ -n "${TMP_DIR:-}" ] && [ -d "$TMP_DIR" ]; then
        rm -rf "$TMP_DIR"
    fi
}

install_node_dependencies() {
    if [ ! -f "$PROJECT_DIR/package-lock.json" ]; then
        return
    fi

    local desired_hash current_hash=""
    desired_hash="$(package_lock_hash)"
    if [ -f "$NODE_LOCK_HASH_FILE" ]; then
        current_hash="$(cat "$NODE_LOCK_HASH_FILE")"
    fi

    if node_modules_ready && [ "$desired_hash" = "$current_hash" ]; then
        echo "=== Reusing Node dependencies ==="
        return
    fi

    echo "=== Installing Node dependencies ==="
    if [ -d "$PROJECT_DIR/node_modules" ]; then
        NODE_MODULES_BACKUP_DIR="$PROJECT_DIR/node_modules.backup.$$"
        mv "$PROJECT_DIR/node_modules" "$NODE_MODULES_BACKUP_DIR"
    fi

    run_as_ec2_user bash -lc "cd '$PROJECT_DIR' && npm ci --no-audit --no-fund --loglevel=warn --progress=false"

    if ! node_modules_ready; then
        echo "ERROR: npm ci completed without installing OpenClaw." >&2
        exit 1
    fi

    printf '%s\n' "$desired_hash" > "$NODE_LOCK_HASH_FILE"
    chown ec2-user:ec2-user "$NODE_LOCK_HASH_FILE"

    if [ -n "$NODE_MODULES_BACKUP_DIR" ] && [ -d "$NODE_MODULES_BACKUP_DIR" ]; then
        rm -rf "$NODE_MODULES_BACKUP_DIR"
    fi
    NODE_MODULES_BACKUP_DIR=""
}

playwright_chromium_ready() {
    run_as_ec2_user bash -lc "find /home/ec2-user/.cache/ms-playwright -maxdepth 1 -type d -name 'chromium-*' 2>/dev/null | grep -q ."
}

trap cleanup EXIT HUP INT TERM

echo "=== Installing system packages ==="
dnf update -y
dnf install -y python3.11 python3.11-pip git tar xz \
    nss atk at-spi2-atk cups-libs libdrm libXcomposite libXdamage \
    libXrandr mesa-libgbm pango alsa-lib libXtst \
    libxkbcommon libxkbcommon-x11

echo "=== Installing ffmpeg static build ==="
TMP_DIR="$(mktemp -d)"
curl -fsSL "$FFMPEG_URL" -o "$TMP_DIR/ffmpeg.tar.xz"
tar -xf "$TMP_DIR/ffmpeg.tar.xz" -C "$TMP_DIR"
install -m 0755 "$TMP_DIR"/ffmpeg-master-latest-linuxarm64-gpl/bin/ffmpeg /usr/local/bin/ffmpeg
install -m 0755 "$TMP_DIR"/ffmpeg-master-latest-linuxarm64-gpl/bin/ffprobe /usr/local/bin/ffprobe

echo "=== Installing Node.js runtime ==="
node_archive="$(curl -fsSL "$NODE_DIST_URL/SHASUMS256.txt" | awk '/linux-arm64.tar.xz$/ {print $2; exit}')"
curl -fsSL "$NODE_DIST_URL/$node_archive" -o "$TMP_DIR/$node_archive"
rm -rf "$NODE_DIR"
mkdir -p "$NODE_DIR"
tar -xf "$TMP_DIR/$node_archive" -C "$NODE_DIR" --strip-components=1
ln -sf "$NODE_DIR/bin/node" /usr/local/bin/node
ln -sf "$NODE_DIR/bin/npm" /usr/local/bin/npm
ln -sf "$NODE_DIR/bin/npx" /usr/local/bin/npx

echo "=== Installing gog (Google Workspace CLI) ==="
if command -v gog >/dev/null 2>&1 && gog --version 2>/dev/null | grep -qF "$GOG_VERSION"; then
    echo "gog $GOG_VERSION already installed"
else
    curl -fsSL "$GOG_URL" -o "$TMP_DIR/gogcli.tar.gz"
    tar -xf "$TMP_DIR/gogcli.tar.gz" -C "$TMP_DIR"
    install -m 0755 "$TMP_DIR/gog" /usr/local/bin/gog
    echo "Installed gog $(gog --version 2>&1 | head -1)"
fi

echo "=== Preparing Python runtime ==="
alternatives --set python3 "$PYTHON_BIN" || true
"$PYTHON_BIN" -m pip install --upgrade pip
chown -R ec2-user:ec2-user "$PROJECT_DIR"
chmod +x "$PROJECT_DIR/setup_ec2.sh" "$PROJECT_DIR/sync_app_repo.sh"
ensure_state_dir

install_python_dependencies
install_node_dependencies

if playwright_chromium_ready; then
    echo "=== Reusing Playwright Chromium ==="
else
    echo "=== Installing Playwright Chromium ==="
    run_as_ec2_user "$VENV_DIR/bin/python" -m playwright install chromium
fi

echo "=== Configuring git identity ==="
run_as_ec2_user git config --global user.name "IslamTayeb"
run_as_ec2_user git config --global user.email "147297243+IslamTayeb@users.noreply.github.com"

echo "=== Creating systemd service ==="
cat > /etc/systemd/system/clawd-bot.service <<'SERVICEEOF'
[Unit]
Description=Clawd OpenClaw Gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/clawd-bot
Environment=HOME=/home/ec2-user
Environment=AWS_PROFILE=default
Environment=OPENCLAW_CONFIG_PATH=/home/ec2-user/clawd-bot/openclaw.runtime.json
Environment=OPENCLAW_STATE_DIR=/home/ec2-user/.openclaw
Environment=PATH=/usr/local/bin:/usr/bin:/bin:/home/ec2-user/clawd-bot/node_modules/.bin:/home/ec2-user/clawd-bot/.venv/bin
EnvironmentFile=/home/ec2-user/clawd-bot/.env
ExecStartPre=/bin/bash -lc 'set -a && source /home/ec2-user/clawd-bot/.env && export AWS_PROFILE="${AWS_PROFILE:-default}" && set +a && cd /home/ec2-user/clawd-bot && /home/ec2-user/clawd-bot/.venv/bin/python -m clawd_ops.openclaw_config --output /home/ec2-user/clawd-bot/openclaw.runtime.json --workspace /home/ec2-user/clawd-bot --python-exec /home/ec2-user/clawd-bot/.venv/bin/python'
ExecStart=/usr/local/bin/node /home/ec2-user/clawd-bot/node_modules/openclaw/openclaw.mjs gateway run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICEEOF

cat > /etc/systemd/system/clawd-bot-repo-sync.service <<'SERVICEEOF'
[Unit]
Description=Sync Clawd app repo to GitHub
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=ec2-user
WorkingDirectory=/home/ec2-user/clawd-bot
Environment=HOME=/home/ec2-user
Environment=PATH=/usr/local/bin:/usr/bin:/bin
ExecStart=/bin/bash -lc '/home/ec2-user/clawd-bot/sync_app_repo.sh'
SERVICEEOF

cat > /etc/systemd/system/clawd-bot-repo-sync.timer <<'SERVICEEOF'
[Unit]
Description=Periodic Clawd app repo sync

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
Unit=clawd-bot-repo-sync.service

[Install]
WantedBy=timers.target
SERVICEEOF

cat > /etc/systemd/system/clawd-bot-duke-exchange.service <<'SERVICEEOF'
[Unit]
Description=Clawd Duke Exchange watcher
After=network-online.target clawd-bot.service
Wants=network-online.target clawd-bot.service

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/clawd-bot
Environment=HOME=/home/ec2-user
Environment=OPENCLAW_STATE_DIR=/home/ec2-user/.openclaw
Environment=PATH=/usr/local/bin:/usr/bin:/bin:/home/ec2-user/clawd-bot/.venv/bin
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/home/ec2-user/clawd-bot/.env
ExecStart=/home/ec2-user/clawd-bot/.venv/bin/python -m clawd_ops.exchange watch
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
SERVICEEOF

systemctl daemon-reload
systemctl enable --now clawd-bot-repo-sync.timer

if grep -Eq '^TELEGRAM_TOKEN=.+' "$PROJECT_DIR/.env" && grep -Eq '^ALLOWED_USER_ID=.+' "$PROJECT_DIR/.env"; then
    systemctl enable clawd-bot
    echo "=== Bot service enabled ==="
else
    systemctl disable clawd-bot >/dev/null 2>&1 || true
    echo "=== Bot service left disabled: TELEGRAM_TOKEN or ALLOWED_USER_ID missing ==="
fi

if grep -Eqi '^DUKE_EXCHANGE_ENABLED=(1|true|yes|on)$' "$PROJECT_DIR/.env"; then
    systemctl enable clawd-bot-duke-exchange
    echo "=== Duke Exchange watcher enabled ==="
else
    systemctl disable clawd-bot-duke-exchange >/dev/null 2>&1 || true
    echo "=== Duke Exchange watcher left disabled: set DUKE_EXCHANGE_ENABLED=true after auth ==="
fi

echo "=== Setup complete ==="
