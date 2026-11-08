@echo off
rem One click to test Orion: starts Orion if it isn't running, opens the Test Center in the Orion window and
rem runs the automatic checks. Then follow the "Next" card. (docs\TESTING.md)
title Test Orion
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo Node.js is not installed. Get the LTS version from https://nodejs.org and try again.
  pause
  exit /b 1
)

rem Same start as "Start Orion.cmd" (sets things up the first time; does nothing if Orion is already running).
call "Start Orion.cmd"
if errorlevel 1 exit /b 1

node scripts\test-orion.mjs
if errorlevel 1 (
  echo.
  echo The Test Center did not open. Scroll up for why, then press any key.
  pause >nul
  exit /b 1
)
timeout /t 4 >nul
exit /b 0
