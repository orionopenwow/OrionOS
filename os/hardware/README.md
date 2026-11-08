# OrionOS displays & printing

`docs/ORIONOS_COMPLETE_PLAN.md` Part 1, "Several monitors" (kanshi + wdisplays) and "Printing and scanning"
(CUPS driverless printing, Simple Scan). Installed by `os/image/os-hardware.sh`, called from `os/lab/provision.sh`
step 6 (and step 9 for the firewall rule, because step 9 resets the firewall). The Sway lines are one block of
`os/lab/sway-config` (`# ---- Displays & printing`).

| What | Keys | Command |
|---|---|---|
| Screens, primary screen, saved layouts | | `orion-displays list [--json]` |
| Arrange screens (wdisplays) | Super+P, Display key | `orion-displays arrange` |
| Remember the layout on screen | | `orion-displays save NAME` |
| Put a saved layout back | | `orion-displays apply NAME` |
| Which screen has the Orion HUD | | `orion-displays primary NAME\|auto` |
| Printers (added and found on the network) | | `orion-printers list [--json]` |
| Add a printer ("Printers" in the launcher) | | `orion-printers add` |
| Scan ("Scan documents" in the launcher) | | `orion-printers scan` |
| Paper size | | `orion-printers paper [a4\|letter\|auto]` |

## Several screens
- **Every set of screens is remembered.** `orion-displays` keeps your layouts in `~/.config/orion/displays.json`
  and writes `~/.config/kanshi/config` from it. kanshi (a user service, `orion-kanshi.service`) puts the right
  layout on screen whenever screens are plugged in or out. Screens are recognised by make, model and serial
  number, so a monitor is the same monitor on any port. Two identical monitors without serial numbers are told
  apart by their port instead.
- **A new set of screens** (one no layout exists for) gets one straight away: the laptop's own panel on the
  left, the other screens to its right in port order, tops lined up, each at its native resolution. Scale steps
  like Windows' (1, 1.25, 1.5, 1.75, 2) by pixel density (125, 155, 190 and 215 pixels per inch and up, read from
  the screen's EDID), never so big that the screen is less than 1280 wide. In-between sizes are sharp for Wayland
  apps; older X11 apps (through Xwayland) look a little soft at them, which Settings > Displays says next to the
  choice. A notification asks "New screen set up — Arrange?"; the button opens wdisplays.
- **Arrange** opens wdisplays (drag screens, change resolution or scale). When you close it, the layout is saved
  for this set of screens (`save --current`), so it comes back next time. `save NAME` does the same under a name
  of your choice. A saved layout replaces the automatic one for the same screens.
- **Workspace 1, the Orion HUD, is on the primary screen** and follows it when screens come and go
  (`orion-displays.service` watches Sway's output events, and kanshi runs `orion-displays changed` after each
  layout). The primary screen is the one you picked (`primary`, Settings > Displays), else the laptop panel,
  else the leftmost screen. With one other screen, workspaces 2-5 go there (apps open beside the HUD, not on top
  of it) and 6-9 stay on the primary; with more screens 2-9 are shared out from left to right. Workspaces that
  are already open are moved over, and each screen goes back to showing what it showed.
- **Lid shut with another screen on:** the laptop panel goes off (Sway's lid switch runs `orion-displays lid
  close`) and the computer keeps running (`/etc/systemd/logind.conf.d/50-orion-lid.conf`:
  `HandleLidSwitchDocked=ignore`, applied from the next boot). Lid open: the panel comes back where the layout
  has it. Lid shut with no other screen: nothing is switched off, and the laptop sleeps as usual. Saving a layout
  while the lid is shut remembers the panel as it was, not as switched off.
- **Night light** (wlsunset, `orion-nightlight`) already covers every screen, including ones plugged in later.

### `orion-displays list --json` (read by the Settings app)
```json
{
  "outputs": [
    {"name": "eDP-1", "key": "Samsung Display Corp. ATNA40YK07-0 Unknown", "make": "...", "model": "...",
     "serial": "Unknown", "internal": true, "active": true, "primary": true,
     "mode": "2880x1800@90.000Hz", "position": [0, 0], "scale": 2.0, "transform": "normal",
     "modes": ["2880x1800@90.000Hz", "2880x1800@60.001Hz"], "workspaces": [1, 6, 7, 8, 9]}
  ],
  "primary": "eDP-1",
  "current_profile": "laptop-dell-s2721dgf",
  "profiles": [{"name": "laptop-dell-s2721dgf", "auto": true, "current": true, "outputs": ["<key>", "<key>"]}]
}
```
`mode`, `position` and `scale` are `null` for a screen that is off. `primary NAME` takes a port name or a key.

## Printing and scanning
- **Driverless printing.** CUPS 2.4 with Avahi prints to any network printer that speaks IPP Everywhere or
  AirPrint (nearly every printer made since about 2015) without installing a driver: they show up in every print
  dialog and in `orion-printers list` as "found on your network". USB printers do the same through **ipp-usb**
  (not in the VM profile: a virtual machine has no USB printer).
- **No cups-browsed.** CUPS already makes a queue on demand for every driverless printer it sees. cups-browsed
  is the part that listened on UDP port 631 for anyone (CVE-2024-47176, the 2024 remote-printing attack chain);
  it isn't installed, and if something pulls it in it is masked.
- **CUPS stays on this computer.** `Listen localhost:631` and `Browsing No` (your printers are never shared), its
  web page only on `http://localhost:631`; `cupsd -t` checks the configuration on every run.
- **Adding a printer** that isn't found by itself: "Printers" (system-config-printer). The polkit rule
  `50-orion-hardware.rules` lets the session user add, change and remove printers and jobs without a password
  while at the computer. Print-server settings (sharing, remote admin) are left out and still ask. Nobody is
  added to the `lpadmin` group. Orion's daemon and agents don't qualify: they don't run in the active session.
- **Scanning:** "Scan documents" (Simple Scan; its launcher entry is renamed through
  `/usr/local/share/applications`, same file id). sane-airscan finds network scanners that speak eSCL
  (AirScan, announced over mDNS) without a driver; ipp-usb does the same for USB multifunction printers. Scanners
  that only speak Microsoft's WSD answer discovery on a random port the firewall doesn't open, so they aren't
  found by themselves (rare; nothing is opened for them on purpose).
- **Paper size** comes from the time zone's country: Letter in the US and its territories, Canada, Mexico, the
  Philippines, most of Central America, Chile, Colombia and Venezuela; A4 everywhere else (and for UTC). It is
  written to `/etc/papersize`, which CUPS (new queues) and the print filters read. `orion-papersize.path` updates
  it whenever the time zone changes; the first `orion-printers` run checks it once. Picking one by hand
  (`orion-printers paper a4|letter`, through `orion-papersize@.service`, allowed by the same polkit rule) stops
  the time zone from changing it; `paper auto` goes back to following it. The time zone -> country lookup is
  OS basics' (`os/basics/orion_basics.py`), with a direct read of the tz tables when that isn't installed.

### `orion-printers list --json`
```json
{"default": "Office_Laser",
 "printers": [{"name": "Office_Laser", "state": "idle", "enabled": true, "added": true,
               "description": "Office laser", "location": "Upstairs", "uri": "ipp://192.0.2.10/ipp/print",
               "default": true}],
 "cups_running": true, "paper": "letter", "paper_auto": true}
```
`state` is `idle`, `printing` (with `job`), `disabled` (with `reason`) or `unknown`. `added: false` = found on
the network, usable as is, not added by anyone (`uri` is then `null`).

## Firewall
Printers and scanners announce themselves with mDNS. Ubuntu's ufw already accepts the multicast replies
(`/etc/ufw/before.rules`: `224.0.0.251` and `ff02::fb`, UDP 5353). Some printers answer straight back to this
computer instead, so one more rule per private address range allows **UDP from port 5353 to port 5353, only from
192.168.0.0/16, 10.0.0.0/8, 172.16.0.0/12, 169.254.0.0/16 and fe80::/10** (local networks, never the internet).
Nothing else is opened: printing and scanning are outgoing connections, and nothing on OrionOS is shared or
advertised for others to print to. In the lab VM the same rule only ever sees OrionNet (10.77.0.0/24).

## Tests
- `python3 -m unittest discover -s os/hardware/tests`: the layout maker (captured `swaymsg -t get_outputs` for a
  laptop with a monitor, two identical monitors, a laptop alone), EDID sizes and scale, saving and matching
  layouts, the kanshi file, primary screen and workspace plan, the lid, lpstat reading (captured from CUPS 2.4.7)
  and paper sizes.
- `bash os/hardware/tests/test_sway_headless.sh`: a real headless Sway with two screens and OrionOS's own Sway
  config: kanshi and the watcher start, list/save/apply/primary, a third screen plugged in and out (kanshi puts the
  saved layout back), and a stand-in HUD window following the primary screen.
- `node os/hardware/tests/test_polkit_rule.js`: who the polkit rule lets do what.

## Hardware report card (`orion-check-hardware`)
Will this PC be happy with OrionOS? Run it from the USB stick ("Try Orion") before installing: graphics card and
driver, NVIDIA's driver (and its mode setting), Wi-Fi, Bluetooth and Bluetooth audio, speakers, microphone, camera,
sleep, battery health, screen size and touchpad. The answer is a green "Safe to install on this PC" or a short red
list, each with its fix. `orion-check-hardware --json` feeds Settings > This PC's hardware, the HUD's card while
trying Orion, and `--phone` (or the "Send to my phone" buttons) sends it to the paired phone. Logic:
`orion_hwcheck.py` (tests on made-up /sys trees in `tests/test_orion_hwcheck.py`).

## NVIDIA
`orion-gpu-env` (run by `orion-session`) sees NVIDIA's own driver in `/proc/modules` and starts Sway/SwayFX with
`--unsupported-gpu`; when NVIDIA draws the screen it also sets `WLR_NO_HARDWARE_CURSORS=1`, `GBM_BACKEND=nvidia-drm`
and `__GLX_VENDOR_LIBRARY_NAME=nvidia`; Intel/AMD + NVIDIA laptops keep the built-in card for the screen
(`WLR_DRM_DEVICES`, built-in card first). The NVIDIA USB image ships the driver and boots with
`nvidia-drm.modeset=1`; `orion-install-disk` installs the driver and the boot option when it finds an NVIDIA card.

