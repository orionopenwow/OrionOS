@echo off
rem Copies your files, bookmarks, Wi-Fi names and app list to a USB drive for OrionOS. Never deletes anything.
title Move my stuff to Orion
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0os\windows\Move-ToOrion.ps1" %*
