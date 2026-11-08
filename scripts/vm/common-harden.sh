#!/usr/bin/env bash
# Base hardening shared by orion-lab and orion-boardroom (Appendix A.3 + A.4 of docs/NEXT_PASS_PLAN.md).
# Sourced by lab-setup.sh / boardroom-setup.sh — not meant to be run on its own.
#
# What it does (all idempotent, safe to re-run):
#   - security updates + unattended-upgrades
#   - ufw: deny incoming, allow only the Tailscale interface in
#   - SSH: keys only, no root login (refuses to lock you out if no key is installed yet)
#   - fail2ban
#   - Tailscale with the given hostname
set -euo pipefail

log()  { printf '\033[1;35m[orion]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[orion] warning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[orion] error:\033[0m %s\n' "$*" >&2; exit 1; }

require_root() {
  [[ $EUID -eq 0 ]] || die "Run this with sudo: sudo bash $0"
}

admin_user() {
  # the human who ran sudo (falls back to orion-admin)
  echo "${SUDO_USER:-orion-admin}"
}

harden_base() {
  local hostname="$1"
  export DEBIAN_FRONTEND=noninteractive

  log "Updating packages…"
  apt-get update -q
  apt-get full-upgrade -y -q
  apt-get install -y -q unattended-upgrades ufw fail2ban curl git ca-certificates gnupg nftables

  log "Turning on automatic security updates…"
  cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

  log "Firewall: nothing comes in except over the private VPN…"
  ufw --force default deny incoming
  ufw --force default allow outgoing
  ufw --force enable

  log "SSH: keys only, no root…"
  local admin home keys
  admin="$(admin_user)"
  home="$(getent passwd "$admin" | cut -d: -f6)"
  keys="$home/.ssh/authorized_keys"
  install -d -m 700 -o "$admin" -g "$admin" "$home/.ssh"
  if [[ -s "$keys" ]]; then
    install -d /etc/ssh/sshd_config.d
    cat >/etc/ssh/sshd_config.d/10-orion.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
    chmod 600 "$keys"; chown "$admin:$admin" "$keys"
    systemctl restart ssh || systemctl restart sshd || true
  else
    warn "No SSH key in $keys yet — leaving password login ON so you don't get locked out."
    warn "Paste your PC's ~/.ssh/id_ed25519.pub into that file, then re-run this script."
  fi

  systemctl enable --now fail2ban >/dev/null

  log "Installing Tailscale (private VPN)…"
  if ! command -v tailscale >/dev/null; then
    # Tailscale's signed apt repository (apt checks the signing key; no "curl | sh")
    . /etc/os-release
    curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${VERSION_CODENAME:-noble}.noarmor.gpg" -o /usr/share/keyrings/tailscale-archive-keyring.gpg
    echo "deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/ubuntu ${VERSION_CODENAME:-noble} main" > /etc/apt/sources.list.d/tailscale.list
    apt-get update -q && apt-get install -y -q tailscale
  fi
  if ! tailscale status >/dev/null 2>&1; then
    log "Log in to Tailscale in the browser link below (same account as your PC):"
    tailscale up --ssh --hostname "$hostname"
  fi
  ufw allow in on tailscale0 >/dev/null
  log "Base hardening done for $hostname."
}
