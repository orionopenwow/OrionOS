@echo off
rem Orion USB Maker: puts OrionOS on a USB stick (it will be erased; you pick which one).
title Orion USB Maker
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0os\windows\Make-OrionUSB.ps1" %*
