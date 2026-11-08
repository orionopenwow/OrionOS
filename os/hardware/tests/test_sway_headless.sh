#!/bin/bash
# End-to-end test of orion-displays in a real (headless) Sway with two screens, using OrionOS's own Sway config:
# the session's exec line starts kanshi and the screen watcher; then list / save / apply / primary, a third
# screen plugged in and out, and workspace 1 (a stand-in HUD window, app_id "orion") following the primary screen.
#
#   bash os/hardware/tests/test_sway_headless.sh [SWAY_CONFIG]
#
# Needs sway, kanshi, foot, jq and python3, and orion-displays on PATH (os/image/os-hardware.sh installs it).
# Run it as an ordinary user: it uses a throwaway HOME and XDG_RUNTIME_DIR, and never touches a real session.
# The checks are strings run by eval, so single quotes are on purpose.
# shellcheck disable=SC2016,SC2034,SC2012
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
CONF="${1:-$HERE/../../lab/sway-config}"
T="$(mktemp -d)"
export HOME="$T/home" XDG_RUNTIME_DIR="$T/run" XDG_CONFIG_HOME="$T/home/.config"
mkdir -p "$HOME" "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
export WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_HEADLESS_OUTPUTS=2 WLR_LIBINPUT_NO_DEVICES=1
unset SWAYSOCK WAYLAND_DISPLAY
# notifications are written down instead of shown
mkdir -p "$T/stub"
printf '#!/bin/sh\necho "$*" >> "%s/notify.log"\n' "$T" > "$T/stub/notify-send"; chmod +x "$T/stub/notify-send"
export PATH="$T/stub:$PATH"
PASS=0; FAIL=0
ok()   { PASS=$((PASS + 1)); printf 'ok   %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf 'FAIL %s\n' "$1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
wait_for() { local i; for i in $(seq 1 "${2:-40}"); do eval "$1" && return 0; sleep 0.25; done; return 1; }

sway -c "$CONF" > "$T/sway.log" 2>&1 &
SWAY_PID=$!
# kanshi and the watcher end with the session (their Wayland/IPC connection closes)
cleanup() { swaymsg exit >/dev/null 2>&1; sleep 1; kill "$SWAY_PID" 2>/dev/null; rm -rf "$T"; }
trap cleanup EXIT
wait_for '[ -S "$(ls "$XDG_RUNTIME_DIR"/sway-ipc.*.sock 2>/dev/null | head -n1)" ]' || { cat "$T/sway.log"; echo "sway didn't start"; exit 1; }
SWAYSOCK="$(ls "$XDG_RUNTIME_DIR"/sway-ipc.*.sock | head -n1)"; export SWAYSOCK

ws_out() { swaymsg -t get_workspaces -r | jq -r ".[] | select(.num == $1) | .output"; }
hud_out() { swaymsg -t get_tree -r | jq -r '.nodes[] | select(.type=="output") | . as $o | [.. | objects | select(.app_id? == "orion")] | if length > 0 then $o.name else empty end'; }
pos() { swaymsg -t get_outputs -r | jq -r ".[] | select(.name == \"$1\") | \"\(.rect.x),\(.rect.y)\""; }
store() { jq -r "$1" "$HOME/.config/orion/displays.json"; }

# Without a systemd user manager the session's exec line falls back to: kanshi & exec orion-displays watch
# (the shell becomes the watcher, with kanshi as its child). Sway double-forks what it runs, so this test's own
# processes are told apart from any other session by their XDG_RUNTIME_DIR.
ours() { tr '\0' '\n' < "/proc/$1/environ" 2>/dev/null | grep -qx "XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"; }
session_ok() {
  local w
  for w in $(pgrep -f "orion-displays watch"); do
    ours "$w" && pgrep -P "$w" -x kanshi >/dev/null && return 0
  done
  return 1
}
check "the session config starts kanshi and the screen watcher" 'wait_for session_ok'
check "the HUD stand-in is on screen" 'wait_for "[ -n \"\$(hud_out)\" ]"'
check "a layout was made for the two screens" 'wait_for "[ -f \$HOME/.config/orion/displays.json ] && [ \"\$(store \".profiles | length\")\" = 1 ]"'
check "  ... automatic, both screens, side by side" \
  '[ "$(store ".profiles[0].auto")" = true ] && [ "$(store "[.profiles[0].outputs[].criteria] | join(\" \")")" = "HEADLESS-1 HEADLESS-2" ] && [ "$(store ".profiles[0].outputs[1].position | join(\",\")")" = "1280,0" ]'
check "kanshi's file was written from it" 'grep -q "output HEADLESS-2 enable mode 1280x720 position 1280,0 scale 1" "$HOME/.config/kanshi/config"'

J="$(orion-displays list --json)"
check "list --json: two screens, HEADLESS-1 primary" \
  '[ "$(echo "$J" | jq ".outputs | length")" = 2 ] && [ "$(echo "$J" | jq -r .primary)" = HEADLESS-1 ]'
check "list --json: the current layout and workspaces per screen" \
  '[ "$(echo "$J" | jq -r .current_profile)" = headless-1-headless-2 ] && [ "$(echo "$J" | jq -c ".outputs[1].workspaces")" = "[2,3,4,5]" ]'
check "workspace 1 (HUD) on the primary screen" 'wait_for "[ \"\$(ws_out 1)\" = HEADLESS-1 ] && [ \"\$(hud_out)\" = HEADLESS-1 ]"'

swaymsg "workspace number 2" >/dev/null; swaymsg "exec foot --app-id test-app sleep 1000" >/dev/null
check "workspace 2 opens on the second screen (the HUD stays in view)" 'wait_for "[ \"\$(ws_out 2)\" = HEADLESS-2 ]"'

orion-displays primary HEADLESS-2 >/dev/null
check "primary -> HEADLESS-2: workspace 1 and the HUD move there" '[ "$(ws_out 1)" = HEADLESS-2 ] && [ "$(hud_out)" = HEADLESS-2 ]'
check "  ... and workspace 2 moves to the other screen" '[ "$(ws_out 2)" = HEADLESS-1 ]'
check "  ... the HUD is still full screen" \
  '[ "$(swaymsg -t get_tree -r | jq "[.. | objects | select(.app_id? == \"orion\") | .fullscreen_mode] | first")" = 1 ]'
orion-displays primary auto >/dev/null
check "primary auto: back to the leftmost screen" '[ "$(ws_out 1)" = HEADLESS-1 ] && [ "$(hud_out)" = HEADLESS-1 ]'

# Arrange by hand (what wdisplays does), then save it
swaymsg "output HEADLESS-2 position 0 0; output HEADLESS-1 position 1280 0" >/dev/null
sleep 1.5
orion-displays save "Desk at home" >/dev/null
check "save: a named layout replaces the automatic one" \
  '[ "$(store "[.profiles[].name] | join(\" \")")" = desk-at-home ] && [ "$(store ".profiles[0].auto")" = false ]'
check "  ... with the arranged positions" \
  '[ "$(store "[.profiles[0].outputs[] | \"\(.criteria)@\(.position|join(\",\"))\"] | join(\" \")")" = "HEADLESS-2@0,0 HEADLESS-1@1280,0" ]'
check "  ... and kanshi has it" 'grep -q "^profile desk-at-home {" "$HOME/.config/kanshi/config" && grep -q "output HEADLESS-1 enable mode 1280x720 position 1280,0" "$HOME/.config/kanshi/config"'
check "the primary screen follows the layout (leftmost is now HEADLESS-2)" 'wait_for "[ \"\$(ws_out 1)\" = HEADLESS-2 ]"'

swaymsg "output HEADLESS-1 position 0 0; output HEADLESS-2 position 1280 0" >/dev/null
sleep 1.5
orion-displays apply desk-at-home >/dev/null
check "apply: the saved layout comes back" '[ "$(pos HEADLESS-2)" = 0,0 ] && [ "$(pos HEADLESS-1)" = 1280,0 ]'
check "apply: an unknown layout is refused" '! orion-displays apply nope 2>/dev/null'

# Plug in a third screen
swaymsg create_output >/dev/null
check "a new screen gets a layout of its own" 'wait_for "[ \"\$(store \".profiles | length\")\" = 2 ]" 40'
check "  ... and a notification offers to arrange it" 'wait_for "grep -q \"New screen set up\" \"$T/notify.log\""'
check "  ... placed to the right, the saved layout kept" \
  '[ "$(store "[.profiles[] | select(.auto) | .outputs[] | .criteria] | join(\" \")")" = "HEADLESS-1 HEADLESS-2 HEADLESS-3" ] && [ "$(store ".profiles[0].name")" = desk-at-home ]'
plan() { orion-displays list --json | jq -c '[.outputs[] | {(.name): .workspaces}] | add'; }
check "  ... workspaces shared out: 2-5 and 6-9 on the two other screens" \
  'wait_for "[ \"\$(plan)\" = \"{\\\"HEADLESS-1\\\":[1],\\\"HEADLESS-2\\\":[2,3,4,5],\\\"HEADLESS-3\\\":[6,7,8,9]}\" ] && [ \"\$(ws_out 2)\" = HEADLESS-2 ]"'
check "  ... the HUD stays on the primary screen" '[ "$(hud_out)" = "$(orion-displays list --json | jq -r .primary)" ]'

# Unplug it again: kanshi puts the saved two-screen layout back
swaymsg output HEADLESS-3 unplug >/dev/null
check "unplugged: kanshi restores the saved layout" 'wait_for "[ \"\$(pos HEADLESS-2)\" = 0,0 ] && [ \"\$(pos HEADLESS-1)\" = 1280,0 ]" 40'
check "  ... workspace 1 back on the primary screen" 'wait_for "[ \"\$(ws_out 1)\" = HEADLESS-2 ] && [ \"\$(hud_out)\" = HEADLESS-2 ]"'

# A screen switched off by hand (as the lid does) stays off: kanshi doesn't fight it
swaymsg output HEADLESS-1 disable >/dev/null
sleep 3
check "a screen switched off stays off" '[ "$(swaymsg -t get_outputs -r | jq ".[] | select(.name==\"HEADLESS-1\") | .active")" = false ]'
check "  ... and everything is on the one left" '[ "$(ws_out 1)" = HEADLESS-2 ] && [ "$(ws_out 2)" = HEADLESS-2 ]'
swaymsg output HEADLESS-1 enable >/dev/null

echo "$PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
