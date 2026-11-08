@echo off
rem Puts the Orion update Claude delivered (in the _incoming folder) into this folder. Checks first, backs up what it replaces.
title Apply Orion update
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0os\windows\Apply-OrionUpdate.ps1" %*
echo.
pause
