@echo off
REM Step through Orion's drafted social posts & creator outreach and press Enter to approve & schedule.
node "%~dp0scripts\publish-approved.mjs" %*
pause
