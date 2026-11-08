#!/usr/bin/env bash
# OrionOS — orion-boardroom provisioning (VM 2): the sign-off station.
# A text-only machine whose one job is holding the signing key and showing decisions for approval.
# No browser, no desktop, no other apps. It may only talk to the lab's boardroom port, DNS, updates and Tailscale.
#
# Run as root from the bundle:  bash /opt/orion-src/os/boardroom/provision.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OS="$SRC/os"
LAB_IP="${ORION_LAB_IP:-10.77.0.10}"
BR_USER=boardroom
export DEBIAN_FRONTEND=noninteractive

step() { printf '\n\033[1;33m[orion-boardroom] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

step "1/6 Hostname, UTC clock (signatures need both clocks within 2 minutes)"
hostnamectl set-hostname orion-boardroom
timedatectl set-timezone UTC || true
timedatectl set-ntp true || true
grep -q "orion-boardroom" /etc/hosts || echo "127.0.1.1 orion-boardroom" >> /etc/hosts
grep -q " orion-lab" /etc/hosts || echo "$LAB_IP orion-lab" >> /etc/hosts

step "2/6 Packages (Python + cryptography only)"
apt-get update -q
apt-get install -y -q --no-install-recommends python3 python3-cryptography ufw unattended-upgrades ca-certificates curl

step "3/6 The boardroom client"
install -d -m 755 /opt/orion-boardroom
install -m 755 "$SRC/apps/boardroom/orion_boardroom.py" /opt/orion-boardroom/orion_boardroom.py
cat > /usr/local/bin/orion-boardroom <<EOF
#!/bin/sh
export ORION_LAB_URL="\${ORION_LAB_URL:-http://$LAB_IP:9121}"
exec /usr/bin/python3 /opt/orion-boardroom/orion_boardroom.py "\$@"
EOF
chmod 755 /usr/local/bin/orion-boardroom

step "4/6 User '$BR_USER' auto-logs in on the console straight into the sign-off console"
id "$BR_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "Orion boardroom" "$BR_USER"
install -d -m 700 -o "$BR_USER" -g "$BR_USER" "/home/$BR_USER/.orion-boardroom"
chmod 750 "/home/$BR_USER"
cat > "/home/$BR_USER/.bash_profile" <<'EOF'
# Console only: run the sign-off console; when it exits, log out (no shell left open on the console).
if [ "$(tty)" = "/dev/tty1" ]; then
  clear
  orion-boardroom
  logout
fi
EOF
chown "$BR_USER:$BR_USER" "/home/$BR_USER/.bash_profile"
install -d /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $BR_USER --noclear %I \$TERM
EOF
systemctl daemon-reload

step "5/6 Firewall: nothing comes in except Tailscale; out only to the lab, DNS, updates, Tailscale"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default deny outgoing
ufw allow in on tailscale0 to any port 22 proto tcp comment "SSH over Tailscale for maintenance"
ufw allow out on tailscale0
ufw allow out to "$LAB_IP" port 9121 proto tcp comment "the lab's boardroom endpoint"
ufw allow out 53 comment "DNS"
ufw allow out 123/udp comment "NTP"
ufw allow out 80/tcp comment "Ubuntu security updates"
ufw allow out 443/tcp comment "updates, Tailscale coordination"
ufw allow out 41641/udp comment "Tailscale"
ufw allow out 67/udp comment "DHCP"
ufw --force enable
if ! command -v tailscale >/dev/null; then
  bash "$OS/common/install-tailscale.sh" || echo "!! Tailscale install failed; run it later."   # signed apt repo, no curl|sh
fi

step "6/6 Base hardening"
install -d /etc/orion
bash "$OS/common/harden.sh"
echo "$(date -u +%FT%TZ) provisioned" > /etc/orion/provisioned
step "Done. After the reboot, the console asks you to create the signing key and pair with the lab."
