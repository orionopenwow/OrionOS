#!/usr/bin/env bash
# OrionOS desktop for people coming from Windows (review H5 + H13, cheap feature 5).
#
#   bash os/image/os-desktop.sh     (as root: in the image chroot, on the lab VM, or on an installed OrionOS)
#
# Called from os/lab/provision.sh step 6. Safe to re-run.
#   * a graphical admin-password window (polkit agent: policykit-1-gnome, else lxpolkit), started by sway-config
#     (orion-polkit-agent); without it pkexec from a window is simply refused
#   * window handling: title bars with a window menu, Alt+Tab, Alt+F4 (orion-windows), keyboard layout
#     (orion-keyboard), the shortcut overlay (orion-shortcuts: hold Super or Super+/), a small tray (orion-tray)
#   * /opt/orion/desktop: the Python logic for those (os/desktop/*.py) and the tray's look (os/desktop/waybar)
#   ORION_USER=orion
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OS="$SRC/os"
ORION_USER="${ORION_USER:-orion}"
export DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1

say() { printf '\033[1;36m[os-desktop] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

say "Packages: polkit agent, tray (waybar), shortcut overlay (GTK 3 layer shell), keyboard layouts"
PKGS="waybar gir1.2-gtk-3.0 gir1.2-gtklayershell-0.1 python3-gi xkb-data wofi"
PKGS="$PKGS python3-gi-cairo python3-pyatspi at-spi2-core"   # [v1gaps] Guide Mode ring (cairo) + finding buttons by name (AT-SPI)
missing=""
for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
if ! dpkg -s policykit-1-gnome >/dev/null 2>&1 && ! dpkg -s lxpolkit >/dev/null 2>&1; then missing="$missing policykit-1-gnome"; fi
if [ -n "$missing" ]; then
  apt-get update -q
  # shellcheck disable=SC2086
  apt-get install -y -q --no-install-recommends $missing || {
    echo "!! some desktop packages didn't install:$missing"
    # the GNOME agent's package name has changed between releases: LXDE's agent does the same job
    dpkg -s policykit-1-gnome >/dev/null 2>&1 || apt-get install -y -q --no-install-recommends lxpolkit || true
  }
fi
# Ubuntu's waybar package switches on its own full status bar (waybar.service) for every graphical session. Orion
# runs waybar only as its small tray (orion-tray.service), so the stock bar is masked: otherwise a second, default bar
# sits across the top of every session.
rm -f /etc/systemd/user/graphical-session.target.wants/waybar.service
ln -sfn /dev/null /etc/systemd/user/waybar.service
# Same for mako: Ubuntu's mako.service starts with every graphical session and takes the notification name first, so
# Orion's own orion-mako.service (same program, same settings, but supervised with the session) fails and turns the
# health light red. Mask the stock one; D-Bus can still start mako on demand if orion-mako isn't running.
rm -f /etc/systemd/user/graphical-session.target.wants/mako.service
ln -sfn /dev/null /etc/systemd/user/mako.service

say "The logic -> /opt/orion/desktop"
install -d -m 755 /opt/orion/desktop /opt/orion/desktop/waybar /opt/orion/bin
install -m 644 "$OS/desktop/orion_windows.py" "$OS/desktop/orion_keyboard.py" "$OS/desktop/orion_shortcuts.py" /opt/orion/desktop/
# [look] the compositor adapter (Sway, or the optional Hyprland session) and Gravity mode's logic + shader
install -m 644 "$OS/desktop/orion_compositor.py" "$OS/desktop/orion_gravity.py" /opt/orion/desktop/
install -d -m 755 /opt/orion/desktop/hyprland
install -m 644 "$OS/desktop/hyprland/gravity.frag" "$OS/desktop/hyprland/hyprland.conf" /opt/orion/desktop/hyprland/
install -m 644 "$OS/desktop/orion_guide_ring.py" "$OS/desktop/orion_a11y.py" /opt/orion/desktop/   # [v1gaps] Guide Mode ring, screen reader check
install -m 644 "$OS/desktop/waybar/config.jsonc" "$OS/desktop/waybar/style.css" /opt/orion/desktop/waybar/
for c in orion-windows orion-keyboard orion-shortcuts orion-polkit-agent orion-tray orion-gravity orion-guide-ring orion-a11y-check; do  # [look] orion-gravity, [v1gaps] guide ring + a11y check
  install -m 755 "$OS/lab/bin/$c" "/opt/orion/bin/$c"
  ln -sf "/opt/orion/bin/$c" "/usr/local/bin/$c"
done

# [v1gaps] Guide Mode finds buttons by name through AT-SPI: give Orion's own Python just pyatspi and gi from the
# system (a .pth at the end of its search path, so nothing the venv installed is shadowed).
if [ -d /opt/orion/venv ] && [ -d /usr/lib/python3/dist-packages/pyatspi ]; then
  install -d -m 755 /opt/orion/a11y-py
  for m in pyatspi gi; do ln -sfn "/usr/lib/python3/dist-packages/$m" "/opt/orion/a11y-py/$m"; done
  for sp in /opt/orion/venv/lib/python3*/site-packages; do echo /opt/orion/a11y-py > "$sp/orion-a11y.pth"; done
fi

# Which password window this computer uses (sway-config floats and centres it).
say "Admin password window: $(for a in /usr/lib/policykit-1-gnome/polkit-gnome-authentication-agent-1 /usr/bin/lxpolkit; do [ -x "$a" ] && { echo "$a"; break; }; done)"

if id "$ORION_USER" >/dev/null 2>&1; then
  H="$(getent passwd "$ORION_USER" | cut -d: -f6)"
  install -d -o "$ORION_USER" -g "$ORION_USER" "$H/.config" "$H/.config/orion" "$H/.config/orion/sway.d"
fi
say "Done. Alt+Tab switches windows, hold Super for the shortcuts, Settings > Keyboard & windows for the rest."
