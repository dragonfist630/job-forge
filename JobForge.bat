@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title JobForge

cls
echo.
echo  ==========================================
echo   ^|    JobForge                          ^|
echo  ==========================================
echo.

REM ── Check Python ──────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo   [!] Python not found. Installing via winget...
    winget install Python.Python.3.11 -e --silent
    if errorlevel 1 (
        echo   [X] Could not install Python automatically.
        echo       Please download from https://www.python.org/downloads/
        pause & exit /b 1
    )
    call refreshenv 2>nul
    where python >nul 2>&1
    if errorlevel 1 (
        echo   [!] Please restart this file after Python finishes installing.
        pause & exit /b 1
    )
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo   [OK] %PYVER%

REM ── Require Python 3.10+ ──────────────────────────────────
python -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [X] JobForge needs Python 3.10 or newer.
    echo       You have: %PYVER%
    echo.
    echo       Download Python 3.11 from:
    echo       https://www.python.org/downloads/
    echo.
    echo       When installing, tick "Add Python to PATH".
    echo       Then close and re-open this file.
    echo.
    pause & exit /b 1
)

REM ── Check Node.js ─────────────────────────────────────────
where node >nul 2>&1
if errorlevel 1 (
    echo   [!] Node.js not found. Installing via winget...
    winget install OpenJS.NodeJS.LTS -e --silent
    call refreshenv 2>nul
    where node >nul 2>&1
    if errorlevel 1 (
        echo   [!] Please restart this file after Node.js finishes installing.
        pause & exit /b 1
    )
)
for /f "tokens=*" %%v in ('node --version 2^>^&1') do echo   [OK] Node.js %%v

REM ── Install Python packages if needed ─────────────────────
python -c "import flask, yaml, selenium, webdriver_manager" >nul 2>&1
if errorlevel 1 (
    echo   [->] Installing Python packages (one-time setup)...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo   [X] Failed to install packages. Check your internet connection.
        pause & exit /b 1
    )
)
echo   [OK] Python packages

REM ── Install Playwright + Chromium if needed ───────────────
set CAREER_OPS=%~dp0..\career-ops
if exist "%CAREER_OPS%\package.json" (
    if not exist "%CAREER_OPS%\node_modules" (
        echo   [->] Installing Playwright (downloads a browser, one-time)...
        pushd "%CAREER_OPS%"
        call npm install --silent 2>nul
        call npx playwright install chromium 2>nul
        popd
    )
    echo   [OK] Playwright
)

REM ── Start JobForge ─────────────────────────────────────────
echo.
echo  ==========================================
echo   Starting JobForge...
echo   Browser will open at http://localhost:7070
echo   Close this window to stop.
echo  ==========================================
echo.

python main.py
if errorlevel 1 (
    echo.
    echo   [X] JobForge crashed. Error shown above.
    echo       Screenshot this window and share with support.
)

echo.
echo   JobForge stopped.
pause
