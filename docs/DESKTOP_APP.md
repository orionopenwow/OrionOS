# Orion desktop app: build, install and auto-update

> **New here?** On Windows, double-click **Start Here.cmd** in the Orion folder: it sets everything up step by step. To check that it all works, see [docs/TESTING.md](TESTING.md).

Orion ships as a normal Windows app (`Orion_<version>_x64-setup.exe`). It needs no admin rights, sits in
the system tray and runs its own Python brain. Updates install themselves.

## How it works
- **One program.** The Tauri app (`apps/desktop/src-tauri`) starts the Python daemon hidden, with no
  console window. On first run it finds Python 3.10+, creates a private environment in
  `%LOCALAPPDATA%\com.orion.desktop\runtime` and installs the brain's packages, which takes a few
  minutes. After an update it only reinstalls if `requirements.txt` changed.
  - If Python is missing, the startup screen offers **Install Python** (winget, per-user).
  - Logs are in `%LOCALAPPDATA%\com.orion.desktop\logs\daemon.log`. You can also reach them from
    tray → **Open logs**.
- **Settings** live in `%USERPROFILE%\.orion\.env`, which is created on first run, so updates never
  touch them. Copy your keys over from `apps\daemon\.env` once.
- **Tray.** Closing the window hides Orion. The tray menu has **Show**, **Restart Orion's brain**,
  **Check for updates**, **Open logs** and **Quit**. Launching Orion a second time just brings the
  window back.
- **Updates.** The app checks GitHub Releases 15 s after launch, every 6 hours, and when you choose
  tray → **Check for updates**. A banner then offers **Restart to update**. Updates are signed: the
  app refuses anything not signed with your key.

## One-time GitHub setup (about 5 minutes)
1. **Create a repository on GitHub.** The update feed must be publicly downloadable, so choose one:
   - **Public repo** (simplest): releases go to the same repo.
   - **Private code repo:** also create a public repo such as `orion-releases` with a README. In the
     private repo, add the variable `ORION_RELEASE_REPO` = `you/orion-releases` and the secret
     `RELEASE_TOKEN` (a fine-grained token with *Contents: read & write* on `orion-releases`).
2. **Add the signing secrets.** In the code repo, go to **Settings → Secrets and variables → Actions →
   New repository secret**:
   - `TAURI_SIGNING_PRIVATE_KEY`: the full contents of `.orion-signing/orion-updater.key`
   - `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`: the contents of `.orion-signing/orion-updater.password`
3. **Push the code.** Run this from the `wonderful-meitner` folder. The first command installs the
   build pipeline from `scripts/ci/`:
   ```powershell
   node -e "require('fs').mkdirSync('.github/workflows',{recursive:true});require('fs').copyFileSync('scripts/ci/release.yml','.github/workflows/release.yml')"
   git add -A
   git commit -m "Orion 2.0 desktop app"
   git branch -M main
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
4. **Publish the first installer.** Run `npm run release -- 2.0.0`, then wait about 15 minutes for
   **Actions → Release Orion**. Download `Orion_2.0.0_x64-setup.exe` from **Releases** and run it.

## Shipping an update
```powershell
npm run release                                    # 2.0.0 -> 2.0.1 (patch)
npm run release -- minor --notes "Grimoire + Battle Stations"
```
This command:
1. Bumps the version everywhere it appears.
2. Runs the daemon tests.
3. Commits, tags and pushes.

GitHub Actions then builds and signs the installer and publishes it. Every installed Orion offers the
update within a few hours, or right away from tray → **Check for updates**.

Self-evolve patches made from the installed app are written to your source checkout. To enable this,
set `ORION_REPO_ROOT` in `%USERPROFILE%\.orion\.env`. The patches ship the same way.

## Local builds (optional)
To build on your own PC you need Rust (https://rustup.rs) and the "Desktop development with C++"
workload from Visual Studio Build Tools. Then:
```powershell
npm run build:app        # installer in apps/desktop/src-tauri/target/release/bundle/nsis/
```
`npm run dev` still runs the app in development mode with hot reload, using `apps/daemon/.venv`.
