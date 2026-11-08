#!/bin/sh
# Tests for the OS basics commands' non-GUI paths, with stand-ins for grim, cliphist, wl-paste and timedatectl.
#   sh os/basics/tests/test_scripts.sh
# Test secrets are joined from pieces at runtime ([fake-secrets]).
# The checks are strings run by eval, so single quotes are on purpose.
# shellcheck disable=SC2016,SC2034
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$(cd "$HERE/../../lab/bin" && pwd)"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
export HOME="$T/home" XDG_RUNTIME_DIR="$T/run" XDG_CACHE_HOME="$T/cache"
export ORION_BASICS_LIB="$HERE/../orion_basics.py"
mkdir -p "$HOME" "$XDG_RUNTIME_DIR" "$T/stub"
PASS=0; FAIL=0
ok()   { PASS=$((PASS + 1)); printf 'ok   %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf 'FAIL %s\n' "$1"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# ---- stand-ins
cat > "$T/stub/cliphist" <<'EOF'
#!/bin/sh
case "$*" in *store*) cat >> "$XDG_RUNTIME_DIR/stored"; printf '\n--\n' >> "$XDG_RUNTIME_DIR/stored"; echo "$*" > "$XDG_RUNTIME_DIR/cliphist-args" ;;
  wipe) : > "$XDG_RUNTIME_DIR/stored" ;; list) cat "$XDG_RUNTIME_DIR/stored" 2>/dev/null ;; esac
EOF
cat > "$T/stub/wl-paste" <<'EOF'
#!/bin/sh
[ "$1" = --list-types ] && cat "$XDG_RUNTIME_DIR/types" 2>/dev/null
EOF
cat > "$T/stub/timedatectl" <<'EOF'
#!/bin/sh
echo "$*" >> "$XDG_RUNTIME_DIR/timedatectl"
EOF
for s in notify-send wl-copy; do printf '#!/bin/sh\ncat >/dev/null 2>&1 &\nexit 0\n' > "$T/stub/$s"; done
chmod +x "$T/stub/"*
export PATH="$T/stub:$PATH"

# ---- screenshots: names
export ORION_SCREENSHOT_DIR="$T/shots"
mkdir -p "$ORION_SCREENSHOT_DIR"
p1=$("$BIN/orion-screenshot" path)
check "screenshot name is Screenshot_YYYY-MM-DD_HH-MM-SS.png in the folder" \
  'echo "$p1" | grep -Eq "^$T/shots/Screenshot_[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2}\.png$"'
cat > "$T/stub/date" <<'EOF'
#!/bin/sh
echo 2026-10-16_09-30-00
EOF
chmod +x "$T/stub/date"
: > "$T/shots/Screenshot_2026-10-16_09-30-00.png"; : > "$T/shots/Screenshot_2026-10-16_09-30-00_2.png"
check "two in the same second get _2, _3" \
  '[ "$("$BIN/orion-screenshot" path)" = "$T/shots/Screenshot_2026-10-16_09-30-00_3.png" ]'
rm "$T/stub/date"
printf '#!/bin/sh\nfor a; do f=$a; done; printf "\\211PNG" > "$f"\n' > "$T/stub/grim"; chmod +x "$T/stub/grim"
out=$("$BIN/orion-screenshot" full)
check "Print saves the picture where it says" '[ -s "$out" ] && [ "$(dirname "$out")" = "$T/shots" ]'
check "unknown mode shows usage, exit 2" '! "$BIN/orion-screenshot" bogus >/dev/null'

# ---- clipboard: what gets stored
store() { printf '%s' "$1" | env ${2:+CLIPBOARD_STATE=$2} "$BIN/orion-clipboard" store; }
: > "$XDG_RUNTIME_DIR/stored"; : > "$XDG_RUNTIME_DIR/types"
store "lunch at noon" data
check "ordinary text is kept" 'grep -q "lunch at noon" "$XDG_RUNTIME_DIR/stored"'
check "history is capped (-max-items)" 'grep -q -- "-max-items 100 store" "$XDG_RUNTIME_DIR/cliphist-args"'
KEY="sk-""proj-""Q7rT2mX9vL4kP8wZ3nB6yH1cF5dJ0sGa"
store "export OPENAI_API_KEY=$KEY" data
check "an API key is skipped" '! grep -q "OPENAI_API_KEY" "$XDG_RUNTIME_DIR/stored"'
store "-----BEGIN ""OPENSSH PRIVATE KEY""-----" data
check "a private key is skipped" '! grep -q "PRIVATE KEY" "$XDG_RUNTIME_DIR/stored"'
store "hunter-pass-from-a-manager" sensitive
check "CLIPBOARD_STATE=sensitive is skipped" '! grep -q "hunter-pass" "$XDG_RUNTIME_DIR/stored"'
store "gone" clear
check "CLIPBOARD_STATE=clear is skipped" '! grep -q "^gone" "$XDG_RUNTIME_DIR/stored"'
printf 'text/plain;charset=utf-8\nx-kde-passwordManagerHint\n' > "$XDG_RUNTIME_DIR/types"
store "Correct-Horse-Battery-9" data
check "a copy marked by a password manager is skipped" '! grep -q "Correct-Horse" "$XDG_RUNTIME_DIR/stored"'
check "none of the skipped copies reached the history (still 1 entry)" '[ "$(grep -c -- "^--$" "$XDG_RUNTIME_DIR/stored")" = 1 ]'
: > "$XDG_RUNTIME_DIR/types"
store "" data
check "an empty copy isn't stored" '[ "$(grep -c -- "^--$" "$XDG_RUNTIME_DIR/stored")" = 1 ]'
printf '\211PNG\r\n\032\n\377\376' | CLIPBOARD_STATE=data "$BIN/orion-clipboard" store
check "images are kept" '[ "$(grep -c -- "^--$" "$XDG_RUNTIME_DIR/stored")" = 2 ]'
check "nothing is left behind in the runtime folder" '! ls "$XDG_RUNTIME_DIR"/orion-clip.* >/dev/null 2>&1'
"$BIN/orion-clipboard" clear >/dev/null
check "clear empties the history" '[ ! -s "$XDG_RUNTIME_DIR/stored" ]'

# ---- night light: timing from the zone, no network
a=$(ORION_NIGHT_ZONE=Europe/London "$BIN/orion-nightlight" args)
check "night light uses London's position (got: $a)" '[ "$a" = "-t 4000 -l 51.51 -L -0.13" ]'
a=$(ORION_NIGHT_ZONE=Asia/Calcutta "$BIN/orion-nightlight" args)
check "old zone names are followed (got: $a)" 'echo "$a" | grep -q -- "-l 22.53 -L 88.37"'
a=$(ORION_NIGHT_ZONE=UTC "$BIN/orion-nightlight" args)
check "UTC falls back to fixed evening hours (got: $a)" '[ "$a" = "-t 4000 -S 07:00 -s 19:30" ]'
"$BIN/orion-nightlight" off >/dev/null
check "off is remembered" '[ "$(cat "$HOME/.config/orion/nightlight")" = off ] && "$BIN/orion-nightlight" status | grep -q "off"'
check "start does nothing while it's off" '"$BIN/orion-nightlight" start && ! pgrep -u "$(id -u)" -x wlsunset >/dev/null'

# ---- time zone: set, refuse junk, first start keeps an existing zone
export ORION_TZ_NO_RESTART=1
: > "$XDG_RUNTIME_DIR/timedatectl"
"$BIN/orion-timezone" set Europe/Oslo </dev/null >/dev/null
check "set calls timedatectl set-timezone and turns network time on" \
  'grep -qx "set-timezone Europe/Oslo" "$XDG_RUNTIME_DIR/timedatectl" && grep -qx "set-ntp true" "$XDG_RUNTIME_DIR/timedatectl"'
check "the choice is remembered (provision won't reset it)" '[ "$(cat "$HOME/.config/orion/timezone-chosen")" = Europe/Oslo ]'
: > "$XDG_RUNTIME_DIR/timedatectl"
"$BIN/orion-timezone" set "../../etc/passwd" </dev/null >/dev/null 2>&1; rc=$?
check "a made-up zone is refused before timedatectl (exit $rc)" '[ $rc = 2 ] && [ ! -s "$XDG_RUNTIME_DIR/timedatectl" ]'
"$BIN/orion-timezone" set "Mars/Olympus_Mons" </dev/null >/dev/null 2>&1
check "an unknown zone is refused" '[ ! -s "$XDG_RUNTIME_DIR/timedatectl" ]'
out=$(printf '\n' | "$BIN/orion-timezone" first-start 2>&1)
check "first start offers to keep a zone already chosen" 'echo "$out" | grep -q "Keep it?" && [ ! -s "$XDG_RUNTIME_DIR/timedatectl" ]'
check "first start never goes online without a yes" '! echo "$out" | grep -q "It looks like"'
check "status prints the zone" '"$BIN/orion-timezone" status | grep -q "^Time zone: "'

# ---- Share clean (Toolkit #23): a log file gets a clean copy next to it
printf 'crash in /home/sam/game/server.py talking to 203.0.113.4\n' > "$T/crash.log"
"$BIN/orion-share-scrub" "$T/crash.log" >/dev/null 2>&1
check "share clean writes FILE-clean with home folders and addresses taken out" \
  'grep -qx "crash in ~/game/server.py talking to \[ip\]" "$T/crash-clean.log" && grep -q /home/sam "$T/crash.log"'
check "share clean refuses a missing file" '! "$BIN/orion-share-scrub" "$T/nope.png" >/dev/null 2>&1'

printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
