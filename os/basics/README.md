# OrionOS everyday basics

Step 1 of `docs/ORIONOS_COMPLETE_PLAN.md` Part 1: the clock, screenshots, clipboard history and night light.
Installed by `os/image/os-basics.sh` (called from `os/lab/provision.sh` step 6). The keys are set in one block of
`os/lab/sway-config` (`# ---- OS basics`).

| What | Keys | Command |
|---|---|---|
| Screenshot of the whole screen | Print | `orion-screenshot full` |
| Screenshot of an area | Shift+Print, Ctrl+Alt+P | `orion-screenshot area` |
| Area into the markup tool | Ctrl+Print | `orion-screenshot markup` |
| Clipboard history (last 50) | Super+V, Ctrl+Alt+V | `orion-clipboard pick` |
| Night light on/off | Super+N, Ctrl+Alt+N | `orion-nightlight toggle` |
| Time zone | Settings > Time zone & clock | `orion-timezone choose` |

None of these keys were taken before (the Orion Sway config has no split or layout keys, so Super+V was free).

## Clock and time zone
- **Network time** is on: `systemd-timesyncd` (Ubuntu's own time servers). If chrony or ntpsec is already
  installed, that one is kept.
- **Time zone at first start.** The first-run window (`orion-firstrun`, step 1) keeps a zone that was already
  chosen. Otherwise it asks whether to look it up online; that is **one** request to Ubuntu's location service
  `https://geoip.ubuntu.com/lookup` (the same one Ubuntu's installer uses), which sees the computer's internet
  address, and it only happens after you say yes (default: no). Without it, you pick from a searchable list
  (wofi) of every zone in the tz database, with the zones of your language setting's country first
  (`en_GB` puts `Europe/London` on top). Nothing is hard-coded; no location is ever guessed.
- **Change it later:** Settings > Time zone & clock (list, online lookup, or status), or
  `orion-timezone set Region/City`. The polkit rule `50-orion-basics.rules` lets the session user change the
  zone and network time without a password while at the computer; setting the clock by hand still asks.
- **Everything follows it:** the bar uses local time (`date`, no more `-u`); screenshot names use local time;
  the night light re-times itself; the Orion window and the brain are restarted after a change, because a
  running web view and Python only read the zone when they start.
- **The VM lab** stays on UTC until you pick a zone; `provision.sh` no longer resets a zone you picked.

### Hardware clock and Windows (dual boot)
OrionOS keeps the PC's hardware clock in **UTC** (`timedatectl set-local-rtc 0`), as Linux expects. Windows keeps
it in local time by default, so after switching from OrionOS to Windows the Windows clock is off by your UTC
offset until Windows syncs from the internet. Don't switch Linux to local time to "fix" it (it breaks daylight
saving changes); fix Windows instead, once, in an administrator Command Prompt:

    reg add "HKLM\System\CurrentControlSet\Control\TimeZoneInformation" /v RealTimeIsUniversal /t REG_DWORD /d 1 /f

## Screenshots
`grim` takes the picture, `slurp` picks the area, `swappy` adds arrows, boxes, text and blur. Files go to
`~/Pictures/Screenshots/Screenshot_YYYY-MM-DD_HH-MM-SS.png` (a `_2`, `_3`... is added if two land in the same
second), are copied to the clipboard as an image, and a notification shows the file name. The folder is in the
file manager's side bar.

**swappy** isn't packaged for Ubuntu 24.04, so `os-basics.sh` builds the pinned upstream release (v1.5.1, MIT,
commit checked) and removes the build tools afterwards. It is patched to Orion's colours: the three colour
buttons are alert pink `#FF5C8A`, Photon `#00E5FF` and Accretion `#7C5CFF`, the custom colour starts on Ember
`#FFC66B`; its window uses the Orion Glass GTK theme and Figtree for text (`swappy-config`). If the build fails,
Ctrl+Print still saves the area, without markup.

**Share clean (Toolkit #23, the Share Scrubber).** Every screenshot notification, including the one after the
markup tool closes, has a "Share clean" button: it runs `orion-share-scrub /path/to/file.png`, which reads the
picture with OCR, blurs keys, tokens, email addresses, home folders, IP addresses and your server names (from
`~/.ssh/config`), saves `file-clean.png` next to it and copies that. **Super+Shift+C** does the same for
whatever is on the clipboard, text or picture (Sway's reload moved to Super+Shift+R). Nothing is ever uploaded;
the clean copy only goes to the clipboard. The cleaning itself is `apps/daemon/share_scrubber.py`.

## Clipboard history
`cliphist` keeps the last 100 copies (text and images) in `~/.cache/cliphist/db`; Super+V lists the newest 50,
and the pick is pasted where you were typing (Ctrl+Shift+V in terminals). The last entry in the list clears the
history. What is **never** kept:
- copies a password manager marks as secret (the `x-kde-passwordManagerHint` type KeePassXC offers, and
  `CLIPBOARD_STATE=sensitive` from newer wl-clipboard versions). Ubuntu 24.04's cliphist 0.4.0 and
  wl-clipboard 2.2.1 don't check either, so `orion-clipboard store` does;
- any text matching Secret Shield's patterns (`apps/daemon/secret_shield_rules.json`, the same patterns the
  daemon and the git hook use: API keys, tokens, private keys, `password=...` lines).
The copy is checked in memory (`$XDG_RUNTIME_DIR`), so a skipped secret never touches the disk.

## Night light
`wlsunset` warms the screen to 4000 K from sunset to sunrise. The sun times come from your time zone's main city
(the tz database's `zone1970.tab` / `zone.tab` positions; old names such as `Asia/Calcutta` are followed to
their current zone), so no location lookup is needed. Zones without a place (UTC) use 19:30 to 07:00.
On by default; Super+N or Settings turns it off, and the choice is kept in `~/.config/orion/nightlight`.
Screens that can't change colour (most VMs) are detected, and it stays off there quietly.

## Files
- `orion_basics.py`: offline helpers (zone positions, the zone list, the geoip answer, the secret filter).
- `swappy-config`, `50-orion-basics.rules`: installed by `os-basics.sh`.
- `tests/`: `python3 -m unittest discover -s os/basics/tests` and `sh os/basics/tests/test_scripts.sh`.
- Commands (in `os/lab/bin/`): `orion-timezone`, `orion-screenshot`, `orion-clipboard`, `orion-nightlight`.
