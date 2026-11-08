#!/usr/bin/env bash
# OrionOS everyday basics (docs/ORIONOS_COMPLETE_PLAN.md Part 1): clock and time zone, screenshots, clipboard
# history, night light. What each piece does and why: os/basics/README.md.
#
#   bash os/image/os-basics.sh        (as root: inside the image chroot, on the lab VM, or on an installed OrionOS)
#
# Called from os/lab/provision.sh step 6. Safe to re-run: every step checks before it changes anything.
#   ORION_USER=orion            the session user whose config is set up (skipped if that user doesn't exist yet)
#   ORION_SWAPPY_BUILD=0        don't build the markup tool (Ctrl+Print then saves the area without markup)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OS="$SRC/os"
ORION_USER="${ORION_USER:-orion}"
SWAPPY_VERSION=1.5.1
SWAPPY_COMMIT=a996b2cec65aadd04ded780f141b02c5bdfbeb32     # the v1.5.1 tag (MIT licence)
SWAPPY_REPO=https://github.com/jtheoof/swappy.git
SWAPPY_STAMP=/usr/local/share/orion/swappy.version
WORK="${ORION_BASICS_WORK:-/var/tmp/orion-basics-build}"
export DEBIAN_FRONTEND=noninteractive

say() { printf '\033[1;36m[os-basics] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
# A booted system (not the image chroot, where timedatectl would reach the build host's clock, or nothing).
live() { ! systemd-detect-virt -q --chroot 2>/dev/null && [ -d /run/systemd/system ]; }

say "Packages: screenshots (grim, slurp), clipboard (wl-clipboard, cliphist), night light (wlsunset), network time"
PKGS="grim slurp wl-clipboard cliphist wlsunset wtype wofi libnotify-bin jq python3 tzdata fonts-font-awesome libgtk-3-0t64"
PKGS="$PKGS restic"   # Backups with restore drills (apps/daemon/backup.py)
# systemd-timesyncd conflicts with chrony and ntpsec: if one of those already keeps the time, keep it.
if ! dpkg -l chrony ntpsec ntp 2>/dev/null | grep -q '^ii'; then PKGS="$PKGS systemd-timesyncd"; fi
missing=""
for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
if [ -n "$missing" ]; then
  apt-get update -q
  # shellcheck disable=SC2086
  apt-get install -y -q --no-install-recommends $missing
fi

say "Markup tool (swappy $SWAPPY_VERSION in Orion's colours)"
compile_swappy() {
  git -c advice.detachedHead=false clone -q --depth 1 --branch "v$SWAPPY_VERSION" "$SWAPPY_REPO" "$WORK/swappy" \
    || { echo "!! couldn't download swappy from GitHub"; return 1; }
  [ "$(git -C "$WORK/swappy" rev-parse HEAD)" = "$SWAPPY_COMMIT" ] || { echo "!! swappy v$SWAPPY_VERSION: tag moved"; return 1; }
  ( cd "$WORK/swappy" || exit 1
    # Orion's colours on the three colour buttons (red, green, blue upstream) and the custom colour.
    sed -i 's/action_update_color_state(state, 1, 0, 0, 1, false)/action_update_color_state(state, 1, 0.361, 0.541, 1, false)/
            s/action_update_color_state(state, 0, 1, 0, 1, false)/action_update_color_state(state, 0, 0.898, 1, 1, false)/
            s/action_update_color_state(state, 0, 0, 1, 1, false)/action_update_color_state(state, 0.486, 0.361, 1, 1, false)/
            s/state->settings.g = 0;/state->settings.g = 0.361;/; s/state->settings.b = 0;/state->settings.b = 0.541;/' src/application.c
    sed -i 's/rgb(255, 0, 0)/rgb(255, 92, 138)/; s/rgb(0, 255, 0)/rgb(0, 229, 255)/; s/rgb(0, 0, 255)/rgb(124, 92, 255)/
            s/font-family: "FontAwesome 5 Free Solid";/font-family: "FontAwesome 5 Free Solid", "FontAwesome";/' res/style/swappy.css
    sed -i 's/rgb(193,125,17)/rgb(255,198,107)/' res/swappy.glade
    { [ "$(grep -c '0.361\|0.898' src/application.c)" -ge 7 ] && grep -q 'rgb(124, 92, 255)' res/style/swappy.css \
      && grep -q 'rgb(255,198,107)' res/swappy.glade; } || { echo "!! swappy colour patch didn't apply"; exit 1; }
    meson setup _b --prefix=/usr/local --buildtype=release -Dwerror=false -Dman-pages=disabled >"$WORK/meson.log" 2>&1 \
      || { tail -30 "$WORK/meson.log"; exit 1; }
    ninja -C _b >>"$WORK/meson.log" 2>&1 || { tail -30 "$WORK/meson.log"; exit 1; }
    ninja -C _b install >>"$WORK/meson.log" 2>&1 || { tail -30 "$WORK/meson.log"; exit 1; }
  ) || return 1
  install -d /usr/local/share/orion /usr/local/share/doc/swappy
  install -m 644 "$WORK/swappy/LICENSE" /usr/local/share/doc/swappy/LICENSE        # MIT
  strip --strip-unneeded /usr/local/bin/swappy 2>/dev/null || true
  echo "$SWAPPY_VERSION-orion1" > "$SWAPPY_STAMP"
}
build_swappy() {
  # Ubuntu 24.04 has no swappy package, so it's built from the pinned upstream release. Whatever happens, the
  # -dev packages and build tools this adds are removed again afterwards.
  local deps="build-essential meson ninja-build pkg-config gettext git ca-certificates libgtk-3-dev" built=0 added
  dpkg-query -W -f='${Package}\n' | sort > /tmp/orion-basics-pkgs-before
  rm -rf "$WORK"; mkdir -p "$WORK"
  # shellcheck disable=SC2086
  if apt-get update -q && apt-get install -y -q --no-install-recommends $deps && compile_swappy; then built=1; fi
  if [ "$built" = 1 ]; then   # keep the runtime libraries swappy links to
    for so in $(ldd /usr/local/bin/swappy | awk '$3 ~ /^\//{print $3}'); do
      dpkg -S "$(readlink -f "$so")" 2>/dev/null || dpkg -S "$so" 2>/dev/null || true
    done | cut -d: -f1 | sort -u | xargs -r apt-mark manual >/dev/null
  fi
  dpkg-query -W -f='${Package}\n' | sort > /tmp/orion-basics-pkgs-after
  added="$(comm -13 /tmp/orion-basics-pkgs-before /tmp/orion-basics-pkgs-after | grep -E -- '-dev$|-dev-bin$|^(meson|ninja-build|build-essential|gettext)$' || true)"
  # shellcheck disable=SC2086
  [ -z "$added" ] || apt-get purge -y -q $added >/dev/null || true
  apt-get autoremove -y -q >/dev/null || true
  rm -f /tmp/orion-basics-pkgs-before /tmp/orion-basics-pkgs-after
  rm -rf "$WORK"
  [ "$built" = 1 ] || return 1
  if ldd /usr/local/bin/swappy | grep -q 'not found'; then echo "!! swappy lost a library in the clean-up"; return 1; fi
}
if command -v swappy >/dev/null && { [ ! -f "$SWAPPY_STAMP" ] || [ "$(cat "$SWAPPY_STAMP")" = "$SWAPPY_VERSION-orion1" ]; }; then
  say "swappy already installed ($(cat "$SWAPPY_STAMP" 2>/dev/null || echo 'from Ubuntu'))"
elif apt-cache show swappy >/dev/null 2>&1; then
  apt-get install -y -q --no-install-recommends swappy               # a later Ubuntu release that ships it
elif [ "${ORION_SWAPPY_BUILD:-1}" = 1 ]; then
  build_swappy || echo "!! swappy wasn't built; Ctrl+Print saves the area without markup"
fi

say "Orion's basics commands and helper"
install -d -m 755 /opt/orion/basics /opt/orion/bin
install -m 644 "$OS/basics/orion_basics.py" /opt/orion/basics/orion_basics.py
install -m 644 "$OS/basics/orion_default_apps.py" /opt/orion/basics/orion_default_apps.py   # "Try with Craft" switch
for c in orion-timezone orion-screenshot orion-clipboard orion-nightlight; do
  install -m 755 "$OS/lab/bin/$c" "/opt/orion/bin/$c"
  ln -sf "/opt/orion/bin/$c" "/usr/local/bin/$c"
done

say "Clock: network time on, hardware clock in UTC, time zone chosen at first start"
install -d -m 755 /etc/polkit-1/rules.d
sed "s/subject.user == \"orion\"/subject.user == \"$ORION_USER\"/" "$OS/basics/50-orion-basics.rules" \
  > /etc/polkit-1/rules.d/50-orion-basics.rules
chmod 644 /etc/polkit-1/rules.d/50-orion-basics.rules
if dpkg -s systemd-timesyncd >/dev/null 2>&1; then systemctl enable systemd-timesyncd.service >/dev/null 2>&1 || true; fi
# Linux keeps the hardware clock in UTC. Windows keeps it in local time, so on a dual-boot PC Windows' clock is off
# by your UTC offset until it syncs; the fix belongs on the Windows side (os/basics/README.md), not here.
if live && command -v timedatectl >/dev/null; then
  timedatectl set-local-rtc 0 >/dev/null 2>&1 || true
  timedatectl set-ntp true >/dev/null 2>&1 || true
elif [ -f /etc/adjtime ]; then
  sed -i '3s/^LOCAL$/UTC/' /etc/adjtime
fi

say "Session user's settings ($ORION_USER)"
seed_home() {  # home owner
  local h="$1" o="$2"
  install -d -o "$o" -g "$o" "$h/.config" "$h/.config/swappy" "$h/.config/orion" "$h/Pictures" "$h/Pictures/Screenshots"
  # Your own changes to it are kept on a re-run.
  [ -f "$h/.config/swappy/config" ] || install -m 644 -o "$o" -g "$o" "$OS/basics/swappy-config" "$h/.config/swappy/config"
}
seed_home /etc/skel root
if id "$ORION_USER" >/dev/null 2>&1; then
  H="$(getent passwd "$ORION_USER" | cut -d: -f6)"
  [ -d "$H" ] && seed_home "$H" "$ORION_USER"
  # The Screenshots folder in the file manager's side bar, next to Projects and Captures.
  B="$H/.config/gtk-3.0/bookmarks"
  if [ -d "$H/.config/gtk-3.0" ] && ! grep -qs "Pictures/Screenshots" "$B"; then
    printf 'file://%s/Pictures/Screenshots Screenshots\n' "$H" >> "$B"; chown "$ORION_USER:$ORION_USER" "$B"
  fi
else
  echo "   ($ORION_USER doesn't exist yet: new users get the settings from /etc/skel)"
fi
say "Done."
