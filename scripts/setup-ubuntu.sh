#!/usr/bin/env bash
# SENTINEL — Ubuntu Environment Setup
# Idempotent: safe to run multiple times.
# Tested on Ubuntu 22.04 and 24.04.
#
# Usage:
#   bash scripts/setup-ubuntu.sh
#   # or
#   make setup

set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }
step()  { echo -e "\n${BOLD}── $* ──${NC}"; }

# ── Pre-flight ────────────────────────────────────────────────────────────────

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f "pyproject.toml" ]]; then
    err "Not in the SENTINEL repo root. Aborting."
    exit 1
fi

if ! grep -qi 'ubuntu\|debian' /etc/os-release 2>/dev/null; then
    warn "This script targets Ubuntu/Debian. Other distros may need manual adjustments."
fi

UBUNTU_VERSION=$(grep VERSION_ID /etc/os-release 2>/dev/null | tr -d '"' | cut -d= -f2 || echo "unknown")
info "Detected OS version: ${UBUNTU_VERSION}"
info "Repo root: ${REPO_ROOT}"

SUMMARY=()

# ── 1. System packages ───────────────────────────────────────────────────────

step "System packages"

SYSTEM_PKGS=(
    python3
    python3-pip
    python3-venv
    python3-dev
    build-essential
    libuhd-dev
    uhd-host
    python3-uhd
    libusb-1.0-0-dev
    git
    curl
)

MISSING_PKGS=()
for pkg in "${SYSTEM_PKGS[@]}"; do
    if ! dpkg -l "$pkg" 2>/dev/null | grep -q '^ii'; then
        MISSING_PKGS+=("$pkg")
    fi
done

if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
    info "Installing: ${MISSING_PKGS[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y -qq "${MISSING_PKGS[@]}"
    SUMMARY+=("Installed system packages: ${MISSING_PKGS[*]}")
    ok "System packages installed."
else
    ok "All system packages already installed."
fi

# ── 2. Node.js 20 LTS ────────────────────────────────────────────────────────

step "Node.js 20 LTS"

NODE_MAJOR=20

install_node() {
    info "Installing Node.js ${NODE_MAJOR}.x via NodeSource..."
    # NodeSource setup script (works on both 22.04 and 24.04)
    curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | sudo -E bash -
    sudo apt-get install -y -qq nodejs
    SUMMARY+=("Installed Node.js $(node --version)")
}

if command -v node &>/dev/null; then
    CURRENT_NODE=$(node --version | sed 's/v//' | cut -d. -f1)
    if [[ "$CURRENT_NODE" -ge 18 ]]; then
        ok "Node.js $(node --version) already installed (>= 18 required)."
    else
        warn "Node.js $(node --version) is too old (need >= 18). Upgrading..."
        install_node
    fi
else
    install_node
fi

ok "Node $(node --version), npm $(npm --version)"

# ── 3. Python venv ────────────────────────────────────────────────────────────

step "Python virtual environment"

VENV_DIR="${REPO_ROOT}/.venv"

if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating venv with --system-site-packages (required for UHD bindings)..."
    python3 -m venv --system-site-packages "$VENV_DIR"
    SUMMARY+=("Created Python venv at .venv/")
else
    ok "Venv already exists at .venv/"
fi

# Always upgrade pip and install/update deps
info "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -e ".[dev,hardware]" -q
ok "Python deps installed."
SUMMARY+=("Installed Python deps: sentinel[dev,hardware]")

# ── 4. Radar app (Node) ──────────────────────────────────────────────────────

step "Radar app (Express + Socket.IO)"

if [[ -d "radar-app" ]]; then
    cd radar-app
    if [[ ! -d "node_modules" ]]; then
        info "Installing npm dependencies..."
        npm install --silent
        SUMMARY+=("Installed radar-app npm dependencies")
    else
        ok "node_modules already exists. Run 'cd radar-app && npm install' to update."
    fi
    cd "$REPO_ROOT"
else
    warn "radar-app/ directory not found. Skipping npm install."
fi

# ── 5. UHD setup (B210 FPGA images + udev) ───────────────────────────────────

step "UHD / B210 setup"

# Download FPGA images for B2xx devices
if command -v uhd_images_downloader &>/dev/null; then
    # Check if images already exist
    UHD_IMG_DIR=$(python3 -c "import subprocess; r=subprocess.run(['uhd_images_downloader','--list-targets'],capture_output=True,text=True); print('ok')" 2>/dev/null || true)

    info "Downloading B2xx FPGA images (skips if already present)..."
    if sudo uhd_images_downloader --types b2xx 2>/dev/null; then
        ok "B2xx FPGA images ready."
        SUMMARY+=("Downloaded B2xx FPGA images")
    else
        warn "uhd_images_downloader failed — B210 may not be usable until images are installed."
        warn "Try: sudo uhd_images_downloader --types b2xx"
    fi
else
    warn "uhd_images_downloader not found. Install uhd-host first."
fi

# Install udev rules for USB SDR devices
# The uhd-host package installs rules to /usr/lib/udev/rules.d/ automatically.
# We check for that first, then fall back to manual copy if needed.
UHD_RULES_SYSTEM="/usr/lib/udev/rules.d/60-uhd-host.rules"
UHD_RULES_LOCAL="/etc/udev/rules.d/uhd-usrp.rules"

if [[ -f "$UHD_RULES_SYSTEM" ]] || [[ -f "$UHD_RULES_LOCAL" ]]; then
    ok "UHD udev rules already installed."
else
    # Try to find and install rules manually
    UHD_RULES_SRC=""
    for candidate in \
        /usr/lib/uhd/utils/uhd-usrp.rules \
        /usr/share/uhd/utils/uhd-usrp.rules; do
        if [[ -f "$candidate" ]]; then
            UHD_RULES_SRC="$candidate"
            break
        fi
    done

    if [[ -n "$UHD_RULES_SRC" ]]; then
        info "Installing UHD udev rules from $UHD_RULES_SRC..."
        sudo cp "$UHD_RULES_SRC" "$UHD_RULES_LOCAL"
        sudo udevadm control --reload-rules
        sudo udevadm trigger
        SUMMARY+=("Installed UHD udev rules")
        ok "udev rules installed and reloaded."
    else
        warn "UHD udev rules source not found. B210 may need manual permission setup."
    fi
fi

# ── 6. Serial access (dialout group) ─────────────────────────────────────────

step "Serial port access"

if groups "$USER" | grep -q '\bdialout\b'; then
    ok "User '$USER' already in dialout group."
else
    info "Adding '$USER' to dialout group (for antenna servo control)..."
    sudo usermod -aG dialout "$USER"
    SUMMARY+=("Added $USER to dialout group (log out/in to take effect)")
    warn "You must log out and back in for group change to take effect."
fi

# ── 7. Project directories ───────────────────────────────────────────────────

step "Project directories"

DIRS=(
    "data/samples"
    "data/signatures"
    "logs"
    "docs/test-logs"
)

for d in "${DIRS[@]}"; do
    if [[ ! -d "$d" ]]; then
        mkdir -p "$d"
        info "Created $d/"
    fi
done
ok "All project directories exist."

# ── 8. Verification ──────────────────────────────────────────────────────────

step "Verification"

PASS=true

# Python imports
info "Checking Python imports..."
if "$VENV_DIR/bin/python" -c "import numpy, scipy, yaml, serial" 2>/dev/null; then
    ok "Core Python packages importable."
else
    err "Some Python packages failed to import."
    PASS=false
fi

# UHD import (non-fatal — might not be on dev machine)
if "$VENV_DIR/bin/python" -c "import uhd" 2>/dev/null; then
    ok "UHD Python bindings available."
else
    warn "UHD Python bindings not importable. Expected if python3-uhd is not installed."
    warn "The venv uses --system-site-packages, so install python3-uhd system-wide."
fi

# Run tests
info "Running pytest..."
if "$VENV_DIR/bin/python" -m pytest --tb=short -q 2>/dev/null; then
    ok "Tests passed."
else
    warn "Some tests failed. Check output above."
    PASS=false
fi

# ── Summary ───────────────────────────────────────────────────────────────────

step "Setup complete"

echo ""
if [[ ${#SUMMARY[@]} -gt 0 ]]; then
    info "Actions taken:"
    for s in "${SUMMARY[@]}"; do
        echo -e "  ${GREEN}+${NC} $s"
    done
else
    ok "Everything was already configured. No changes made."
fi

echo ""
info "${BOLD}Next steps:${NC}"
echo "  1. Activate the venv:  source .venv/bin/activate"
echo "  2. Run tests:          make test"
echo "  3. Start radar app:    make run-radar"
echo "  4. Connect B210 and:   uhd_usrp_probe"
echo ""

if groups "$USER" | grep -q '\bdialout\b' 2>/dev/null; then
    true
else
    warn "Remember to log out and back in for dialout group access."
fi

if [[ "$PASS" == true ]]; then
    ok "${BOLD}SENTINEL environment is ready.${NC}"
else
    warn "Setup completed with warnings. Review output above."
fi
