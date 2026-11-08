@echo off
rem Save all of Orion's work to GitHub on its own branch (never main), then open a pull request to review.
rem Secret Shield checks the commit first; your .env, signing keys, logs and build output are never included.
title Save Orion work to GitHub
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
  echo Git is not installed. Get it from https://git-scm.com/download/win, then run this again.
  pause
  exit /b 1
)
if not exist ".git\HEAD" (
  echo This folder isn't connected to GitHub yet. Run "Publish to GitHub.cmd" once first.
  pause
  exit /b 1
)

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmm"') do set STAMP=%%i
set BRANCH=orion-work-%STAMP%

where node >nul 2>nul && node scripts\secret-shield\install-hooks.mjs

git switch -c %BRANCH%
if errorlevel 1 (
  echo Couldn't make the branch %BRANCH%.
  pause
  exit /b 1
)
git add -A
rem safety net: never publish keys even if .gitignore was edited
git rm -r --cached --ignore-unmatch --quiet .orion-signing apps/daemon/.env .env >nul 2>nul
git diff --cached --quiet
if not errorlevel 1 (
  echo Nothing new to save.
  pause
  exit /b 0
)
git commit -q -m "Orion work saved %STAMP%"
if errorlevel 1 (
  echo.
  echo Nothing was uploaded: the commit was stopped ^(Secret Shield says why above^). Fix it and run this again.
  pause
  exit /b 1
)

where node >nul 2>nul
if not errorlevel 1 (
  node scripts\secret-shield\scan-history.mjs
  if errorlevel 1 (
    echo.
    echo Nothing was uploaded: Secret Shield found something that looks like a key ^(report in logs\secret-shield^).
    pause
    exit /b 1
  )
)

git push -u origin %BRANCH%
if errorlevel 1 (
  echo.
  echo The upload didn't go through. Check your internet connection and GitHub sign-in, then run this again.
  pause
  exit /b 1
)

where gh >nul 2>nul
if not errorlevel 1 (
  gh pr create --base main --head %BRANCH% --title "Orion work %STAMP%" --body "Saved from this PC. Review before merging into main." && gh pr view --web
) else (
  for /f "delims=" %%u in ('git remote get-url origin') do set URL=%%u
  start "" "%URL:.git=%/compare/main...%BRANCH%?expand=1"
)
echo.
echo Saved to the branch %BRANCH%. main is unchanged until you merge the pull request.
pause
