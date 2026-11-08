@echo off
rem One-time: put Orion on GitHub as github.com/<you>/Orion and turn on the website.
rem Your .env (API keys), updater signing keys, logs and build output are excluded by .gitignore.
title Publish Orion to GitHub
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
  echo Git is not installed. Get it from https://git-scm.com/download/win ^(defaults are fine^), then run this again.
  pause
  exit /b 1
)

echo.
set /p GHUSER=Your GitHub username:
if "%GHUSER%"=="" (
  echo No username entered.
  pause
  exit /b 1
)
set REPO=https://github.com/%GHUSER%/Orion

rem ---- 1. create the GitHub repo
where gh >nul 2>nul
if not errorlevel 1 (
  gh auth status >nul 2>nul || gh auth login --web
  gh repo view %GHUSER%/Orion >nul 2>nul || gh repo create Orion --public --description "Orion - a desktop companion that works the way you do" --homepage "https://%GHUSER%.github.io/Orion"
) else (
  echo.
  echo Your browser will open GitHub's "new repository" page.
  echo   - Repository name: Orion   - Public   - do NOT add a README, .gitignore or license
  echo Click "Create repository", then come back here.
  start "" "https://github.com/new?name=Orion&visibility=public&description=Orion%%20-%%20a%%20desktop%%20companion%%20that%%20works%%20the%%20way%%20you%%20do"
  pause
)

rem ---- 2. commit everything that isn't ignored
if not exist ".git\HEAD" git init >nul
rem Secret Shield: every commit is checked for passwords, tokens and keys before it's made
where node >nul 2>nul && node scripts\secret-shield\install-hooks.mjs
git config user.name >nul 2>nul || git config user.name "%GHUSER%"
git config user.email >nul 2>nul || git config user.email "%GHUSER%@users.noreply.github.com"
git add -A
rem safety net: never publish keys even if .gitignore was edited
git rm -r --cached --ignore-unmatch --quiet .orion-signing apps/daemon/.env .env >nul 2>nul
git diff --cached --quiet
if errorlevel 1 (
  git commit -q -m "Orion"
  if errorlevel 1 (
    echo.
    echo Nothing was uploaded: the commit was stopped ^(Secret Shield explains why above^). Fix it and run this again.
    pause
    exit /b 1
  )
)
git branch -M main

rem Secret Shield: scan the whole history once more before anything goes public (report in logs\secret-shield)
where node >nul 2>nul
if not errorlevel 1 (
  node scripts\secret-shield\scan-history.mjs
  if errorlevel 1 (
    echo.
    echo Nothing was uploaded: Secret Shield found something that looks like a key in the history ^(see above^).
    pause
    exit /b 1
  )
)

rem ---- 3. push (Git asks you to sign in to GitHub in your browser the first time)
git remote remove origin >nul 2>nul
git remote add origin %REPO%.git
echo.
echo Uploading to %REPO% ...
git push -u origin main
if errorlevel 1 (
  echo.
  echo The upload failed. Check that the repository exists at %REPO% and that you signed in as %GHUSER%.
  pause
  exit /b 1
)

rem ---- 4. turn on the website (GitHub Pages from the docs folder)
where gh >nul 2>nul
if not errorlevel 1 (
  gh api -X POST repos/%GHUSER%/Orion/pages -f "source[branch]=main" -f "source[path]=/docs" >nul 2>nul
  echo Website switched on.
) else (
  echo.
  echo Last step: on the page that opens, under "Build and deployment" choose
  echo   Source: Deploy from a branch   Branch: main   Folder: /docs   then Save.
  start "" "%REPO%/settings/pages"
)

echo.
echo Done. Your site will be live in a minute or two at:
echo   https://%GHUSER%.github.io/Orion/
echo Code: %REPO%
echo.
echo To publish a new Windows installer later, run:  npm run release
pause
