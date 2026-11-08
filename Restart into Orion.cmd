@echo off
rem Restarts this PC into OrionOS once (Windows stays the normal choice).
title Restart into Orion
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0os\windows\Restart-IntoOrion.ps1" %*
