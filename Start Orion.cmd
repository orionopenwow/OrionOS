@echo off
rem One-click Orion: double-click this (or the "Orion" desktop shortcut it creates).
title Orion launcher
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo Node.js is not installed. Get the LTS version from https://nodejs.org and try again.
  pause
  exit /b 1
)

node scripts\launch.mjs
if "%errorlevel%"=="3" (
  timeout /t 5 >nul
  exit /b 0
)
if not "%errorlevel%"=="0" (
  echo.
  echo Orion could not finish setting up. Scroll up for the error, then press any key.
  pause >nul
  exit /b 1
)

rem Run Orion in its own minimized window. Closing that window quits Orion.
rem The desktop shell starts (and stops) the Python brain itself. Output goes to logs\orion-dev.log.
echo Starting Orion... the first launch compiles the desktop window and can take several minutes.
start "Orion (close this window to quit Orion)" /min cmd /c "npm run dev:desktop > logs\orion-dev.log 2>&1"
timeout /t 4 >nul
exit /b 0
