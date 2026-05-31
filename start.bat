@echo off
cd /d "%~dp0"
echo.
echo   Starting JobForge...
echo   Open: http://localhost:7070
echo   Press Ctrl+C to stop
echo.
python main.py
pause
