@echo off
rem Start here: one calm wizard that walks you through updating, testing and setting up your phone, step by step.
rem Enter always picks the recommended answer. Run it again any time: finished steps are skipped. (docs\TESTING.md)
title Start Here - Orion
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0os\windows\Start-Here.ps1" %*
echo.
pause
