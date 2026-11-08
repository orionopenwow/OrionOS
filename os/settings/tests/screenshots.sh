#!/bin/sh
# Orion Settings: screenshot every page under a headless Sway, with the demo data (nothing on the machine changes).
#   sh os/settings/tests/screenshots.sh OUT_DIR [glass|solid] [page ...]
# Needs sway, grim, python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 and the Orion fonts. Used in a test chroot by the
# os-settings build; run it as a normal user (not root).
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:?usage: screenshots.sh OUT_DIR [glass|solid] [page ...]}"; shift
LOOK="${1:-solid}"; [ $# -gt 0 ] && shift
PAGES="${*:-network bluetooth sound displays printers power time users privacy updates appearance orion permissions}"
mkdir -p "$OUT"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-$(mktemp -d)}"
chmod 700 "$XDG_RUNTIME_DIR"
export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 GSK_RENDERER=cairo
WALL="${ORION_SHOT_WALLPAPER:-}"
CFG="$XDG_RUNTIME_DIR/sway-shots.conf"
{
  echo "xwayland disable"; echo "output HEADLESS-1 resolution 1440x900 scale 1"
  if [ -n "$WALL" ] && [ -f "$WALL" ]; then echo "output * bg $WALL fill"; else echo "output * bg #05070E solid_color"; fi
  echo 'default_border none'
  echo 'for_window [app_id="^ai.orion.Settings$"] floating enable, resize set 1100 720, move position center'
} > "$CFG"
sway -c "$CFG" > "$XDG_RUNTIME_DIR/sway.log" 2>&1 &
SWAY=$!
for _ in $(seq 1 50); do ls "$XDG_RUNTIME_DIR"/wayland-* >/dev/null 2>&1 && break; sleep 0.2; done
export WAYLAND_DISPLAY="$(basename "$(ls "$XDG_RUNTIME_DIR"/wayland-* | grep -v lock | head -n1)")"
export SWAYSOCK="$(ls "$XDG_RUNTIME_DIR"/sway-ipc.* | head -n1)"
swaymsg -t get_outputs -r > "$OUT/sway-outputs-headless.json" 2>/dev/null || true
for p in $PAGES; do
  PYTHONPATH="$HERE" python3 -m orion_settings --demo --"$LOOK" --page "$p" > "$OUT/$p.log" 2>&1 &
  APP=$!
  sleep "${ORION_SHOT_WAIT:-4}"
  grim "$OUT/$LOOK-$p.png"
  kill "$APP" 2>/dev/null; wait "$APP" 2>/dev/null || true
done
kill "$SWAY" 2>/dev/null || true
echo "shots in $OUT"
