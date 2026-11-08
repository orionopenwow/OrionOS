# Get apps and Share files

Step 2 of `docs/ORIONOS_COMPLETE_PLAN.md`: the app store (Part 1 "App store"), file sharing (Part 1 "File
sharing"), the optional Orion Studio section (Part 2), and app updates that are tested and rolled back if they
break (Part 3, items 5 and 6). Installed by `os/image/os-getapps.sh`, called from `os/lab/provision.sh` for every
desktop profile (never the boardroom).

| What | Where | Command |
|---|---|---|
| Get apps window | launcher: "Get apps" | `orion-apps open [APP_ID]` |
| Share files | launcher: "Share files" | `orion-share open` |
| Safe update of every app | Get apps > Updates > "Update all safely" | `orion-apps update-all [--json]` |
| What's waiting | Get apps > Updates | `orion-apps updates --json` |
| Install one app (the owner's action) | a click in Get apps | `orion-apps install APP_ID [--json]` |
| Weekly updates on / tell-only / off | Settings (os-settings) or the file | `orion-apps config auto-update auto\|notify\|off` |
| Sharing port toggle | Settings (os-settings) | `orion-share firewall on\|off\|status [--json]` |
| Change history | Get apps > Updates > "What changed" | `orion-apps history --json` |
| Run Windows apps (Bottles, Steam/Proton, Lutris, recipes) | Get apps > Run Windows apps (`catalog.json` "windows") | `orion-apps open --page windows`; root recipe `windows-apps`; `orion-wow-client` |

Every command answers in plain words, or JSON with `--json` (each JSON answer has `ok` and a plain-words
`message`). Also: `installed`, `search WORDS`, `picks`, `info APP_ID`, `remove APP_ID`, `resume APP_ID`,
`status`, `install-bundle FILE` (Orion Studio).

## Flatpak and Flathub
- `flatpak` (Ubuntu 24.04's 1.14) is installed, and Flathub is added **system-wide** (`flatpak remotes --system`
  shows `flathub`), so apps are installed once for everyone on the computer.
- **No download at build time.** The remote comes from `flathub.flatpakrepo` in this folder: Flathub's own repo
  file. dl.flathub.org wasn't reachable from the build machine, so the file was taken from two independent copies
  on GitHub (`pop-os/default-settings` `etc/pop-os/flatpak/flathub.flatpakrepo` and `getsolus/packages`
  `packages/f/flatpak/files/flathub.flatpakrepo`, both identical, 2026-10-16). Its key is "Flathub Repo Signing
  Key <flathub@flathub.org>", fingerprint **6E5C 05D9 79C7 6DAF 93C0 8135 4184 DD4D 907A 7CAE** (RSA 4096,
  created 2017-06-16, expires **2027-06-14**). `os-getapps.sh` refuses the file if the key's SHA-256 or fingerprint
  ever changes. Before 2027-06 replace the file with Flathub's refreshed one (same fingerprint, new expiry) and
  update the pinned SHA-256 in `os-getapps.sh`.
- Flatpak checks every download against that key (`gpg-verify=true`, `gpg-verify-summary=true`).
- The first time an app is installed, Flatpak also downloads the shared parts ("runtimes") it needs.
- `orion-session` now reads `/etc/profile.d/flatpak.sh`, so installed apps appear in the launcher.

## Who may install
- **The owner.** Installing is a click in Get apps (or `orion-apps install`). The polkit rule
  `50-orion-getapps.rules` lets the session user `orion` update apps (and shared runtimes) without a password, so
  the weekly timer works. Installing a **new app** needs a person to confirm it (review item H9): polkit asks for
  your password (a password window needs the desktop's polkit agent; in a terminal flatpak asks itself), unless the
  install gate is open: `orion-install-gate open` opens it for 10 minutes after your password, or after a fresh
  approval from your paired phone's fingerprint (`--approval FILE`, checked by root with
  `os/safety/orion-approval-check`). Removing an app and installing a downloaded bundle also ask for your password.
  Adding or changing remotes is never allowed by this rule. Polkit counts Orion's own background services as part
  of your session, so a password (or the phone) is what separates you from a program running as `orion`; agents'
  sandbox user `orion-agent` gets nothing.
- **Agents only ask.** The MCP tool `apps_install` (or `POST /apps/install-request`, `apps/daemon/get_apps.py`)
  puts an "Install X?" card in Orion's approval queue, saying who asked, why, and whether the app is one of
  Orion's picks. Nothing is installed unless the owner approves it; then the daemon runs
  `orion-apps install <id> --json`. `apps_request_status` tells the agent what happened.

## The window
GTK 4 + libadwaita (python3-gi, gir1.2-adw-1), application id `local.GetApps` (no "orion" in it: the Sway
config sends `app_id ~ orion` to the HUD's workspace full screen). Libadwaita ignores GTK themes, so the window
loads the Orion colours and the Orion Glass layer (`/usr/share/themes/Orion-Glass/gtk-4.0/orion-glass.css`)
itself, then `getapps.css`: see-through glass, belt-dot window buttons, Unbounded headings, Figtree text. It floats
in the middle (`sway-getapps.conf` -> `/etc/sway/config.d/61-orion-getapps.conf`).
- **Discover:** Orion's picks by category (`catalog.json`; every id was checked on 2026-10-16 against its Flathub
  packaging repo `github.com/flathub/<id>`), and Flathub search from the local catalogue (`flatpak search`).
- **Installed**, with Open and Remove (your settings stay in `~/.var/app/<id>`).
- **Updates:** what's waiting, "Update all safely", apps paused after a bad update ("Let it update again"), and the
  change history.
- **Orion Studio** (Part 2): the Craft suite, optional, with the plan's caution text and an alpha badge.
  - PhotoCraft (`ai.storyteller.photocraft`) and PdfCraft (`ai.storyteller.pdfcraft`): ids from their own Flatpak
    manifests (`packaging/linux/flatpak/` in `github.com/storytold/photocraft` and `.../pdfcraft`). They are **not
    on Flathub**: each GitHub release has a Flatpak bundle, an AppImage and `SHA256SUMS.txt`. "Download page" opens
    the official release page; "Install downloaded file…" (`orion-apps install-bundle FILE`) installs a bundle only
    if the `SHA256SUMS.txt` saved next to it lists it with the same checksum and its name is one of these apps.
    The Linux bundles aren't signed (only the macOS/Windows builds are), so the checksum is the check available;
    the plan's "installs are checked for signatures" waits on that upstream.
  - The other Craft apps are "coming soon", linking to `github.com/storytold` (no ids were invented).

## Safe updates (`orion-apps update-all`)
For each app with an update, one at a time:
1. note its current version (`flatpak info --show-commit`) and its runtime's, and check it starts now
   (`flatpak run --command=true <id>`: the sandbox starts and runs a no-op, no window needed);
2. update it (`flatpak update <id>`);
3. check it starts again. If it started before and doesn't now, put the noted version back
   (`flatpak update --commit=<old> <id>`), and if it still won't start, the runtime's old version too; then
   pause its updates (`flatpak mask <id>`) and say so in plain words ("Spotify's update didn't start, so Orion put
   the previous version back and paused its updates…"). If it didn't start before either, the check can't tell
   anything, so the update stays and the message says so.

Then runtime-only updates, with every app checked before and after; if one stops starting, the runtimes go back
and are paused. "Let it update again" (`orion-apps resume <id>`) removes the pause.

**Change history** (Part 3 item 6 reads it): JSON lines in `~/.local/state/orion/changes/apps.jsonl`, one per
change: `id`, `ts`, `time`, `source: "get-apps"`, `kind` (`app_install`, `app_remove`, `app_update`,
`app_update_failed`, `app_rollback`, `runtime_update`, `runtime_rollback`, `app_resume`), `app`, `name`,
`from_commit`, `to_commit`, `why`, `checks` (`[{name, before, after}]`), `result`, and `undo` (the command that
undoes it). The last run's summary is in `apps-last-run.json` next to it.

**Weekly** (plan Part 3 permission levels: app and Flatpak updates are level 2, "tell me after"): the user timer
`orion-apps-update.timer` (weekly, up to 6 h random delay, catches up after the computer was off) runs
`orion-apps update-all --auto`, which follows `~/.config/orion/apps.json`:
- `"auto_update": "auto"` (default): update safely, then one notification with what changed (desktop, and your
  phone through Orion's alerts, `POST /apps/notify`);
- `"notify"`: only tell you what's waiting (level 1);
- `"off"`: nothing.

## Share files (`orion-share`)
LocalSend (Apache-2.0; Windows, Android, iPhone, Mac, Linux), from Flathub (`org.localsend.localsend_app`), never
bundled. The first `orion-share open` shows a small window that explains it and installs it with one click;
after that it opens LocalSend directly. LocalSend's own launcher entry is routed through `orion-share open`
(`~/.local/share/applications/org.localsend.localsend_app.desktop`, hidden), so the port logic always applies.

**Firewall.** The image's ufw blocks everything coming in (provision.sh step 9). LocalSend needs port **53317,
TCP and UDP**, to be found and to receive. Off by default:
- `orion-share open` starts `orion-share-firewall.service` while LocalSend runs and stops it when LocalSend quits.
  The unit runs `orion_share_firewall.py open|close` (as root, no input): `ufw allow in from <net> to any port
  53317 proto tcp|udp comment "orion-share LocalSend"` for each private IPv4 network (10/8, 172.16/12,
  192.168/16) this computer is on right now, skipping VPN, container and VM interfaces (tailscale, wg, podman,
  docker, virbr, ...). Closing deletes exactly the rules with that comment.
- Settings can keep it open: `orion-share firewall on` (until `off` or a restart).
- `orion-share-firewall-reset.service` closes it at every boot, so a power cut can't leave it open.
- polkit lets `orion` start and stop that one unit, nothing else. IPv6 isn't opened (LocalSend finds devices over
  IPv4).

## How Flatpak apps look
What works, through the settings portal (`xdg-desktop-portal-gtk`, picked by Sway's `sway-portals.conf`, reading
the gsettings that `os/theme/install-desktop-theme.sh` sets):
- **Dark mode:** libadwaita / GTK 4 apps, Electron apps (Discord, VS Code, Obsidian) and Firefox-based apps follow
  `color-scheme = prefer-dark`.
- **Icons and cursor:** Flatpak shows the host's `/usr/share/icons` inside every sandbox (`/run/host/share/icons`)
  and the portal passes `icon-theme = Orion`, so apps use the Orion icons (Papirus-Dark beneath).
- **Fonts:** the host's fonts (`/run/host/fonts`) include Figtree, Unbounded and Martian Mono.
- **Window frame:** SwayFX's glass, corners and shadows frame every app, Flatpak or not.

What doesn't: the **Orion Glass GTK theme** can't reach inside the sandbox; that needs a Flatpak theme package
(`org.gtk.Gtk3theme.Orion-Glass`), which doesn't exist yet. Libadwaita apps keep their own look in dark;
GTK 3 apps that can't read the portal may fall back to light Adwaita; for one such app,
`flatpak override --user --env=GTK_THEME=Adwaita:dark <id>` makes it dark. Nothing is overridden globally (it would
also hit libadwaita apps). Firefox (Mozilla's .deb) and other non-Flatpak apps are unaffected and keep Orion
Glass. Not tested against real Flathub apps here (dl.flathub.org wasn't reachable from the build machine).

## Tests
- `python3 -m pytest os/getapps/tests -q`: the CLI's JSON output and the install / update / rollback / pause /
  weekly logic, against a stand-in `flatpak` (`tests/fake_flatpak.py`), plus the firewall helper with stand-in
  `ufw`/`ip`.
- `apps/daemon/tests/test_get_apps.py`: approval cards, nothing installed without approval, routes, MCP tools.
- `sh os/getapps/tests/shot.sh OUT_DIR` (root, in a test chroot with sway and grim): screenshots of every page
  under headless Sway with stand-in data.
