#!/usr/bin/env bash
# Shared base hardening for both OrionOS VMs (V3 Appendix A.3).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -q unattended-upgrades
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF
# raid6_pq runs a speed test at every boot that can freeze a VM for ~40 minutes when VirtualBox runs on top of
# Windows Hyper-V. OrionOS's disk is ext4, so btrfs and software RAID (the only users of raid6_pq) aren't needed.
# Only in VMs: on your own machine the disk might be btrfs or RAID, and there's no Hyper-V problem.
if [ "${ORION_PROFILE:-vm}" = vm ]; then
cat > /etc/modprobe.d/orion-no-raid6.conf <<'CONF'
install raid6_pq /usr/bin/true
install btrfs /usr/bin/true
install raid456 /usr/bin/true
CONF
update-initramfs -u -k all >/dev/null 2>&1 || update-initramfs -u || true
else
rm -f /etc/modprobe.d/orion-no-raid6.conf
fi
# SSH: keys only, no root login.
install -d /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/10-orion.conf <<'CONF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
CONF
systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
# Keep the agent user (if any) away from the daemon's local ports: a local process could otherwise
# drive Orion's HTTP API. Separate nft table so ufw's own rules are untouched.
if id orion-agent >/dev/null 2>&1; then
  cat > /etc/orion/localguard.nft <<'NFT'
table inet orion_localguard
delete table inet orion_localguard
table inet orion_localguard {
  chain output {
    type filter hook output priority 0; policy accept;
    meta skuid "orion-agent" tcp dport { 9120, 9121 } reject with tcp reset
  }
}
NFT
  cat > /etc/systemd/system/orion-localguard.service <<'UNIT'
[Unit]
Description=OrionOS: block the agent user from Orion's local API ports
After=network-pre.target ufw.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/orion/localguard.nft
ExecStop=/usr/sbin/nft delete table inet orion_localguard
[Install]
WantedBy=multi-user.target
UNIT
  if [ "${ORION_PROFILE:-vm}" = image ]; then systemctl enable orion-localguard.service
  else systemctl daemon-reload; systemctl enable --now orion-localguard.service; fi
fi
