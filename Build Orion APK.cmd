@echo off
REM Builds the Orion Android app (Orion-Mobile.apk) with Android Studio, and installs it if your phone is plugged in.
REM Needs Android Studio (it brings Java and the Android SDK). The APK lands next to this file: Orion-Mobile.apk
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0apps\mobile\build-apk.ps1"
if errorlevel 1 goto failed
echo.
echo Done. The app is at: "%~dp0Orion-Mobile.apk"
goto end
:failed
echo.
echo Orion-Mobile.apk was NOT built. Read the message above.
:end
echo.
pause
