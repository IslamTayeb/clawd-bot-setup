#!/bin/bash
# EC2 setup script for Amazon Linux 2023 ARM
set -euo pipefail

PROJECT_DIR="/home/ec2-user/clawd-bot"
VENV_DIR="$PROJECT_DIR/.venv"
PYTHON_BIN="/usr/bin/python3.11"
FFMPEG_URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linuxarm64-gpl.tar.xz"
NODE_DIST_URL="https://nodejs.org/dist/latest-v22.x"
NODE_DIR="/opt/node-v22"

run_as_ec2_user() {
    sudo -u ec2-user HOME=/home/ec2-user "$@"
}

echo "=== Installing system packages ==="
dnf update -y
dnf install -y python3.11 python3.11-pip git tar xz \
    nss atk at-spi2-atk cups-libs libdrm libXcomposite libXdamage \
    libXrandr mesa-libgbm pango alsa-lib libXtst \
    libxkbcommon libxkbcommon-x11

echo "=== Installing ffmpeg static build ==="
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
curl -fsSL "$FFMPEG_URL" -o "$tmp_dir/ffmpeg.tar.xz"
tar -xf "$tmp_dir/ffmpeg.tar.xz" -C "$tmp_dir"
install -m 0755 "$tmp_dir"/ffmpeg-master-latest-linuxarm64-gpl/bin/ffmpeg /usr/local/bin/ffmpeg
install -m 0755 "$tmp_dir"/ffmpeg-master-latest-linuxarm64-gpl/bin/ffprobe /usr/local/bin/ffprobe

echo "=== Installing Node.js runtime ==="
node_archive="$(curl -fsSL "$NODE_DIST_URL/SHASUMS256.txt" | awk '/linux-arm64.tar.xz$/ {print $2; exit}')"
curl -fsSL "$NODE_DIST_URL/$node_archive" -o "$tmp_dir/$node_archive"
rm -rf "$NODE_DIR"
mkdir -p "$NODE_DIR"
tar -xf "$tmp_dir/$node_archive" -C "$NODE_DIR" --strip-components=1
ln -sf "$NODE_DIR/bin/node" /usr/local/bin/node
ln -sf "$NODE_DIR/bin/npm" /usr/local/bin/npm
ln -sf "$NODE_DIR/bin/npx" /usr/local/bin/npx

echo "=== Preparing Python runtime ==="
alternatives --set python3 "$PYTHON_BIN" || true
"$PYTHON_BIN" -m pip install --upgrade pip
chown -R ec2-user:ec2-user "$PROJECT_DIR"
chmod +x "$PROJECT_DIR/setup_ec2.sh" "$PROJECT_DIR/sync_app_repo.sh"

echo "=== Installing project dependencies ==="
run_as_ec2_user "$PYTHON_BIN" -m venv "$VENV_DIR"
run_as_ec2_user "$VENV_DIR/bin/python" -m pip install --upgrade pip
run_as_ec2_user "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
if [ -f "$PROJECT_DIR/requirements-dev.txt" ]; then
    run_as_ec2_user "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements-dev.txt"
fi

if [ -f "$PROJECT_DIR/package-lock.json" ]; then
    echo "=== Installing Node dependencies ==="
    run_as_ec2_user bash -lc "cd '$PROJECT_DIR' && npm ci"
fi

echo "=== Installing Playwright Chromium ==="
run_as_ec2_user "$VENV_DIR/bin/python" -m playwright install chromium

echo "=== Configuring git identity ==="
run_as_ec2_user git config --global user.name "clawd-bot"
run_as_ec2_user git config --global user.email "clawd-bot@ec2"

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

systemctl daemon-reload
systemctl enable --now clawd-bot-repo-sync.timer

if grep -Eq '^TELEGRAM_TOKEN=.+' "$PROJECT_DIR/.env" && grep -Eq '^ALLOWED_USER_ID=.+' "$PROJECT_DIR/.env"; then
    systemctl enable clawd-bot
    echo "=== Bot service enabled ==="
else
    systemctl disable clawd-bot >/dev/null 2>&1 || true
    echo "=== Bot service left disabled: TELEGRAM_TOKEN or ALLOWED_USER_ID missing ==="
fi

echo "=== Setup complete ==="
