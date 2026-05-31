#!/bin/bash
# JobForge — macOS installer
# Run once: bash setup/install_mac.sh

set -e
echo ""
echo "  ⚙  JobForge — macOS Setup"
echo "  ─────────────────────────"

# 1. Homebrew
if ! command -v brew &>/dev/null; then
  echo "  → Installing Homebrew..."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
echo "  ✓ Homebrew"

# 2. Node.js
if ! command -v node &>/dev/null; then
  echo "  → Installing Node.js..."
  brew install node
fi
echo "  ✓ Node.js $(node --version)"

# 3. Python 3.11+
if ! command -v python3 &>/dev/null; then
  echo "  → Installing Python..."
  brew install python
fi
echo "  ✓ Python $(python3 --version)"

# 4. Pip dependencies
echo "  → Installing Python packages..."
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
pip3 install -r "$SCRIPT_DIR/requirements.txt" -q
echo "  ✓ Python packages"

# 5. Playwright (for career-ops PDF generation)
echo "  → Installing Playwright browsers..."
cd "$SCRIPT_DIR/../career-ops" 2>/dev/null || true
if [ -f "package.json" ]; then
  npm install --silent 2>/dev/null || true
  npx playwright install chromium 2>/dev/null || true
fi
echo "  ✓ Playwright"

echo ""
echo "  ✅ Setup complete!"
echo "  → Run: bash start.sh"
echo ""
