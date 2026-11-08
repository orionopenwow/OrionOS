#!/usr/bin/env bash
# Install Tailscale from its signed apt repository (no "curl | sh").
# apt checks every package against Tailscale's signing key, so a tampered download is refused.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
command -v tailscale >/dev/null && exit 0
. /etc/os-release
codename="${VERSION_CODENAME:-noble}"
distro="${ID:-ubuntu}"
case "$distro" in ubuntu|debian) ;; *) distro=ubuntu ;; esac
install -d -m 755 /usr/share/keyrings
curl -fsSL "https://pkgs.tailscale.com/stable/$distro/$codename.noarmor.gpg" -o /usr/share/keyrings/tailscale-archive-keyring.gpg
chmod 644 /usr/share/keyrings/tailscale-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/$distro $codename main" \
  > /etc/apt/sources.list.d/tailscale.list
apt-get update -q
apt-get install -y -q tailscale
