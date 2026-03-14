#!/bin/bash
set -e

APP_DIR="/opt/aiap-protocol"
APP_USER="aiap"
VENV_DIR="$APP_DIR/venv"

echo "=== AIAP Meeting Protocol Service — Installer ==="

# 1. System dependencies
echo "[1/6] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv ffmpeg fonts-dejavu-core

# 2. Create service user
echo "[2/6] Creating service user..."
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$APP_USER"
fi

# 3. Deploy application
echo "[3/6] Deploying application..."
mkdir -p "$APP_DIR"
cp -r app requirements.txt "$APP_DIR/"

# Copy fonts for PDF generation
mkdir -p "$APP_DIR/fonts"
cp /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf "$APP_DIR/fonts/" 2>/dev/null || true
cp /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf "$APP_DIR/fonts/" 2>/dev/null || true

# 4. Create virtualenv and install dependencies
echo "[4/6] Setting up Python environment..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt" -q

# 5. Setup .env if not exists
if [ ! -f "$APP_DIR/.env" ]; then
    echo "[5/6] Creating .env from template..."
    if [ -f .env.example ]; then
        cp .env.example "$APP_DIR/.env"
    else
        cat > "$APP_DIR/.env" <<'ENVEOF'
YANDEX_API_KEY=
YANDEX_FOLDER_ID=
ANTHROPIC_API_KEY=
S3_ENDPOINT_URL=https://storage.yandexcloud.net
S3_ACCESS_KEY=
S3_SECRET_KEY=
S3_BUCKET_NAME=
APP_HOST=0.0.0.0
APP_PORT=8000
UPLOAD_DIR=/tmp/aiap-uploads
MAX_AUDIO_SIZE_MB=500
ENVEOF
    fi
    echo "  >> IMPORTANT: Edit $APP_DIR/.env with your credentials!"
else
    echo "[5/6] .env already exists, skipping."
fi

# Set permissions
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
mkdir -p /tmp/aiap-uploads
chown "$APP_USER":"$APP_USER" /tmp/aiap-uploads

# 6. Install systemd service
echo "[6/6] Installing systemd service..."
cat > /etc/systemd/system/aiap-protocol.service <<SVCEOF
[Unit]
Description=AIAP Meeting Protocol Service
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=$APP_DIR/.env
ExecStart=$VENV_DIR/bin/uvicorn app.main:app --host \${APP_HOST:-0.0.0.0} --port \${APP_PORT:-8000}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable aiap-protocol.service

echo ""
echo "=== Installation complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit credentials:  sudo nano $APP_DIR/.env"
echo "  2. Start the service:  sudo systemctl start aiap-protocol"
echo "  3. Check status:       sudo systemctl status aiap-protocol"
echo "  4. View logs:          sudo journalctl -u aiap-protocol -f"
echo ""
echo "API will be available at http://<server-ip>:8000"
echo "Docs: http://<server-ip>:8000/docs"
