#!/usr/bin/env bash
# OrionOS safety net (docs/ORIONOS_COMPLETE_PLAN.md step 3; files in os/safety): snapshots, the kill switch's
# root part, and what makes them work at boot.
#
#   bash os/image/os-safety.sh      (as root: inside the image chroot, on the lab VM, or on an installed OrionOS)
#
# Called from os/lab/provision.sh. Safe to re-run.
#   /usr/local/bin/orion-snapshot            snapshots (btrfs + snapper installed, vault copies on the USB stick)
#   /usr/local/bin/orion-frozen              "is the kill switch on?" for update scripts
#   /opt/orion/safety/orion-freeze-helper    pauses / resumes unattended-upgrades (pkexec, polkit)
#   /opt/orion/safety/orion-self-helper      self-running: allow-listed restarts, update channel, levels
#   /usr/local/bin/orion-self-gate           unattended-upgrades runs by itself only at Orion's level 2-3
#   /etc/apt/apt.conf.d/80orion-snapshot     a snapshot before apt changes packages
#   orion-snapshot-nightly.timer             one every night (03:30, or at the next start if the computer was off)
#   /etc/grub.d/42_orion_snapshots           boot menu entries for snapshots (installed btrfs systems)
#   initramfs local-bottom script            starts a snapshot from the boot menu with a RAM overlay on top
#   polkit: the session user may take/list snapshots and use the kill switch; restores ask for the admin password
#   ORION_USER=orion
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$SRC/os/safety"
ORION_USER="${ORION_USER:-orion}"
export DEBIAN_FRONTEND=noninteractive
say() { printf '\033[1;36m[os-safety] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

say "Packages: btrfs tools and snapper (used on drives installed with snapshots), rsync (vault copies)"
missing=""
for p in btrfs-progs snapper rsync; do dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
if [ -n "$missing" ]; then
  apt-get update -q || true
  # shellcheck disable=SC2086
  apt-get install -y -q --no-install-recommends $missing || echo "!! couldn't install:$missing (snapshots need them)"
fi
# snapper's own hourly timer stays off: Orion takes one nightly and before updates instead
systemctl disable snapper-timeline.timer 2>/dev/null || true

say "Commands"
install -d -m 755 /opt/orion/safety /var/lib/orion /var/lib/orion/changes
# a real file (not a link) at the path the polkit action names
install -m 755 "$SRC/os/lab/bin/orion-snapshot" /usr/local/bin/orion-snapshot
install -m 755 "$S/orion-frozen" /usr/local/bin/orion-frozen
install -m 755 -o root -g root "$S/orion-freeze-helper" /opt/orion/safety/orion-freeze-helper
# Self-running (complete plan step 4): allow-listed restarts / channel / levels, the security-update gate
install -m 755 -o root -g root "$S/orion-self-helper" /opt/orion/safety/orion-self-helper
install -m 755 "$S/orion-self-gate" /usr/local/bin/orion-self-gate
install -m 755 "$SRC/os/lab/bin/orion-selftest" /usr/local/bin/orion-selftest
install -d -m 755 /usr/local/share/orion/selftest   # the QEMU boot test the self-test reuses (needs its grub.cfg)
install -m 755 "$SRC/os/image/test-boot.sh" /usr/local/share/orion/selftest/test-boot.sh
install -m 644 "$SRC/os/image/grub.cfg" /usr/local/share/orion/selftest/grub.cfg
[ -f /etc/orion/channel ] || { install -d -m 755 /etc/orion; echo stable > /etc/orion/channel; chmod 644 /etc/orion/channel; }
install -d -m 755 /etc/systemd/system/apt-daily-upgrade.service.d
install -m 644 "$S/apt-daily-upgrade-orion.conf" /etc/systemd/system/apt-daily-upgrade.service.d/50-orion.conf
chmod 755 /var/lib/orion/changes

say "Snapshot before every apt change, and every night"
install -m 644 "$S/80orion-snapshot" /etc/apt/apt.conf.d/80orion-snapshot
install -m 644 "$S/orion-snapshot-nightly.service" "$S/orion-snapshot-nightly.timer" /etc/systemd/system/
systemctl enable orion-snapshot-nightly.timer 2>/dev/null || \
  ln -sf /etc/systemd/system/orion-snapshot-nightly.timer /etc/systemd/system/timers.target.wants/orion-snapshot-nightly.timer

say "Boot menu: start an earlier system from a snapshot (read-only)"
install -d -m 755 /etc/grub.d /etc/initramfs-tools/scripts/local-bottom
install -m 755 "$S/42_orion_snapshots" /etc/grub.d/42_orion_snapshots
install -m 755 "$S/orion-snapshot-overlay" /etc/initramfs-tools/scripts/local-bottom/orion-snapshot-overlay
grep -qx overlay /etc/initramfs-tools/modules 2>/dev/null || echo overlay >> /etc/initramfs-tools/modules

say "Permissions: take and list snapshots, kill switch; restores ask for the admin password"
install -d -m 755 /usr/share/polkit-1/actions /etc/polkit-1/rules.d
install -m 644 "$S/ai.orion.safety.policy" /usr/share/polkit-1/actions/ai.orion.safety.policy
sed "s/subject.user != \"orion\"/subject.user != \"$ORION_USER\"/" "$S/50-orion-safety.rules" \
  > /etc/polkit-1/rules.d/50-orion-safety.rules
chmod 644 /etc/polkit-1/rules.d/50-orion-safety.rules

# Snapshots themselves are only made where they can work: an installed btrfs system (orion-install-disk sets up
# snapper there) or the USB stick with its vault unlocked. Elsewhere orion-snapshot says so and does nothing.
/usr/local/bin/orion-snapshot status >/dev/null 2>&1 || true
say "Done. orion-snapshot status shows what's there; orion-ctl undo goes back to before the last update."
