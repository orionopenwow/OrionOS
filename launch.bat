@echo off
setlocal enabledelayedexpansion
title Orion Intelligence Hub Launcher

echo ===================================================
echo     ORION: Autonomous Desktop Agent and Intel Hub    
echo ===================================================
echo.

cd /d "%~dp0"

:: 1. Check Node.js
where node >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Node.js is not found on PATH. Please install Node.js v20+.
    pause
    exit /b 1
)

:: 2. Check Python
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not found on PATH. Please install Python 3.11+.
    pause
    exit /b 1
)

:: 3. Check node_modules
if not exist "node_modules\" (
    echo [ORION] Installing npm dependencies...
    call npm install --no-audit --no-fund
) else if not exist "node_modules\@fontsource\nunito\" (
    echo [ORION] Installing new V2 dependencies...
    call npm install --no-audit --no-fund
)

:: 4. Check Python virtual environment
if not exist "apps\daemon\.venv\Scripts\python.exe" (
    echo [ORION] Bootstrapping Python virtual environment...
    call node scripts\setup-daemon.mjs
)

:: 5. Copy .env if not present
if not exist "apps\daemon\.env" (
    if exist "apps\daemon\.env.example" (
        copy "apps\daemon\.env.example" "apps\daemon\.env" >nul
    )
)

echo [ORION] Launching Orion Hub Backend Daemon...
:: run-daemon.mjs relaunches the daemon when a self-evolve patch asks for a hot reload (exit code 75)
start "Orion Daemon" cmd /k "node scripts\run-daemon.mjs"

echo [ORION] Waiting for Daemon initialization...
ping 127.0.0.1 -n 3 >nul

echo [ORION] Launching Desktop User Interface...
start "Orion Desktop UI" cmd /k "cd apps\desktop && npx vite --port 1420 --open"

echo.
echo ===================================================
echo     Orion is now running!
echo     - Desktop UI: http://localhost:1420
echo     - WebSocket:  ws://localhost:9120
echo ===================================================
echo.
