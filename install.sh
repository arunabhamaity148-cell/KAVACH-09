#!/usr/bin/env bash
# ============================================================
#  KAVACH-09 — One-click Installer for Linux VPS
#  CoinDCX USDT Futures · Signal Intelligence Bot
# ============================================================
#  Usage:
#    chmod +x install.sh
#    sudo ./install.sh
#
#  What it does:
#    1. Creates /opt/kavach09 directory
#    2. Creates 'kavach' system user
#    3. Copies project files
#    4. Creates Python venv + installs deps
#    5. Sets up .env from .env.example
#    6. Installs systemd service
#    7. Enables + starts the service
#
#  After install:
#    sudo nano /opt/kavach09/.env   (fill API keys)
#    sudo systemctl restart kavach09
#    sudo journalctl -u kavach09 -f
# ============================================================

set -euo pipefail

# ─── Colors ───────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERR]${NC}   $*"; exit 1; }
step()  { echo -e "\n${CYAN}━━━ $* ━━━${NC}"; }

# ─── Pre-flight checks ────────────────────────────────────────
step "Pre-flight checks"

if [[ $EUID -ne 0 ]]; then
    error "Run as root: sudo ./install.sh"
fi

if [[ ! -f main.py ]]; then
    error "Run from project root (where main.py lives): cd kavach09 && sudo ./install.sh"
fi

PYTHON_BIN=""
for candidate in python3.11 python3.10 python3.9 python3; do
    if command -v "$candidate" &> /dev/null; then
        PYTHON_BIN="$candidate"
        break
    fi
done
[[ -z "$PYTHON_BIN" ]] && error "Python 3 not found. Install: sudo apt install python3 python3-venv python3-pip"
info "Using Python: $PYTHON_BIN ($($PYTHON_BIN --version))"

# ─── Config ───────────────────────────────────────────────────
INSTALL_DIR="/opt/kavach09"
SERVICE_USER="kavach"
SERVICE_GROUP="kavach"
SERVICE_NAME="kavach09"

# ─── Step 1: Create user ──────────────────────────────────────
step "Create system user: $SERVICE_USER"
if id "$SERVICE_USER" &>/dev/null; then
    info "User '$SERVICE_USER' already exists — skipping"
else
    useradd --system --no-create-home --shell /bin/false "$SERVICE_USER"
    info "Created user '$SERVICE_USER'"
fi

# ─── Step 2: Install dir ──────────────────────────────────────
step "Setup install directory: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
# Preserve .env if already exists
if [[ -f "$INSTALL_DIR/.env" ]]; then
    cp "$INSTALL_DIR/.env" /tmp/kavach09.env.bak
    info "Existing .env backed up to /tmp/kavach09.env.bak"
fi
# Copy all project files
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '*.db' \
      --exclude '.env' --exclude 'venv' --exclude '.git' \
      ./ "$INSTALL_DIR/"
info "Project files copied to $INSTALL_DIR"

# ─── Step 3: Virtualenv + deps ────────────────────────────────
step "Create Python virtualenv + install deps"
if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    sudo -u "$SERVICE_USER" "$PYTHON_BIN" -m venv "$INSTALL_DIR/venv"
    info "Virtualenv created"
fi
info "Installing dependencies (this may take a minute)..."
sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
sudo -u "$SERVICE_USER" "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet
info "Dependencies installed"

# ─── Step 4: .env file ────────────────────────────────────────
step "Configure .env"
if [[ -f /tmp/kavach09.env.bak ]]; then
    cp /tmp/kavach09.env.bak "$INSTALL_DIR/.env"
    info "Restored previous .env"
elif [[ ! -f "$INSTALL_DIR/.env" ]]; then
    cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
    info "Created .env from .env.example"
fi
chown "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR/.env"
chmod 600 "$INSTALL_DIR/.env"
warn "Edit .env to fill API keys: sudo nano $INSTALL_DIR/.env"

# ─── Step 5: DB directory (if KAVACH_DB_PATH points elsewhere) ─
mkdir -p /var/lib/kavach09
chown "$SERVICE_USER:$SERVICE_GROUP" /var/lib/kavach09

# ─── Step 6: Permissions ──────────────────────────────────────
step "Set ownership + permissions"
chown -R "$SERVICE_USER:$SERVICE_GROUP" "$INSTALL_DIR"
find "$INSTALL_DIR" -type d -exec chmod 755 {} \;
find "$INSTALL_DIR" -type f -exec chmod 644 {} \;
chmod 600 "$INSTALL_DIR/.env"
info "Permissions set"

# ─── Step 7: systemd service ──────────────────────────────────
step "Install systemd service"
cp "$INSTALL_DIR/kavach09.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
info "Service registered: $SERVICE_NAME"

# ─── Step 8: Enable + start ───────────────────────────────────
step "Enable + start service"
systemctl enable "$SERVICE_NAME"
# Don't auto-start if .env still has empty keys — let user fill them first
ENV_OK=$(grep -E "^TELEGRAM_BOT_TOKEN=.+" "$INSTALL_DIR/.env" | wc -l)
if [[ "$ENV_OK" -ge 1 ]]; then
    systemctl restart "$SERVICE_NAME"
    info "Service started"
else
    warn ".env has empty TELEGRAM_BOT_TOKEN — NOT starting service"
    warn "After filling keys, run: sudo systemctl start $SERVICE_NAME"
fi

# ─── Done ─────────────────────────────────────────────────────
step "Installation Complete"

cat <<EOF

 ${GREEN}╔══════════════════════════════════════════════════╗${NC}
 ${GREEN}║  ⚔️  KAVACH-09 Installed Successfully            ║${NC}
 ${GREEN}╚══════════════════════════════════════════════════╝${NC}

 ${CYAN}Next steps:${NC}

1. Edit .env and fill API keys:
   ${BLUE}sudo nano $INSTALL_DIR/.env${NC}

   Required:
     TELEGRAM_BOT_TOKEN  (from @BotFather)
     TELEGRAM_CHAT_ID    (from @userinfobot)
     GROQ_API_KEY        (from https://console.groq.com/keys)

2. (Re)start the service:
   ${BLUE}sudo systemctl restart $SERVICE_NAME${NC}

3. Check status:
   ${BLUE}sudo systemctl status $SERVICE_NAME${NC}

4. Watch live logs:
   ${BLUE}sudo journalctl -u $SERVICE_NAME -f${NC}

5. Talk to your bot on Telegram — send /start

 ${CYAN}Uninstall:${NC}
   sudo systemctl stop $SERVICE_NAME
   sudo systemctl disable $SERVICE_NAME
   sudo rm /etc/systemd/system/${SERVICE_NAME}.service
   sudo rm -rf $INSTALL_DIR
   sudo userdel $SERVICE_USER

EOF