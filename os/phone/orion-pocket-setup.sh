#!/usr/bin/env bash
# Pocket Orion setup: run this inside Android 16's Linux terminal (the Debian VM on the phone).
#
# It installs a small part of Orion into ~/orion-pocket so voice, Video Scout, your vault and (if the phone has
# the memory) a tiny local model keep working when your PC is off. The Orion app reaches it at
# http://127.0.0.1:9121 through the Terminal app's port forwarding. See docs/ORION_PHONE_GUIDE.md.
#
#   bash orion-pocket-setup.sh               set it up (asks before installing Ollama)
#   bash orion-pocket-setup.sh --dry-run     only print what it would do; changes nothing
#
# Options: --home DIR   --source DIR (apps/daemon of an Orion checkout)   --port N
#          --no-voice   --no-scout   --no-model   --model   --yes
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POCKET_HOME="${HOME}/orion-pocket"
SOURCE="${HERE}/../../apps/daemon"
PORT=9121
DRY=0
VOICE=1
SCOUT=1
MODEL=auto
YES=0
MEMINFO="${ORION_POCKET_MEMINFO:-/proc/meminfo}"

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --home) POCKET_HOME="$2"; shift ;;
    --source) SOURCE="$2"; shift ;;
    --port) PORT="$2"; shift ;;
    --no-voice) VOICE=0 ;;
    --no-scout) SCOUT=0 ;;
    --no-model) MODEL=no ;;
    --model) MODEL=yes ;;
    --yes|-y) YES=1 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)"; exit 2 ;;
  esac
  shift
done

case "$PORT" in ''|*[!0-9]*) echo "--port needs a number"; exit 2 ;; esac
if [ "$PORT" -lt 1024 ] || [ "$PORT" -gt 65535 ]; then echo "--port must be 1024-65535"; exit 2; fi

# Files from apps/daemon that Pocket Orion needs (nothing that touches the PC, money, agents or messages).
# freeze.py: the boardroom asks it before acting; injection_firewall.py: Video Scout checks transcripts with it.
FILES="boardroom.py phones.py phone_link.py phone_api.py phone_pair.py harvester.py alignment_drafter.py alerts.py
video_scout.py scout_catalogue.py scout_routes.py freeze.py injection_firewall.py"

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
run() {
  if [ "$DRY" = 1 ]; then printf '  would run: %s\n' "$*"; else "$@"; fi
}
write_file() {   # write_file PATH (content on stdin)
  if [ "$DRY" = 1 ]; then printf '  would write: %s\n' "$1"; cat >/dev/null; else cat >"$1"; fi
}

# ------------------------------------------------------------------ what this phone can carry
mem_mb=0
if [ -r "$MEMINFO" ]; then
  mem_mb=$(awk '/^MemTotal:/ {print int($2/1024)}' "$MEMINFO")
fi
free_mb=$(df -Pm "$(dirname "$POCKET_HOME")" 2>/dev/null | awk 'NR==2 {print $4}')
free_mb=${free_mb:-0}

step "Pocket Orion setup$([ "$DRY" = 1 ] && echo ' (dry run: nothing will change)')"
say "Memory for Linux: ${mem_mb} MB. Free space: ${free_mb} MB. Install folder: ${POCKET_HOME}"
if [ ! -r /etc/debian_version ]; then
  say "Note: this doesn't look like the Debian terminal on Android. It may still work on other Debian systems."
fi
if [ ! -f "${SOURCE}/phone_pair.py" ]; then
  say "Can't find Orion's files at ${SOURCE}."
  say "Copy the Orion folder to the phone's Download folder, then run this script from there,"
  say "or pass --source /path/to/orion/apps/daemon."
  exit 1
fi

# Voice: faster-whisper's tiny model needs about 1 GB of memory and 1 GB of disk with its libraries.
if [ "$VOICE" = 1 ] && { [ "$mem_mb" -lt 2500 ] || [ "$free_mb" -lt 1500 ]; }; then
  say "Voice skipped: it needs about 2.5 GB of memory and 1.5 GB free space for Linux."
  VOICE=0
fi
# Local model: only when Linux gets at least 4 GB (a 0.5B model), 6 GB for the 1.5B one.
OLLAMA_MODEL=""
if [ "$MODEL" != no ]; then
  if [ "$mem_mb" -ge 6000 ] && [ "$free_mb" -ge 4000 ]; then OLLAMA_MODEL="qwen2.5:1.5b"
  elif [ "$mem_mb" -ge 4000 ] && [ "$free_mb" -ge 2500 ]; then OLLAMA_MODEL="qwen2.5:0.5b"
  else say "Local model skipped: Linux on this phone has too little memory (needs 4 GB or more)."
  fi
fi

# ------------------------------------------------------------------ system packages
step "System packages"
PKGS="python3 python3-venv python3-pip curl ca-certificates"
[ "$SCOUT" = 1 ] && PKGS="$PKGS ffmpeg"
SUDO=""
[ "$(id -u)" != 0 ] && SUDO="sudo"
run $SUDO apt-get update
# shellcheck disable=SC2086
run $SUDO apt-get install -y $PKGS

# ------------------------------------------------------------------ files
step "Orion files"
run mkdir -p "${POCKET_HOME}/daemon" "${POCKET_HOME}/data/vault" "${POCKET_HOME}/bin"
for f in $FILES; do
  if [ -f "${SOURCE}/${f}" ]; then
    run cp "${SOURCE}/${f}" "${POCKET_HOME}/daemon/${f}"
  else
    say "  missing ${f} (skipped)"
  fi
done
run cp "${HERE}/pocket/orion_pocket.py" "${POCKET_HOME}/orion_pocket.py"

# ------------------------------------------------------------------ python
step "Python packages"
run python3 -m venv "${POCKET_HOME}/venv"
PIP="${POCKET_HOME}/venv/bin/pip"
PY_PKGS="fastapi uvicorn httpx cryptography pydantic"
[ "$VOICE" = 1 ] && PY_PKGS="$PY_PKGS faster-whisper"
[ "$SCOUT" = 1 ] && PY_PKGS="$PY_PKGS yt-dlp"
run "$PIP" install --upgrade pip
# shellcheck disable=SC2086
run "$PIP" install $PY_PKGS

# ------------------------------------------------------------------ local model (optional)
if [ -n "$OLLAMA_MODEL" ]; then
  step "Local model (${OLLAMA_MODEL})"
  go=1
  if [ "$MODEL" != yes ] && [ "$YES" != 1 ] && [ "$DRY" != 1 ]; then
    printf 'Install Ollama from ollama.com and download %s (about 0.4-1 GB)? [y/N] ' "$OLLAMA_MODEL"
    read -r answer || answer=""
    case "$answer" in y|Y|yes) go=1 ;; *) go=0; OLLAMA_MODEL="" ;; esac
  fi
  if [ "$go" = 1 ] && [ -n "$OLLAMA_MODEL" ]; then
    if ! command -v ollama >/dev/null 2>&1; then
      run sh -c "curl -fsSL https://ollama.com/install.sh | sh"
    fi
    run ollama pull "$OLLAMA_MODEL"
  fi
fi

# ------------------------------------------------------------------ launcher and service
step "Launcher"
write_file "${POCKET_HOME}/bin/orion-pocket" <<EOF
#!/bin/sh
export ORION_POCKET_HOME="${POCKET_HOME}"
export ORION_POCKET_PORT="${PORT}"
export ORION_POCKET_MODEL="${OLLAMA_MODEL:-qwen2.5:0.5b}"
exec "${POCKET_HOME}/venv/bin/python" "${POCKET_HOME}/orion_pocket.py" "\$@"
EOF
run chmod +x "${POCKET_HOME}/bin/orion-pocket"
run mkdir -p "${HOME}/.local/bin"
run ln -sf "${POCKET_HOME}/bin/orion-pocket" "${HOME}/.local/bin/orion-pocket"

step "Keep it running"
if [ "$DRY" = 1 ] || systemctl --user show-environment >/dev/null 2>&1; then
  run mkdir -p "${HOME}/.config/systemd/user"
  write_file "${HOME}/.config/systemd/user/orion-pocket.service" <<EOF
[Unit]
Description=Pocket Orion (phone routes for the Orion app)

[Service]
ExecStart=${POCKET_HOME}/bin/orion-pocket serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
  run systemctl --user daemon-reload
  run systemctl --user enable --now orion-pocket.service
  say "Pocket Orion runs as a user service (systemctl --user status orion-pocket)."
else
  write_file "${POCKET_HOME}/run-pocket.sh" <<EOF
#!/bin/sh
# No systemd here: start Pocket Orion in the background. Run again after the terminal restarts.
nohup "${POCKET_HOME}/bin/orion-pocket" serve >"${POCKET_HOME}/pocket.log" 2>&1 &
echo "Pocket Orion started (log: ${POCKET_HOME}/pocket.log)"
EOF
  run chmod +x "${POCKET_HOME}/run-pocket.sh"
  run "${POCKET_HOME}/run-pocket.sh"
fi

step "Next steps"
say "1. Android asks whether to let the terminal open port ${PORT}. Allow it. The app then reaches it at http://127.0.0.1:${PORT}."
say "   (You can change this later in the Terminal app's settings, under its port forwarding section.)"
say "2. Pair the app: run   orion-pocket pair   and type the code in the Orion app (Phone setup, Pocket Orion card)."
say "3. Compare fingerprints, then run   orion-pocket confirm <fingerprint>"
say "Put guides (.md or .txt) in ${POCKET_HOME}/data/vault for offline answers."
say "Undo: systemctl --user disable --now orion-pocket; then delete ${POCKET_HOME}."
[ "$DRY" = 1 ] && say "Dry run finished. Nothing was changed."
exit 0
