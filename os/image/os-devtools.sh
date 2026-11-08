#!/usr/bin/env bash
# OrionOS developer tools for the Toolkit (docs/ORION_TOOLKIT_PLAN.md): `just` and `mise` for the Project Runbook
# (tool 10: one command per project, tool versions pinned per project), plus minisign for the Release Butler.
#
#   bash os/image/os-devtools.sh        (as root: inside the image chroot, on the lab VM, or on an installed OrionOS)
#
# Called from os/lab/provision.sh step 6. Safe to re-run: a tool that's already at the pinned version is skipped.
# Where they come from:
#   just      Ubuntu's own package when the archive has it; otherwise the official GitHub release (CC0).
#   mise      the official GitHub release (MIT); Ubuntu 24.04 doesn't package it.
#   minisign  Ubuntu's package.
# Release downloads are checked against the SHA-256 pinned below (taken from each release's checksum file),
# so a changed or truncated download is refused and nothing is installed.
#   ORION_DEVTOOLS_FROM_GITHUB=0   never download; use Ubuntu packages only (mise is then skipped)
set -euo pipefail

JUST_VERSION=1.38.0
MISE_VERSION=2025.1.6
declare -A SHA=(
  [just-x86_64]=c803e67fd7b0af01667bd537197bc3df319938eacf9e8d51a441c71d03035bb5
  [just-aarch64]=efded3b062708c2bf55e7d7e7208f47ab70134a299e66e42d2759f67b421f9d3
  [mise-x64]=61fbda9a559f3c378569f666f64be6846d9f316863eb68b8e8ae6717e9d4f1de
  [mise-arm64]=73eddf81527077b006de7421794c609da971117bfa84c260df6dd17fc4a206e4
)
FROM_GITHUB="${ORION_DEVTOOLS_FROM_GITHUB:-1}"
BIN=/usr/local/bin
export DEBIAN_FRONTEND=noninteractive

say() { printf '\033[1;36m[os-devtools] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

case "$(uname -m)" in
  x86_64|amd64) JUST_ARCH=x86_64; MISE_ARCH=x64 ;;
  aarch64|arm64) JUST_ARCH=aarch64; MISE_ARCH=arm64 ;;
  *) JUST_ARCH=""; MISE_ARCH="" ;;
esac

WORK="$(mktemp -d /var/tmp/orion-devtools.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

# fetch <url> <file> <sha256>: download and check, or fail without leaving the file behind
fetch() {
  curl -fsSL --retry 3 --max-time 300 -o "$2" "$1" || { echo "!! couldn't download $1"; rm -f "$2"; return 1; }
  if ! printf '%s  %s\n' "$3" "$2" | sha256sum -c --quiet -; then
    echo "!! $1 doesn't match its pinned checksum; not installing it"; rm -f "$2"; return 1
  fi
}

say "minisign (Release Butler signatures)"
apt-get install -y -q --no-install-recommends minisign curl ca-certificates || echo "!! minisign not installed"

say "just $JUST_VERSION (Project Runbook)"
have_just() { command -v just >/dev/null && just --version 2>/dev/null | grep -q .; }
if ! have_just; then
  if apt-cache show just >/dev/null 2>&1 && apt-get install -y -q --no-install-recommends just; then
    say "just from Ubuntu: $(just --version)"
  elif [ "$FROM_GITHUB" = 1 ] && [ -n "$JUST_ARCH" ]; then
    T="just-$JUST_VERSION-$JUST_ARCH-unknown-linux-musl.tar.gz"
    if fetch "https://github.com/casey/just/releases/download/$JUST_VERSION/$T" "$WORK/$T" "${SHA[just-$JUST_ARCH]}"; then
      tar -xzf "$WORK/$T" -C "$WORK" just
      install -m 755 "$WORK/just" "$BIN/just"
      say "just from GitHub: $("$BIN/just" --version)"
    fi
  else
    echo "!! just isn't installed (no Ubuntu package and downloads are off). Orion runs project commands directly."
  fi
else
  say "already installed: $(just --version)"
fi

say "mise $MISE_VERSION (pinned tool versions per project)"
if command -v mise >/dev/null && mise --version 2>/dev/null | grep -q "^$MISE_VERSION"; then
  say "already installed: $(mise --version | head -n1)"
elif [ "$FROM_GITHUB" = 1 ] && [ -n "$MISE_ARCH" ]; then
  F="mise-v$MISE_VERSION-linux-$MISE_ARCH"
  if fetch "https://github.com/jdx/mise/releases/download/v$MISE_VERSION/$F" "$WORK/mise" "${SHA[mise-$MISE_ARCH]}"; then
    install -m 755 "$WORK/mise" "$BIN/mise"
    say "mise from GitHub: $("$BIN/mise" --version | head -n1)"
  fi
else
  echo "!! mise isn't installed. Projects still run; their tool versions just aren't switched automatically."
fi

# mise for login shells (activates a project's pinned versions when you cd into it). Harmless without mise.
install -d -m 755 /etc/profile.d
cat > /etc/profile.d/orion-mise.sh <<'EOF'
# OrionOS: pinned tool versions per project (.mise.toml), written by Orion's Project Runbook
if command -v mise >/dev/null 2>&1 && [ -n "${BASH_VERSION:-}" ]; then eval "$(mise activate bash)"; fi
EOF
chmod 644 /etc/profile.d/orion-mise.sh
say "Done."
