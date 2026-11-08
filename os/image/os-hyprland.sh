#!/usr/bin/env bash
# OrionOS: the optional Hyprland session (after-1.0 plan §3, Phase 4; Gravity mode).
#
#   ORION_WITH_HYPRLAND=1 bash os/lab/provision.sh     (provisioning runs this only when you choose it)
#   bash os/image/os-hyprland.sh                        (as root, on the lab VM or an installed OrionOS)
#
# The image and the default session stay Sway/SwayFX. This adds Hyprland next to it:
#   * the hyprland package (and swaybg for the wallpaper, wlrctl for clicks Orion makes), from the Ubuntu archive only;
#   * Orion's Hyprland config as /etc/orion/hypr/hyprland.conf and the Gravity shader under /opt/orion/desktop/hyprland;
#   * nothing switches over by itself: pick it with `orion-gravity session hyprland` (or Settings > Keyboard &
#     windows), then log out and back in. `orion-gravity session sway` goes back.
# If the Ubuntu archive has no Hyprland package for this release, it says so and changes nothing. Safe to re-run.
#   ORION_USER=orion
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OS="$SRC/os"
ORION_USER="${ORION_USER:-orion}"
export DEBIAN_FRONTEND=noninteractive

say() { printf '\033[1;36m[os-hyprland] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

say "Packages: hyprland, swaybg, wlrctl (Ubuntu archive)"
if ! command -v Hyprland >/dev/null 2>&1; then
  apt-get update -q
  if ! apt-cache show hyprland >/dev/null 2>&1; then
    echo "!! This Ubuntu release has no hyprland package in its archive. The Sway session is unchanged."
    echo "   (Gravity mode needs Hyprland; Orion doesn't add outside package sources for it.)"
    exit 0
  fi
  apt-get install -y -q --no-install-recommends hyprland || { echo "!! Hyprland didn't install; the Sway session is unchanged."; exit 0; }
fi
for p in swaybg wlrctl; do
  dpkg -s "$p" >/dev/null 2>&1 || apt-get install -y -q --no-install-recommends "$p" || echo "!! $p didn't install (optional)"
done

say "Orion's Hyprland config and the Gravity shader"
install -d -m 755 /etc/orion/hypr /opt/orion/desktop/hyprland
install -m 644 "$OS/desktop/hyprland/hyprland.conf" /etc/orion/hypr/hyprland.conf
install -m 644 "$OS/desktop/hyprland/gravity.frag" /opt/orion/desktop/hyprland/gravity.frag
# Hyprland before 0.45 has no shadow { } block: use its older drop_shadow settings instead.
ver="$(Hyprland --version 2>/dev/null | sed -n 's/^Hyprland \([0-9]*\)\.\([0-9]*\).*/\1 \2/p' | head -n1)"
if [ -n "$ver" ] && [ "${ver%% *}" -eq 0 ] && [ "${ver##* }" -lt 45 ]; then
  sed -i '/# orion:shadow-block (/,/# orion:shadow-block-end/c\    drop_shadow = true\n    shadow_range = 28\n    shadow_render_power = 3\n    col.shadow = rgba(05070ecc)' /etc/orion/hypr/hyprland.conf
  say "Hyprland ${ver/ /.}: older shadow settings"
fi

if id "$ORION_USER" >/dev/null 2>&1; then
  H="$(getent passwd "$ORION_USER" | cut -d: -f6)"
  install -d -o "$ORION_USER" -g "$ORION_USER" "$H/.config/orion" "$H/.config/orion/hypr.d"
  for f in keyboard.conf gravity.conf user.conf; do
    [ -f "$H/.config/orion/hypr.d/$f" ] || install -m 644 -o "$ORION_USER" -g "$ORION_USER" /dev/null "$H/.config/orion/hypr.d/$f"
  done
fi
say "Done. The next login still starts Sway. Try Hyprland: orion-gravity session hyprland, then log out and in."
