@echo off
rem Lets Orion Mobile on your home Wi-Fi reach the OrionOS lab. Run again if your PCs address changes.
title Open Phone Access - OrionOS
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0os\windows\Open-PhoneAccess.ps1" %*
if "%~1"=="" pause
