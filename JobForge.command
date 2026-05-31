#!/bin/bash
# ─────────────────────────────────────────────────────────
#  JobForge — macOS launcher (double-click to run)
#
#  First run:  installs everything automatically
#  Every run:  starts the app and opens your browser
# ─────────────────────────────────────────────────────────

# Go to the folder this script lives in (works wherever user puts the folder)
cd "$(dirname "$0")"

PORT=7070

# ── Colour helpers ────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
ok()   { echo -e "  ${GREEN}✓${RESET}  $1"; }
info() { echo -e "  ${CYAN}→${RESET}  $1"; }
warn() { echo -e "  ${YELLOW}!${RESET}  $1"; }
err()  { echo -e "  ${RED}✗${RESET}  $1"; }
hr()   { echo -e "${CYAN}──────────────────────────────────────────${RESET}"; }

clear
echo ""
hr
echo -e "  ${BOLD}⚙  JobForge${RESET}"
hr
echo ""

# ── Check Python ──────────────────────────────────────────
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(sys.version_info.major, sys.version_info.minor)")
        MAJOR=$(echo $VER | cut -d' ' -f1)
        MINOR=$(echo $VER | cut -d' ' -f2)
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 9 ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    warn "Python 3.9+ not found — installing via Homebrew..."
    echo ""
    # Install Homebrew if missing
    if ! command -v brew &>/dev/null; then
        info "Installing Homebrew (this takes a few minutes the first time)..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Add brew to PATH for Apple Silicon
        if [ -f /opt/homebrew/bin/brew ]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        fi
    fi
    info "Installing Python..."
    brew install python
    PYTHON="python3"
fi
ok "Python ($($PYTHON --version 2>&1))"

# ── Check Node.js ─────────────────────────────────────────
if ! command -v node &>/dev/null; then
    warn "Node.js not found — installing via Homebrew..."
    if ! command -v brew &>/dev/null; then
        info "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        if [ -f /opt/homebrew/bin/brew ]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        fi
    fi
    info "Installing Node.js..."
    brew install node
fi
ok "Node.js ($(node --version))"

# ── Install Python packages if needed ─────────────────────
NEEDS_INSTALL=false
for pkg in flask yaml anthropic selenium webdriver_manager; do
    if ! $PYTHON -c "import $pkg" &>/dev/null 2>&1; then
        NEEDS_INSTALL=true
        break
    fi
done

if [ "$NEEDS_INSTALL" = true ]; then
    info "Installing Python packages (one-time setup)..."
    $PYTHON -m pip install -r requirements.txt -q \
        || $PYTHON -m pip install -r requirements.txt -q --break-system-packages
fi
ok "Python packages"

# ── Install Playwright + Chromium if needed ───────────────
CAREER_OPS_DIR="$(cd "$(dirname "$0")/../career-ops" 2>/dev/null && pwd)" || true

if [ -f "$CAREER_OPS_DIR/package.json" ]; then
    if [ ! -d "$CAREER_OPS_DIR/node_modules" ]; then
        info "Installing Playwright (one-time, downloads a browser)..."
        cd "$CAREER_OPS_DIR"
        npm install --silent 2>/dev/null
        npx playwright install chromium 2>/dev/null
        cd "$(dirname "$0")"
    fi
    ok "Playwright"
fi

# ── Start JobForge ────────────────────────────────────────
echo ""
hr
echo -e "  ${BOLD}Starting JobForge...${RESET}"
echo -e "  Browser will open at ${CYAN}http://localhost:${PORT}${RESET}"
echo -e "  Press ${BOLD}Ctrl+C${RESET} to stop"
hr
echo ""

$PYTHON main.py

# Keep terminal open if app crashes so user can read the error
echo ""
err "JobForge stopped."
echo ""
read -p "  Press Enter to close this window..." _
