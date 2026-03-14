#!/bin/bash
##############################################################################
# Первоначальная настройка VPS (Ubuntu 22.04 / 24.04)
#
# Запуск от root:
#   curl -sSL <url>/bootstrap-vps.sh | bash
#   или
#   bash bootstrap-vps.sh
#
# Что делает:
#   1. Обновляет систему
#   2. Устанавливает Docker + Docker Compose
#   3. Настраивает файрвол (UFW)
#   4. Создаёт рабочую директорию /opt/services
#   5. Настраивает swap (если < 2GB RAM)
##############################################################################

set -e

echo "============================================"
echo "  VPS Bootstrap — Multi-service Setup"
echo "============================================"
echo ""

# ── 1. Обновление системы ──────────────────────────────────────────────────
echo "[1/5] Обновление системы..."
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq curl git wget unzip htop

# ── 2. Docker + Compose ───────────────────────────────────────────────────
echo "[2/5] Установка Docker..."
if command -v docker &>/dev/null; then
    echo "  Docker уже установлен: $(docker --version)"
else
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
    echo "  Docker установлен: $(docker --version)"
fi

# Docker Compose (plugin) — уже включён в docker.io из get.docker.com
if docker compose version &>/dev/null; then
    echo "  Docker Compose: $(docker compose version --short)"
else
    echo "  WARN: docker compose plugin не найден, устанавливаю..."
    apt-get install -y -qq docker-compose-plugin
fi

# ── 3. Файрвол (UFW) ─────────────────────────────────────────────────────
echo "[3/5] Настройка файрвола..."
apt-get install -y -qq ufw

ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp    # HTTP (Nginx)
ufw allow 443/tcp   # HTTPS (на будущее)

# Не блокировать SSH при включении
ufw --force enable
echo "  UFW включён. Открыты порты: SSH, 80, 443"

# ── 4. Рабочая директория ────────────────────────────────────────────────
echo "[4/5] Создание рабочей директории..."
SERVICES_DIR="/opt/services"
mkdir -p "$SERVICES_DIR"
echo "  Директория: $SERVICES_DIR"

# ── 5. Swap (если мало RAM) ──────────────────────────────────────────────
echo "[5/5] Проверка swap..."
TOTAL_RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
if [ "$TOTAL_RAM_MB" -lt 2048 ] && [ ! -f /swapfile ]; then
    echo "  RAM: ${TOTAL_RAM_MB}MB — создаю 2GB swap..."
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "  Swap 2GB создан"
else
    echo "  RAM: ${TOTAL_RAM_MB}MB — swap не требуется"
fi

echo ""
echo "============================================"
echo "  VPS готов!"
echo "============================================"
echo ""
echo "Следующие шаги:"
echo ""
echo "  1. Склонировать проект:"
echo "     cd $SERVICES_DIR"
echo "     git clone <repo-url> aiap-protocol"
echo ""
echo "  2. Настроить credentials:"
echo "     cd aiap-protocol/deploy"
echo "     cp envs/aiap-protocol.env.example envs/aiap-protocol.env"
echo "     nano envs/aiap-protocol.env"
echo ""
echo "  3. Запустить всё:"
echo "     docker compose up -d --build"
echo ""
echo "  4. Проверить:"
echo "     curl http://localhost/aiap/health"
echo ""
