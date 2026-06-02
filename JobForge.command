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
# Kill any prior debug-port Chrome so new flags take effect
lsof -ti tcp:9222 | xargs kill -9 2>/dev/null || true
sleep 1

# Prefer auto-job-applier profile (already logged into LinkedIn)
AUTO_PROFILE="$HOME/Library/Application Support/Google/Chrome/auto-job-apply-profile"
if [ -d "$AUTO_PROFILE" ]; then
    CHROME_PROFILE="$AUTO_PROFILE"
    info "Using existing LinkedIn session (auto-job-applier profile)"
else
    CHROME_PROFILE="$(pwd)/chrome-profile"
    info "Using fresh Chrome profile (log into LinkedIn when Chrome opens)"
fi

# Unpack UK Visa Sponsor extension (--load-extension needs a directory, not a CRX)
EXT_CRX="$(pwd)/extensions/uk_visa_sponsor.crx"
EXT_UNPACKED="$(pwd)/extensions/uk_visa_unpacked"
if [ -f "$EXT_CRX" ] && [ ! -f "$EXT_UNPACKED/manifest.json" ]; then
    python3 - <<PYEOF 2>/dev/null && info "UK Visa Sponsor extension ready" || info "Could not unpack extension (skipping)"
import struct, zipfile, io, os
data = open("$EXT_CRX", "rb").read()
if data[:4] == b"Cr24" and struct.unpack_from("<I", data, 4)[0] == 3:
    zip_start = 12 + struct.unpack_from("<I", data, 8)[0]
else:
    a, b = struct.unpack_from("<II", data, 8)
    zip_start = 16 + a + b
os.makedirs("$EXT_UNPACKED", exist_ok=True)
zipfile.ZipFile(io.BytesIO(data[zip_start:])).extractall("$EXT_UNPACKED")
PYEOF
fi

info "Starting Chrome (remote debugging mode)..."
CHROME_ARGS=(
    --remote-debugging-port=9222
    --remote-debugging-address=0.0.0.0
    --user-data-dir="$CHROME_PROFILE"
    --no-first-run
    --no-default-browser-check
)
[ -f "$EXT_UNPACKED/manifest.json" ] && CHROME_ARGS+=(--load-extension="$EXT_UNPACKED")

"$CHROME" "${CHROME_ARGS[@]}" &>/dev/null &
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
