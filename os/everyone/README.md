# OrionOS: everyone can use it

`docs/ORIONOS_COMPLETE_PLAN.md` Part 1 rows "Accessibility", "Typing other languages", "More than one person" and
"Laptop power" (step 6). Installed by `os/image/os-everyone.sh`, called from `os/lab/provision.sh` step 6. The Sway
lines are one block of `os/lab/sway-config` (`# ---- Everyone can use it`). Settings pages: Accessibility,
Languages & typing, and extra groups on Power and Users (`os/settings/orion_settings/everyone*.py`).

| What | Keys | Command |
|---|---|---|
| Screen reader (Orca) | Super+Alt+S | `orion-access screen-reader on\|off\|toggle` |
| On-screen keyboard (wvkbd) | Super+Alt+K | `orion-access keyboard on\|off\|toggle` |
| High contrast | Super+Alt+C | `orion-access contrast on\|off\|toggle` |
| Bigger text | | `orion-access text-size 1\|1.25\|1.5\|1.75\|2` |
| Everything bigger (every screen) | | `orion-access screen-size 1.25\|1.5\|2\|off` |
| Zoom the screen | Super+Alt+= / - / 0 | `orion-access zoom in\|out\|reset` |
| Zoom the Orion window | Ctrl+= / Ctrl+- / Ctrl+0 | `orion-access hud-zoom 1.5` |
| First-run question | | `orion-access wizard` |
| Another language | Ctrl+Space switches | `orion-typing add mozc`, `orion-typing list` |
| Emoji | Super+. | `orion-typing emoji` |
| Battery, hibernation | | `orion-power status`, `orion-power hibernate on` |
| People | | `orion-people status`; adding: Settings > Users |

## Accessibility
Choices live in `~/.config/orion/access.json`; `orion-access session` (run by Sway at log-in) puts them back.
- **Screen reader:** Orca, started and stopped by `orion-access`, also at log-in when it's on. GTK apps and the
  Orion window (WebKitGTK) talk to it through AT-SPI on their own; for Qt and Electron apps `orion-access` adds
  `QT_ACCESSIBILITY=1`, `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1` and `ACCESSIBILITY_ENABLED=1` to
  `~/.config/orion/session.env` (read by orion-session at the next log-in). **Not yet tested on Sway:** Orca was
  made for GNOME; on Sway its own keyboard commands (the Orca key) may not reach Wayland apps, but reading the
  focused item and what you type should work. Test this on real hardware before promising more.
  `orion-a11y-check` (os/desktop/orion_a11y.py) checks what Orca needs on Sway: Orca and the accessibility service
  installed, the accessibility bus answering, nothing switching the bridge off (`NO_AT_BRIDGE=1`, `GTK_A11Y=none`;
  `orion-a11y-check fix` removes them from session.env), Qt apps told to talk to it, and the Orion window on the
  bus. `orion-access` starts the accessibility bus before Orca (`orion-a11y-check bus-up`), and `orion-hud`
  never runs the HUD with the bridge off.
- **On-screen keyboard:** wvkbd (`wvkbd-mobintl`). Super+Alt+K shows or hides it (signals: SIGRTMIN toggles,
  SIGUSR2 shows). On touch screens it starts hidden at log-in. It can't pop up by itself when a text box gets
  focus: wvkbd isn't an input method and Sway has no "text box focused" signal for it, so it's the key or the
  switch.
- **High contrast:** the `Orion-Glass-HighContrast` GTK theme (`os/theme/gtk/`), the libadwaita high-contrast
  setting, black/white/yellow terminal colours (`~/.config/foot/orion-access.ini`, included by foot.ini), thick
  yellow window borders (`~/.config/orion/sway.d/access.conf`), a bigger pointer, and the Orion window's contrast
  mode (`GET /access` from the daemon; `apps/desktop/src/lib/hudAccess.ts`). The theme line in sway-config keeps
  it on across Sway reloads (`~/.config/orion/high-contrast`).
- **Bigger text:** GTK's `text-scaling-factor`, the terminal font, the Orion window's root font size and the
  pointer. **Everything bigger** sets Sway's output scale for every screen.
- **Zoom:** Super+Alt+= / - change the scale of the screen you're on in steps of 25% (back with Super+Alt+0; reset
  at log-out). wlroots has no built-in magnifier that follows the mouse, so this is the honest equivalent. The
  Orion window has its own zoom.
- **Reduced motion:** the existing calm-motion setting (GTK `enable-animations`, which the Orion window follows);
  the Accessibility page shows the same switch as Appearance.

## Typing other languages and emoji
fcitx5 only runs once you add a second language (one language is just the keyboard Sway already has). Adding one
writes `~/.config/fcitx5/profile`, starts fcitx5, and puts `GTK_IM_MODULE`, `QT_IM_MODULE`, `XMODIFIERS` and
`SDL_IM_MODULE` in session.env (so log out once). Ctrl+Space switches. Engines: keyboard layouts and m17n
(Hindi, Tamil, Vietnamese) everywhere; Chinese (pinyin), Japanese (mozc) and Korean (hangul) on real hardware and
the USB stick (big downloads, left out of the VM). The emoji picker is wofi over Python's own Unicode table (no
download): pick one and it's typed with wtype and copied; if typing doesn't reach the app, paste it.

## More than one person
- **Guest:** turned on in Settings > Users (asks for the orion-admin password: `orion-people-admin guest on`).
  The `guest` account has no password; only greetd lets it in (`pam_succeed_if` in `/etc/pam.d/greetd`).
  At log-in `orion-guest-home` (pam_exec) mounts a fresh RAM disk on `/home/guest` with Orion's look from
  `/etc/orion/guest-skel`; at log-out it stops every guest program and throws the RAM disk away. The guest can't
  read anyone's home (750) or the vault, isn't in any admin group, and can't use Settings' root helpers. Orion in a
  guest session runs in **guest mode** (`apps/daemon/everyone.py`): chat and voice only, no agents, no messages,
  no approvals, no phone, boardroom, apps or money routes (allow-list). "Start a guest session" (polkit action
  `ai.orion.everyone.guest`) leaves a note in `/run/orion-login`; after you log out, `orion-login-next` (put in
  front of greetd's login command) starts the guest once. Or type `guest` at the text login.
- **Add a person:** Settings > Users > Add a person runs `orion-people-admin add LOGIN "Name"` through the
  orion-admin account, then `passwd` for them. They get their own home (750) from `/etc/orion/person-skel`, their
  own Orion (the daemon is a per-user service; its data is in their home) and the first-run on real hardware.

## Laptop power
- `orion-power watch` (Sway starts it; it exits at once on computers without a battery) checks every minute
  (every 20 s below 10%): a note at 15%, a red note at 7%, sleep at 4% (never straight after log-in or waking:
  3 minutes to plug in). Plugging in re-arms the notes.
- Power mode follows the battery (power-profiles-daemon): Performance drops to Balanced when unplugged, Power
  saver below 20%, and what you had comes back when you plug in. Settings > Power > "Save power on battery".
- Lid: Settings > Power (logind). With another screen plugged in the lid does nothing and the panel goes off
  (`os/hardware`, kanshi). "Sleep, then hibernate" (2 hours, `/etc/systemd/sleep.conf.d/60-orion.conf`) can only
  be turned on where hibernation works: swap at least as big as memory, a resume setting, no Secure Boot lockdown,
  and never on the USB stick (memory would land outside the vault).
- Real-laptop test: `os/hardware/LAPTOP_POWER_CHECKLIST.md`.

## Tests
`python -m pytest -q os/everyone/tests os/settings/tests` and `apps/daemon/tests/test_everyone.py`.
