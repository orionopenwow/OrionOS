#!/usr/bin/env bash
# OrionOS on your own machine: turns a fresh Ubuntu 24.04 install (a laptop partition or a USB SSD) into OrionOS.
#
#   wget -O /tmp/install-orion.sh https://raw.githubusercontent.com/orionopenwow/OrionOS/main/os/baremetal/install-orion.sh
#   sudo bash /tmp/install-orion.sh
#
# What it does: downloads Orion's newest release bundle from GitHub and checks its signature with OrionOS's release
# key (minisign), creates the orion-admin account (you choose its password), then runs the same setup the OrionOS
# lab VM uses, tuned for real hardware (Wi-Fi, Bluetooth, battery, your phone on your home network). Your Ubuntu
# account stays as it is. At the next boot you land in Orion. Later updates: sudo orion-update (signed the same way).
#
# The release key: ORION_RELEASE_PUB=/path/to/release.pub if you have it, else os/image/release.pub when this runs
# from a copy of the repo, else the one published with the release (its key ID is shown: compare it with the one on
# the project page). It's kept in /etc/orion/release.pub and every later update must be signed with it.
#
#   sudo bash install-orion.sh --dev-git-unsigned    DEVELOPERS ONLY: git clone, no signature check (prints a warning)
set -euo pipefail
REPO="${ORION_REPO:-https://github.com/orionopenwow/OrionOS.git}"
RELEASE_REPO="${ORION_RELEASE_REPO:-orionopenwow/OrionOS}"
BRANCH="${ORION_BRANCH:-main}"
SRC=/opt/orion-src
DEV_GIT=0; [ "${1:-}" = --dev-git-unsigned ] && DEV_GIT=1
say() { printf '\n\033[1;36m[OrionOS] %s\033[0m\n' "$*"; }
die() { printf '\033[1;31m%s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || { echo "Run it with sudo:  sudo bash $0"; exit 1; }
. /etc/os-release
if [ "${VERSION_ID:-}" != "24.04" ]; then
  echo "OrionOS is built for Ubuntu 24.04; this is ${PRETTY_NAME:-unknown}."
  printf "Continue anyway? [y/N] "; read -r a </dev/tty; [ "$a" = y ] || exit 1
fi
if systemd-detect-virt -q; then echo "(This looks like a virtual machine; that works too.)"; fi

say "1/4 Getting Orion"
apt-get update -q
apt-get install -y -q ca-certificates curl minisign
install -d -m 755 /etc/orion
if [ "$DEV_GIT" = 1 ]; then
  echo "WARNING: developer path. The code comes straight from git with NO signature check: whoever can push to"
  echo "that branch gets admin rights on this computer. For a normal install, run this without --dev-git-unsigned."
  apt-get install -y -q git
  if [ -d "$SRC/.git" ]; then git -C "$SRC" fetch -q origin "$BRANCH" && git -C "$SRC" checkout -q -B "$BRANCH" "origin/$BRANCH"
  else rm -rf "$SRC"; git clone -q --depth 1 -b "$BRANCH" "$REPO" "$SRC"; fi
else
  BASE="https://github.com/$RELEASE_REPO/releases/latest/download"
  W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
  HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)"
  if [ -n "${ORION_RELEASE_PUB:-}" ]; then cp "$ORION_RELEASE_PUB" "$W/release.pub"
  elif [ -f /etc/orion/release.pub ]; then cp /etc/orion/release.pub "$W/release.pub"
  elif [ -n "$HERE" ] && [ -f "$HERE/../image/release.pub" ]; then cp "$HERE/../image/release.pub" "$W/release.pub"
  else
    curl -fsSL --retry 3 -o "$W/release.pub" "$BASE/release.pub" || die "Couldn't get OrionOS's release key. Nothing was changed."
    echo "Release key ID: $(tail -n1 "$W/release.pub" | head -c 24)… (compare it with the one on the project page)"
  fi
  curl -fsSL --retry 3 -o "$W/orion-bundle.tar.gz" "$BASE/orion-bundle.tar.gz" || die "Couldn't download Orion. Nothing was changed."
  curl -fsSL --retry 3 -o "$W/orion-bundle.tar.gz.minisig" "$BASE/orion-bundle.tar.gz.minisig" || die "Couldn't download Orion's signature. Nothing was changed."
  out="$(minisign -V -p "$W/release.pub" -m "$W/orion-bundle.tar.gz" 2>&1)" || die "Signature check FAILED for the Orion download, so nothing was installed."
  V="$(printf '%s\n' "$out" | sed -n 's/^Trusted comment: orion-bundle \([A-Za-z0-9._+-]*\).*/\1/p' | head -n1)"
  [ -n "$V" ] || die "The download's signature doesn't say which version it is, so nothing was installed."
  # nothing absolute, no "..": only files that belong under /opt/orion-src
  bad="$(tar -tzf "$W/orion-bundle.tar.gz" | grep -cE '(^/|(^|/)\.\.(/|$))' || true)"
  [ "$bad" = 0 ] || die "The download contains paths it shouldn't. Nothing was installed."
  rm -rf "$SRC.new"; mkdir -p "$SRC.new"
  tar -xzf "$W/orion-bundle.tar.gz" -C "$SRC.new" --no-same-owner
  [ "$(cat "$SRC.new/BUNDLE_VERSION" 2>/dev/null)" = "$V" ] || die "The download's contents don't match its signature. Nothing was installed."
  if [ -e "$SRC" ]; then rm -rf "$SRC.prev"; mv "$SRC" "$SRC.prev"; fi
  mv "$SRC.new" "$SRC"
  install -m 644 "$W/release.pub" /etc/orion/release.pub
  [ -f /etc/orion/release.conf ] || printf 'REPO=%s\nFLAVOUR=standard\n' "$RELEASE_REPO" > /etc/orion/release.conf
  [ -f /etc/orion/channel ] || echo stable > /etc/orion/channel
  echo "Orion $V, signature checked."
fi
ls "$SRC"/os/dist/*.deb >/dev/null 2>&1 || echo "!! The Orion HUD package (os/dist/*.deb) isn't in this release."

say "2/4 The orion-admin account (for maintenance, like on the VMs)"
if ! id orion-admin >/dev/null 2>&1; then
  adduser --disabled-password --gecos "OrionOS admin" orion-admin
  usermod -aG sudo,adm orion-admin
  echo "Choose a password for orion-admin (you'll need it for updates and settings that change the system):"
  until passwd orion-admin </dev/tty; do echo "Try again."; done
fi

say "3/4 Setting up OrionOS (10-20 minutes)"
ORION_PROFILE=baremetal bash "$SRC/os/lab/provision.sh" 2>&1 | tee /var/log/orion-provision.log

say "4/4 Done"
echo "Restart now and you'll land in Orion. Your phone pairs on your home Wi-Fi: Ctrl+Alt+S > Pair my phone."
echo "Ubuntu's own desktop is still installed. To bring its login screen back: sudo systemctl enable --force gdm3"
printf "Restart now? [Y/n] "; read -r a </dev/tty || a=y
case "$a" in n|N) ;; *) systemctl reboot ;; esac
