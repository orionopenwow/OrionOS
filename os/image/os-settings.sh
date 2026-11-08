#!/usr/bin/env bash
# OrionOS Settings window (docs/ORIONOS_COMPLETE_PLAN.md Part 1 "Settings window", step 2): a GTK 4 + libadwaita
# app in the Orion Glass style, replacing the text menu (still there: orion-settings --text).
#
#   bash os/image/os-settings.sh        (as root: inside the image chroot, on the lab VM, or on an installed OrionOS)
#
# Called from os/lab/provision.sh step 6. Safe to re-run.
#   /opt/orion/settings/                 the app (orion_settings/, style.css) and its root helpers (helpers/)
#   /usr/local/bin/orion-settings-app    the launcher; orion-settings (Ctrl+Alt+S, Super+,) opens the window
#   /usr/share/applications/ai.orion.Settings.desktop   "Settings" in the app launcher
#   /usr/share/polkit-1/actions/ai.orion.settings.policy + /etc/polkit-1/rules.d/50-orion-settings.rules
#       the lid/power-button and camera helpers run through pkexec; the session user at the computer may use them
#   Camera off by default (after-1.0 plan §6): set on the first install only; Settings > Privacy turns it on.
#   ORION_USER=orion            the session user the polkit rule names
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$SRC/os/settings"
ORION_USER="${ORION_USER:-orion}"
export DEBIAN_FRONTEND=noninteractive

say() { printf '\033[1;36m[os-settings] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

say "Packages: GTK 4, libadwaita, the network/Bluetooth/sound/power tools Settings talks to"
PKGS="python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pkexec polkitd libglib2.0-bin gsettings-desktop-schemas
      wireplumber kmod"
missing=""
for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
if [ -n "$missing" ]; then
  apt-get update -q
  # shellcheck disable=SC2086
  apt-get install -y -q --no-install-recommends $missing
fi

say "The app -> /opt/orion/settings"
rm -rf /opt/orion/settings
install -d -m 755 /opt/orion/settings /opt/orion/settings/helpers /opt/orion/bin
cp -r "$S/orion_settings" /opt/orion/settings/
find /opt/orion/settings -name __pycache__ -prune -exec rm -rf {} +
install -m 644 "$S/style.css" /opt/orion/settings/style.css
install -d -m 755 /opt/orion/settings/tests
cp -r "$S/tests/samples" /opt/orion/settings/tests/samples    # sample outputs for orion-settings-app --demo
install -m 755 -o root -g root "$S/helpers/orion-settings-power" "$S/helpers/orion-settings-camera" \
  "$S/helpers/orion-settings-admin" /opt/orion/settings/helpers/
install -m 755 "$S/orion-settings-app" /opt/orion/bin/orion-settings-app
ln -sf /opt/orion/bin/orion-settings-app /usr/local/bin/orion-settings-app
# orion-settings itself (now: open the window, --text = the menu) comes with the other commands (provision step 6).
if [ -f "$SRC/os/lab/bin/orion-settings" ]; then
  install -m 755 "$SRC/os/lab/bin/orion-settings" /opt/orion/bin/orion-settings
  ln -sf /opt/orion/bin/orion-settings /usr/local/bin/orion-settings
fi
chown -R root:root /opt/orion/settings
chmod -R a+rX,go-w /opt/orion/settings
install -m 644 "$S/ai.orion.Settings.desktop" /usr/share/applications/ai.orion.Settings.desktop

say "Privileged steps: polkit action and rule for the two helpers"
install -d -m 755 /usr/share/polkit-1/actions /etc/polkit-1/rules.d
install -m 644 "$S/polkit/ai.orion.settings.policy" /usr/share/polkit-1/actions/ai.orion.settings.policy
# H5: admin jobs (your password, how OrionOS starts, updates, install, vault) through pkexec: always the admin
# password, asked for in the graphical password window (orion-polkit-agent). No rule loosens these.
install -m 644 "$S/polkit/ai.orion.admin.policy" /usr/share/polkit-1/actions/ai.orion.admin.policy
sed "s/subject.user == \"orion\"/subject.user == \"$ORION_USER\"/" "$S/polkit/50-orion-settings.rules" \
  > /etc/polkit-1/rules.d/50-orion-settings.rules
chmod 644 /etc/polkit-1/rules.d/50-orion-settings.rules

say "Camera: off by default (first install only; an earlier choice is kept)"
/opt/orion/settings/helpers/orion-settings-camera default

say "Done. Ctrl+Alt+S opens Settings; orion-settings --text is the old menu."
