#!/bin/bash
cd "$(dirname "$0")"

PORT=7070
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
ok()   { echo -e "  ${GREEN}[OK]${RESET} $1"; }
info() { echo -e "  ${CYAN}[->]${RESET} $1"; }
err()  { echo -e "  ${RED}[X]${RESET}  $1"; }

clear
echo ""
echo -e "  ${BOLD}JobForge${RESET}"
echo "  ────────────────────────────────────────"
echo ""

# ── Check Docker ────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    err "Docker Desktop not found."
    echo "      Download from: https://www.docker.com/products/docker-desktop/"
    echo "      Install it, then double-click this file again."
    echo ""
    read -p "  Press Enter to close..." _; exit 1
fi
if ! docker info &>/dev/null 2>&1; then
    err "Docker Desktop is installed but not running."
    echo "      Open Docker Desktop from your Applications folder."
    echo "      Wait for the whale icon to appear in the menu bar, then try again."
    echo ""
    open -a Docker 2>/dev/null || true
    read -p "  Press Enter to close..." _; exit 1
fi
ok "Docker Desktop"

# ── Check Chrome ────────────────────────────────────────────
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [ ! -f "$CHROME" ]; then
    err "Google Chrome not found."
    echo "      Download from: https://www.google.com/chrome/"
    echo ""
    read -p "  Press Enter to close..." _; exit 1
fi
ok "Chrome"

# ── Launch Chrome with remote debugging ─────────────────────
info "Starting Chrome (remote debugging mode)..."
"$CHROME" \
    --remote-debugging-port=9222 \
    --remote-debugging-address=0.0.0.0 \
    --user-data-dir="$(pwd)/chrome-profile" \
    --no-first-run \
    --no-default-browser-check \
    &>/dev/null &
sleep 2

# ── Build + start Docker container ──────────────────────────
info "Building and starting JobForge (first run takes a few minutes)..."
docker compose up -d --build
if [ $? -ne 0 ]; then
    echo ""
    err "Failed to start JobForge container."
    echo "      Make sure Docker Desktop is running and try again."
    echo ""
    read -p "  Press Enter to close..." _; exit 1
fi
ok "JobForge started"

# ── Open browser ────────────────────────────────────────────
info "Opening JobForge in your browser..."
sleep 4
open "http://localhost:${PORT}"

echo ""
echo "  ────────────────────────────────────────"
echo -e "  ${BOLD}JobForge is running!${RESET}"
echo "  http://localhost:${PORT}"
echo ""
echo "  To stop: docker compose down"
echo "  ────────────────────────────────────────"
echo ""
read -p "  Press Enter to close this window..." _
