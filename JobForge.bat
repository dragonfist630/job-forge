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

REM ── Check Docker ───────────────────────────────────────────
where docker >nul 2>&1
if errorlevel 1 (
    echo   [X] Docker Desktop not found.
    echo       Download from: https://www.docker.com/products/docker-desktop/
    echo       Install it, then double-click this file again.
    echo.
    pause & exit /b 1
)
docker info >nul 2>&1
if errorlevel 1 (
    echo   [X] Docker Desktop is installed but not running.
    echo       Open Docker Desktop from your Start Menu, wait for it to
    echo       fully start ^(whale icon in taskbar^), then try again.
    echo.
    pause & exit /b 1
)
echo   [OK] Docker Desktop

REM ── Find Chrome ────────────────────────────────────────────
set "CHROME_EXE="
for %%P in (
    "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
    "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
    "%LocalAppData%\Google\Chrome\Application\chrome.exe"
) do (
    if exist %%P (
        if "!CHROME_EXE!"=="" set "CHROME_EXE=%%~P"
    )
)
if "!CHROME_EXE!"=="" (
    echo   [X] Google Chrome not found.
    echo       Download from: https://www.google.com/chrome/
    echo.
    pause & exit /b 1
)
echo   [OK] Chrome

REM ── Launch Chrome with remote debugging ────────────────────
echo   [->] Starting Chrome...
start "" "!CHROME_EXE!" --remote-debugging-port=9222 --user-data-dir="%~dp0chrome-profile" --no-first-run --no-default-browser-check
timeout /t 3 /nobreak >nul

REM ── Build + start Docker container ─────────────────────────
echo   [->] Building and starting JobForge ^(first run takes a few minutes^)...
docker compose up -d --build
if errorlevel 1 (
    echo.
    echo   [X] Failed to start JobForge container.
    echo       Make sure Docker Desktop is running and try again.
    echo.
    pause & exit /b 1
)
echo   [OK] JobForge started

REM ── Wait for Flask then open browser ───────────────────────
echo   [->] Waiting for JobForge to be ready...
timeout /t 5 /nobreak >nul
start http://localhost:7070

echo.
echo  ==========================================
echo   JobForge is running!
echo   Open: http://localhost:7070
echo.
echo   To STOP JobForge:
echo     docker compose down
echo  ==========================================
echo.
pause
