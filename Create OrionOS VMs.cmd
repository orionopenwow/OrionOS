@echo off
REM Creates the two OrionOS virtual machines in VirtualBox (see os\README.md).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0os\windows\New-OrionVMs.ps1" %*
echo.
pause
