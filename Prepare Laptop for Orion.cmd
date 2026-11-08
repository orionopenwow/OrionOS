@echo off
rem Gets this laptop ready for OrionOS on its own partition. Changes nothing on your disk without asking.
title Prepare Laptop for Orion
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0os\windows\Prepare-Laptop.ps1" %*
