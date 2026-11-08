#!/usr/bin/env bash
# OrionOS security check-up tools for the Toolkit (docs/ORION_TOOLKIT_PLAN.md tools 19-21):
#   osv-scanner  Supply-Chain Scan: known holes in your projects' packages (official GitHub release, MIT,
#                checked against the SHA-256 pinned below)
#   lynis        Host Health: the weekly security audit (Ubuntu package)
#   aide, smartmontools, upower   Host Health on real hardware: file watch, disk health, battery (not in VMs)
#   nmap         Exposure Map's home-network scan (off until you turn it on in Settings)
#   fail2ban     brute-force protection for SSH: servers only, and only with ORION_BRUTEFORCE=1
#   orion-security-dump + timers  copies the reports that need admin rights to /var/lib/orion/security/
#
#   bash os/image/os-security.sh       (as root: in the image chroot, on the lab VM, on an installed OrionOS,
#                                       or on a plain Ubuntu server with ORION_PROFILE=server)
#   ORION_PROFILE=vm|baremetal|image|server   ORION_USER=orion   ORION_BRUTEFORCE=1
#   ORION_DEVTOOLS_FROM_GITHUB=0   never download; Ubuntu packages only (osv-scanner is then skipped)
# Called from os/lab/provision.sh. Safe to re-run.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROFILE="${ORION_PROFILE:-$(cat /etc/orion/profile 2>/dev/null || echo baremetal)}"
ORION_USER="${ORION_USER:-orion}"
FROM_GITHUB="${ORION_DEVTOOLS_FROM_GITHUB:-1}"
OSV_VERSION=2.0.2
declare -A SHA=(
  [osv-amd64]=3abcfd7126c453a00421487e721b296e0cb68085bd431d6cef60872774170fc8
  [osv-arm64]=5da413cb77ddb99bd115961e25ddf02490f6e1415abea1e15c9557057a457c08
)
export DEBIAN_FRONTEND=noninteractive
say() { printf '\033[1;36m[os-security] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

say "Packages ($PROFILE)"
pkgs="lynis"
case "$PROFILE" in
  vm) ;;                                                    # no disks, battery or file watch to check in a VM
  server) pkgs="$pkgs smartmontools" ;;
  *) pkgs="$pkgs aide smartmontools upower nmap" ;;
esac
[ "${ORION_BRUTEFORCE:-0}" = 1 ] && [ "$PROFILE" = server ] && pkgs="$pkgs fail2ban"
# shellcheck disable=SC2086
apt-get install -y -q --no-install-recommends $pkgs || echo "!! some security tools didn't install: $pkgs"

say "osv-scanner $OSV_VERSION (Supply-Chain Scan)"
case "$(uname -m)" in x86_64|amd64) ARCH=amd64 ;; aarch64|arm64) ARCH=arm64 ;; *) ARCH="" ;; esac
if command -v osv-scanner >/dev/null && osv-scanner --version 2>/dev/null | grep -q "$OSV_VERSION"; then
  say "already installed"
elif [ "$FROM_GITHUB" = 1 ] && [ -n "$ARCH" ]; then
  WORK="$(mktemp -d /var/tmp/orion-security.XXXXXX)"
  F="$WORK/osv-scanner"
  if curl -fsSL --retry 3 --max-time 300 -o "$F" \
       "https://github.com/google/osv-scanner/releases/download/v$OSV_VERSION/osv-scanner_linux_$ARCH" \
     && printf '%s  %s\n' "${SHA[osv-$ARCH]}" "$F" | sha256sum -c --quiet -; then
    install -m 755 "$F" /usr/local/bin/osv-scanner
    say "osv-scanner from GitHub: $(/usr/local/bin/osv-scanner --version 2>/dev/null | head -n1)"
  else
    echo "!! osv-scanner wasn't installed (download failed or didn't match its pinned checksum)."
  fi
  rm -rf "$WORK"
else
  echo "!! osv-scanner isn't installed; the scan uses cargo-audit / pip-audit / npm audit when present."
fi

say "Brute-force protection"
if [ "$PROFILE" = server ] && [ "${ORION_BRUTEFORCE:-0}" = 1 ] && command -v fail2ban-client >/dev/null; then
  install -d /etc/fail2ban/jail.d
  cat > /etc/fail2ban/jail.d/orion-sshd.conf <<'CONF'
# OrionOS (Host Health): slow down password guessing against SSH
[sshd]
enabled = true
maxretry = 5
findtime = 10m
bantime = 1h
CONF
  systemctl enable --now fail2ban || true
  say "fail2ban on for SSH"
else
  say "off (servers only: ORION_PROFILE=server ORION_BRUTEFORCE=1)"
fi

say "Report collector (root timers)"
GROUP="$ORION_USER"; getent group "$GROUP" >/dev/null || GROUP=adm
install -d -m 750 -o root -g "$GROUP" /var/lib/orion/security
install -m 755 "$SRC/os/security/orion-security-dump" /usr/local/sbin/orion-security-dump
for kind in hourly weekly; do
  cat > "/etc/systemd/system/orion-security-$kind.service" <<UNIT
[Unit]
Description=OrionOS: collect $kind security reports for Orion
[Service]
Type=oneshot
Nice=15
IOSchedulingClass=idle
ExecStart=/usr/local/sbin/orion-security-dump $kind
UNIT
done
cat > /etc/systemd/system/orion-security-hourly.timer <<'UNIT'
[Unit]
Description=OrionOS: firewall rules for Orion's Exposure Map, hourly
[Timer]
OnBootSec=5min
OnUnitActiveSec=1h
[Install]
WantedBy=timers.target
UNIT
cat > /etc/systemd/system/orion-security-weekly.timer <<'UNIT'
[Unit]
Description=OrionOS: weekly security check-up reports (Lynis, file watch, disks) for Orion
[Timer]
OnCalendar=Sun 04:30
Persistent=true
RandomizedDelaySec=20min
[Install]
WantedBy=timers.target
UNIT
if [ "$PROFILE" = image ]; then
  systemctl enable orion-security-hourly.timer orion-security-weekly.timer
else
  systemctl daemon-reload
  systemctl enable --now orion-security-hourly.timer orion-security-weekly.timer
  /usr/local/sbin/orion-security-dump hourly || true
fi
say "Done."
