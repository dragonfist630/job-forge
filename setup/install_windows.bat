@echo off
REM JobForge — Windows installer
REM Run once: setup\install_windows.bat

echo.
echo   JobForge - Windows Setup
echo   ─────────────────────────

REM 1. Check winget
where winget >nul 2>&1
if errorlevel 1 (
    echo   [!] winget not found. Install it from the Microsoft Store.
    pause
    exit /b 1
)
echo   [OK] winget found

REM 2. Node.js
where node >nul 2>&1
if errorlevel 1 (
    echo   Installing Node.js...
    winget install OpenJS.NodeJS.LTS -e --silent
    refreshenv
)
echo   [OK] Node.js

REM 3. Python
where python >nul 2>&1
if errorlevel 1 (
    echo   Installing Python...
    winget install Python.Python.3.11 -e --silent
    refreshenv
)
echo   [OK] Python

REM 4. Pip packages
set SCRIPT_DIR=%~dp0..
echo   Installing Python packages...
pip install -r "%SCRIPT_DIR%\requirements.txt" -q
echo   [OK] Python packages

REM 5. Playwright
set CAREER_OPS=%SCRIPT_DIR%\..\career-ops
if exist "%CAREER_OPS%\package.json" (
    echo   Installing Playwright browsers...
    pushd "%CAREER_OPS%"
    call npm install --silent 2>nul
    call npx playwright install chromium 2>nul
    popd
)
echo   [OK] Playwright

echo.
echo   Setup complete!
echo   Run: start.bat
echo.
pause
