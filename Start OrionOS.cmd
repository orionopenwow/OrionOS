@echo off
REM One click: prepares Windows if needed, creates the OrionOS VMs if missing, and starts them.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0os\windows\Start-OrionOS.ps1" %*
if "%~1"=="" pause
