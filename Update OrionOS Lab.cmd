@echo off
REM Puts the latest Orion code into the running orion-lab VM (then run: sudo orion-update inside it).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0os\windows\Update-OrionLab.ps1"
echo.
pause
