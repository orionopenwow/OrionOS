@echo off
rem Checks whether this PC is ready for OrionOS (Wi-Fi, graphics, disk, encryption, your apps). Changes nothing.
title Is my PC ready for Orion?
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0os\windows\Check-OrionReady.ps1" %*
