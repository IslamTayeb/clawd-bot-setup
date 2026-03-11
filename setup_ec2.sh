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
dnf install -y python3.11 python3.11-pip git tar xz curl \
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

echo "=== Installing project dependencies ==="
run_as_ec2_user "$PYTHON_BIN" -m venv "$VENV_DIR"
run_as_ec2_user "$VENV_DIR/bin/python" -m pip install --upgrade pip
run_as_ec2_user "$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

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
Description=Clawd Telegram Bot
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/clawd-bot
Environment=HOME=/home/ec2-user
EnvironmentFile=/home/ec2-user/clawd-bot/.env
ExecStart=/home/ec2-user/clawd-bot/.venv/bin/python /home/ec2-user/clawd-bot/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICEEOF

systemctl daemon-reload

if grep -Eq '^TELEGRAM_TOKEN=.+' "$PROJECT_DIR/.env" && grep -Eq '^ALLOWED_USER_ID=.+' "$PROJECT_DIR/.env"; then
    systemctl enable clawd-bot
    echo "=== Bot service enabled ==="
else
    systemctl disable clawd-bot >/dev/null 2>&1 || true
    echo "=== Bot service left disabled: TELEGRAM_TOKEN or ALLOWED_USER_ID missing ==="
fi

echo "=== Setup complete ==="
