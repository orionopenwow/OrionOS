#!/bin/sh
# Screenshots of the Get apps / Share files windows under a headless Sway (software renderer), with a stand-in
# flatpak (fake_flatpak.py + shot-world.json) so no network or real apps are needed. Run as root inside a test
# chroot that has sway, grim, python3-gi, gir1.2-adw-1 and the Orion theme/fonts installed:
#   sh os/getapps/tests/shot.sh OUT_DIR [discover installed updates studio search share]
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
GA="$(cd "$HERE/.." && pwd)"
SRC="$(cd "$GA/../.." && pwd)"
OUT="${1:?output dir}"; shift
PAGES="${*:-discover installed updates studio search share}"
mkdir -p "$OUT"
W="$(mktemp -d)"
export XDG_RUNTIME_DIR="$W/run" HOME="$W/home" XDG_STATE_HOME="$W/home/.local/state" XDG_CONFIG_HOME="$W/home/.config"
mkdir -p "$XDG_RUNTIME_DIR" "$HOME/.local/state/orion/changes" && chmod 700 "$XDG_RUNTIME_DIR"
cp "$HERE/shot-world.json" "$W/world.json"
cp "$HERE/shot-history.jsonl" "$HOME/.local/state/orion/changes/apps.jsonl"
printf '#!/bin/sh\nexec python3 %s "$@"\n' "$HERE/fake_flatpak.py" > "$W/flatpak"; chmod +x "$W/flatpak"
export FAKE_FLATPAK_STATE="$W/world.json" ORION_FLATPAK="$W/flatpak" ORION_APPS_NO_NOTIFY=1 ORION_GETAPPS_NON_UNIQUE=1
export ORION_SHARE_DRY_RUN=1
WALL="$SRC/os/theme/wallpapers/orion-blackhole-4k.png"
cat > "$W/sway.conf" <<EOF
output HEADLESS-1 resolution 1600x1000 bg $WALL fill
default_border none
gaps inner 0
for_window [app_id="^local\.GetApps$"] floating enable, resize set 1180 780, move position center
for_window [app_id="^local\.ShareFiles$"] floating enable, resize set 780 560, move position center
EOF
WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 sway -c "$W/sway.conf" >"$W/sway.log" 2>&1 &
SWAY=$!
for _ in $(seq 50); do ls "$XDG_RUNTIME_DIR"/wayland-* >/dev/null 2>&1 && break; sleep 0.2; done
export WAYLAND_DISPLAY="$(basename "$(ls "$XDG_RUNTIME_DIR"/wayland-* | grep -v lock | head -n1)")"
export SWAYSOCK="$(ls "$XDG_RUNTIME_DIR"/sway-ipc.*.sock | head -n1)"
export GSK_RENDERER=cairo GDK_BACKEND=wayland GTK_A11Y=none NO_AT_BRIDGE=1
for page in $PAGES; do
  case "$page" in
    share)  python3 "$GA/share_ui.py" >"$W/$page.log" 2>&1 & ;;
    search) python3 "$GA/getapps_ui.py" --app "video" >"$W/$page.log" 2>&1 & ;;
    *)      python3 "$GA/getapps_ui.py" --page "$page" >"$W/$page.log" 2>&1 & ;;
  esac
  APP=$!
  sleep 4
  swaymsg -t get_tree | grep -o '"app_id": "[^"]*"' | sort -u | tr '\n' ' '; echo
  grim "$OUT/$page.png"
  kill "$APP" 2>/dev/null || true; wait "$APP" 2>/dev/null || true
  grep -v 'GtkGizmo' "$W/$page.log" | sed -n '1,15p'
done
kill "$SWAY" 2>/dev/null || true
rm -rf "$W"
echo "screenshots in $OUT"
