#!/usr/bin/env bash
# OrionOS next to Windows, and boot counting on installed drives (files in os/dualboot; review H14, B5, features 3
# and 7).
#
#   bash os/image/os-dualboot.sh      (as root: inside the image chroot, on the lab VM, or on an installed OrionOS)
#
# Called from os/lab/provision.sh. Safe to re-run.
#   /usr/local/bin/orion-restart-windows    restart into Windows once (firmware BootNext, else grub-reboot)
#   /usr/local/bin/orion-windows-files      open the Windows drive read-only (BitLocker: you type the recovery key)
#   /usr/local/bin/orion-bootcount          after an update: two starts to get healthy, else the snapshot from before
#   /opt/orion/dualboot/                    orion_dualboot.py, orion_partsel.py (orion-install-disk), root helper
#   /etc/grub.d/01_orion_bootcount          GRUB's side of the boot counting
#   polkit: the person at the computer may restart into Windows without the admin password; nothing else
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
D="$SRC/os/dualboot"
ORION_USER="${ORION_USER:-orion}"
export DEBIAN_FRONTEND=noninteractive
say() { printf '\033[1;36m[os-dualboot] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

say "Packages: efibootmgr (firmware boot entries), udisks2 + ntfs-3g (Windows files, BitLocker unlock)"
missing=""
for p in efibootmgr udisks2 ntfs-3g; do dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
if [ -n "$missing" ]; then
  apt-get update -q || true
  # shellcheck disable=SC2086
  apt-get install -y -q --no-install-recommends $missing || echo "!! couldn't install:$missing"
fi

say "Commands"
install -d -m 755 /opt/orion/dualboot
install -m 644 "$D/orion_dualboot.py" "$D/orion_partsel.py" /opt/orion/dualboot/
install -m 755 -o root -g root "$D/orion-dualboot-helper" /opt/orion/dualboot/orion-dualboot-helper
install -m 755 "$D/orion-restart-windows" "$D/orion-windows-files" "$D/orion-bootcount" /usr/local/bin/
install -d -m 755 /usr/share/applications
install -m 644 "$D/applications/"*.desktop /usr/share/applications/

say "Permissions: restart into Windows once, without the admin password, for the person at the computer"
install -d -m 755 /usr/share/polkit-1/actions /etc/polkit-1/rules.d
install -m 644 "$D/polkit/ai.orion.dualboot.policy" /usr/share/polkit-1/actions/ai.orion.dualboot.policy
sed "s/subject.user != \"orion\"/subject.user != \"$ORION_USER\"/" "$D/polkit/51-orion-dualboot.rules" \
  > /etc/polkit-1/rules.d/51-orion-dualboot.rules
chmod 644 /etc/polkit-1/rules.d/51-orion-dualboot.rules

say "Boot counting after updates (installed drives with snapshots)"
install -d -m 755 /etc/grub.d /etc/sway/config.d
install -m 755 "$D/01_orion_bootcount" /etc/grub.d/01_orion_bootcount
install -m 644 "$D/systemd/orion-bootcount-good.service" /etc/systemd/system/
install -m 644 "$D/sway-dualboot.conf" /etc/sway/config.d/62-orion-dualboot.conf
systemctl enable orion-bootcount-good.service 2>/dev/null || { install -d -m 755 /etc/systemd/system/graphical.target.wants
  ln -sf /etc/systemd/system/orion-bootcount-good.service /etc/systemd/system/graphical.target.wants/orion-bootcount-good.service; }
say "Done. orion-restart-windows status says whether Windows can be started from here."
