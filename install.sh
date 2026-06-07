#!/usr/bin/env bash
set -euo pipefail

# mcradio installer — sets up the Minecraft DFPWM radio server on Ubuntu
# Usage: curl -sSL https://raw.githubusercontent.com/vakermit/mcradio/main/install.sh | bash

INSTALL_DIR="/opt/icecast"
REPO_URL="https://github.com/vakermit/mcradio.git"
SERVICE_USER="icecast"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()  { echo -e "\033[1;34m==>\033[0m $*"; }
ok()    { echo -e "\033[1;32m  ✓\033[0m $*"; }
warn()  { echo -e "\033[1;33m  !\033[0m $*"; }
fatal() { echo -e "\033[1;31mERR\033[0m $*" >&2; exit 1; }

require_root() {
    if [[ $EUID -ne 0 ]]; then
        fatal "This installer must be run as root (or with sudo)."
    fi
}

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

require_root

info "Checking dependencies..."

command -v git >/dev/null 2>&1 || fatal "git is required. Install with: apt install git"
command -v python3 >/dev/null 2>&1 || fatal "python3 is required. Install with: apt install python3"

if ! command -v ffmpeg >/dev/null 2>&1; then
    fatal "ffmpeg is required (with DFPWM support). Install with: apt install ffmpeg"
fi

# Check ffmpeg has DFPWM muxer
if ! ffmpeg -muxers 2>/dev/null | grep -q dfpwm; then
    warn "ffmpeg may not have DFPWM support. Version 5.1+ required."
    warn "Continuing anyway — transcode may fail later."
fi

if ! command -v yt-dlp >/dev/null 2>&1; then
    warn "yt-dlp not found. Install with: pip install yt-dlp"
    warn "The 'mcradio download' command will not work without it."
fi

ok "Dependencies checked"

# ---------------------------------------------------------------------------
# System user
# ---------------------------------------------------------------------------

info "Setting up system user..."

if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --shell /usr/sbin/nologin --home-dir "$INSTALL_DIR" "$SERVICE_USER"
    ok "Created system user: $SERVICE_USER"
else
    ok "User already exists: $SERVICE_USER"
fi

# ---------------------------------------------------------------------------
# Clone or update repository
# ---------------------------------------------------------------------------

info "Installing to $INSTALL_DIR..."

if [[ -d "$INSTALL_DIR/.git" ]]; then
    cd "$INSTALL_DIR"
    git pull --ff-only
    ok "Updated existing installation"
else
    if [[ -d "$INSTALL_DIR" ]]; then
        warn "$INSTALL_DIR exists but is not a git repo — backing up"
        mv "$INSTALL_DIR" "${INSTALL_DIR}.bak.$(date +%s)"
    fi
    git clone "$REPO_URL" "$INSTALL_DIR"
    ok "Cloned repository"
fi

cd "$INSTALL_DIR"

# ---------------------------------------------------------------------------
# Runtime directories
# ---------------------------------------------------------------------------

info "Creating runtime directories..."

mkdir -p "$INSTALL_DIR/music/raw"
mkdir -p "$INSTALL_DIR/music/dfpwm"
mkdir -p "$INSTALL_DIR/music/metadata"
mkdir -p "$INSTALL_DIR/log"
mkdir -p "$INSTALL_DIR/run"

ok "Directories created"

# ---------------------------------------------------------------------------
# Python virtual environment + package install
# ---------------------------------------------------------------------------

info "Setting up Python environment..."

if command -v uv >/dev/null 2>&1; then
    # Prefer uv (fast)
    uv venv "$INSTALL_DIR/.venv"
    source "$INSTALL_DIR/.venv/bin/activate"
    uv pip install -e "$INSTALL_DIR"
    ok "Installed via uv"
else
    # Fallback to stdlib venv + pip
    python3 -m venv "$INSTALL_DIR/.venv"
    source "$INSTALL_DIR/.venv/bin/activate"
    pip install --upgrade pip -q
    pip install -e "$INSTALL_DIR"
    ok "Installed via pip"
fi

deactivate 2>/dev/null || true

# ---------------------------------------------------------------------------
# Symlink CLI
# ---------------------------------------------------------------------------

info "Creating CLI symlink..."

ln -sf "$INSTALL_DIR/.venv/bin/mcradio" /usr/local/bin/mcradio
ok "/usr/local/bin/mcradio → $INSTALL_DIR/.venv/bin/mcradio"

# ---------------------------------------------------------------------------
# Systemd service
# ---------------------------------------------------------------------------

info "Installing systemd service..."

cp "$INSTALL_DIR/systemd/mcradio.service" /etc/systemd/system/mcradio.service
systemctl daemon-reload
systemctl enable mcradio

ok "Service installed and enabled"

# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

info "Setting ownership..."

chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
ok "chown -R $SERVICE_USER:$SERVICE_USER $INSTALL_DIR"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo ""
info "Installation complete!"
echo ""
echo "  Next steps:"
echo "    1. Edit /opt/icecast/stations.yaml to configure your stations"
echo "    2. Download music:  mcradio download lofi \"ytsearch10:lo-fi hip hop\""
echo "    3. Transcode:       mcradio transcode lofi"
echo "    4. Start server:    mcradio start"
echo "    5. Check status:    mcradio status"
echo ""
echo "  In Minecraft (CC:Tweaked computer):"
echo "    wget run http://<server-ip>:5309/client/installer.lua"
echo ""
