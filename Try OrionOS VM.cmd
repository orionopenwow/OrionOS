@echo off
REM Boot the OrionOS 1.0 image in a VirtualBox test VM (separate from orion-lab). Add -Reset to start over.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0os\windows\Try-OrionOS-VM.ps1" %*
pause
