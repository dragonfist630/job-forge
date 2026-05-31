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
    REM Refresh PATH
    call refreshenv 2>nul
    where python >nul 2>&1
    if errorlevel 1 (
        echo   [!] Please restart this file after Python finishes installing.
        pause & exit /b 1
    )
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo   [OK] %%v

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
python -c "import flask, yaml, anthropic, selenium, webdriver_manager" >nul 2>&1
if errorlevel 1 (
    echo   [->] Installing Python packages (one-time setup)...
    pip install -r requirements.txt -q
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

echo.
echo   [!] JobForge stopped.
pause
