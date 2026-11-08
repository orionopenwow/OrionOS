@echo off
setlocal
title Orion Native Desktop Launcher (Tauri v2)

cd /d "%~dp0"

echo [ORION] Bootstrapping and launching Orion Native Desktop App...

if not exist "node_modules\" (
    call npm install --no-audit --no-fund
)
if not exist "apps\daemon\.venv\Scripts\python.exe" (
    call node scripts\setup-daemon.mjs
)

:: Run daemon and Tauri desktop container concurrently
call npm run dev
pause
