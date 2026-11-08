#!/usr/bin/env bash
# OrionOS displays & printing (docs/ORIONOS_COMPLETE_PLAN.md Part 1, "Several monitors", "Printing and scanning").
# What each piece does and why: os/hardware/README.md.
#
#   bash os/image/os-hardware.sh            (as root: in the image chroot, on the lab VM, or on an installed OrionOS)
#   bash os/image/os-hardware.sh firewall   only the firewall rule (provision.sh step 9 calls this after its reset)
#
# Called from os/lab/provision.sh step 6. Real-hardware profiles also get Bluetooth audio, laptop sound firmware,
# the password keyring, thermald and ubuntu-drivers (H7). Safe to re-run: every step checks before it changes anything.
#   ORION_USER=orion        the session user (polkit rule, config folders)
#   ORION_PROFILE=vm|baremetal|image   vm skips ipp-usb (USB printers); network printing and kanshi stay
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OS="$SRC/os"
HW="$OS/hardware"
ORION_USER="${ORION_USER:-orion}"
PROFILE="${ORION_PROFILE:-$(cat /etc/orion/profile 2>/dev/null || { systemd-detect-virt -q 2>/dev/null && echo vm || echo baremetal; })}"
export DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1

say() { printf '\033[1;36m[os-hardware] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
live() { ! systemd-detect-virt -q --chroot 2>/dev/null && [ -d /run/systemd/system ]; }

firewall() {
  # Printers and scanners announce themselves with mDNS. Ubuntu's ufw already lets the multicast replies in
  # (before.rules: 224.0.0.251 / ff02::fb port 5353); some printers answer straight back to this computer
  # instead, so those replies are allowed too: only from mDNS port 5353, only from local network addresses
  # (never the internet). Printing itself is outgoing and needs nothing.
  command -v ufw >/dev/null || { echo "   (no ufw here: nothing to open)"; return 0; }
  local net nets="192.168.0.0/16 10.0.0.0/8 172.16.0.0/12 169.254.0.0/16"
  # IPv6 link-local too, where the kernel has IPv6 (ufw refuses IPv6 rules otherwise)
  if [ -d /proc/sys/net/ipv6 ] && grep -qs '^IPV6=yes' /etc/default/ufw; then nets="$nets fe80::/10"; fi
  for net in $nets; do
    ufw allow in proto udp from "$net" port 5353 to any port 5353 \
      comment "mDNS replies: printers and scanners on your network (LAN only)" >/dev/null
  done
  echo "   mDNS replies from the local network allowed (udp 5353 -> 5353)"
}

if [ "${1:-}" = firewall ]; then
  say "Firewall: printer and scanner discovery on the local network"
  firewall
  exit 0
fi

say "Packages ($PROFILE): screens (kanshi, wdisplays), printing (CUPS, driverless), scanning (Simple Scan, sane-airscan)"
PKGS="kanshi wdisplays jq python3 libnotify-bin tzdata
      cups cups-client cups-filters avahi-daemon libnss-mdns system-config-printer cups-pk-helper
      simple-scan sane-airscan"
# USB printers and scanners speak IPP over USB through ipp-usb. A virtual machine has no USB printer to drive.
[ "$PROFILE" = vm ] || PKGS="$PKGS ipp-usb"
missing=""
for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
if [ -n "$missing" ]; then
  apt-get update -q
  # shellcheck disable=SC2086
  apt-get install -y -q --no-install-recommends $missing
fi

if [ "$PROFILE" != vm ]; then
  # Real laptops and desktops (H7): Bluetooth headphones (PipeWire's Bluetooth part), the sound chips of most laptops
  # since 2019 (Intel SOF firmware + ALSA's device profiles), saved passwords for apps (GNOME Keyring, unlocked by
  # your login password when you have one), Intel's overheating guard (thermald), and Ubuntu's driver finder
  # (ubuntu-drivers: NVIDIA and other extra drivers; orion-install-disk uses it).
  say "Real hardware: Bluetooth audio, laptop sound chips, saved passwords, cooling, driver finder"
  HWP="libspa-0.2-bluetooth firmware-sof-signed alsa-ucm-conf gnome-keyring libpam-gnome-keyring thermald ubuntu-drivers-common"
  missing=""
  for p in $HWP; do dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
  if [ -n "$missing" ]; then
    apt-get update -q >/dev/null 2>&1 || true
    # shellcheck disable=SC2086
    apt-get install -y -q --no-install-recommends $missing || echo "!! some hardware packages didn't install:$missing"
  fi
  systemctl enable thermald.service >/dev/null 2>&1 || true
  # The keyring opens with the password you log in with (the starry login screen). With auto-login there is no
  # password to unlock it, so apps ask for the keyring password once instead.
  PG=/etc/pam.d/greetd
  if [ -f "$PG" ] && [ -f /usr/lib/x86_64-linux-gnu/security/pam_gnome_keyring.so ] && ! grep -q pam_gnome_keyring "$PG"; then
    printf '\n# OrionOS (os-hardware.sh): unlock the app password keyring with the login password\nauth     optional pam_gnome_keyring.so\nsession  optional pam_gnome_keyring.so auto_start\n' >> "$PG"
  fi
fi

say "Printing: CUPS for this computer only, driverless network printers, no cups-browsed"
# cups-browsed isn't needed: CUPS 2.4 makes a queue on demand for any driverless (IPP Everywhere / AirPrint)
# printer it sees through Avahi. It is the part that listened on UDP 631 for anyone (CVE-2024-47176), so if
# something pulled it in, it stays off.
if dpkg -s cups-browsed >/dev/null 2>&1; then
  if live; then systemctl disable --now cups-browsed.service >/dev/null 2>&1 || true; fi
  systemctl mask cups-browsed.service >/dev/null 2>&1 || true
fi
C=/etc/cups/cupsd.conf
if [ -f "$C" ]; then
  # The CUPS web page and the scheduler answer this computer only, and its printers are never shared.
  sed -i -E 's/^Port 631$/Listen localhost:631/; /^Listen (\*|0\.0\.0\.0|\[::\]):631$/d; s/^Browsing (Yes|On)$/Browsing No/;
             s/^([[:space:]]*)Allow @LOCAL$/\1# Allow @LOCAL  (OrionOS: printers are not shared)/' "$C"
  grep -q '^Listen localhost:631$' "$C" || sed -i '1i Listen localhost:631' "$C"
  grep -q '^Browsing No$' "$C" || printf 'Browsing No\n' >> "$C"
  cupsd -t >/dev/null || { echo "!! CUPS says its configuration is broken: cupsd -t"; exit 1; }
fi
# ipp-usb keeps USB printers to this computer (interface = loopback, Ubuntu's default); make sure of it.
if [ -f /etc/ipp-usb/ipp-usb.conf ]; then
  sed -i -E 's/^([[:space:]]*interface[[:space:]]*=[[:space:]]*)all\b/\1loopback/' /etc/ipp-usb/ipp-usb.conf
fi
for u in cups.service cups.socket avahi-daemon.service; do systemctl enable "$u" >/dev/null 2>&1 || true; done

say "Orion's commands: orion-displays, orion-printers, orion-check-hardware (the hardware report card), orion-gpu-env"
install -d -m 755 /opt/orion/hardware /opt/orion/bin
install -m 644 "$HW/orion_hardware.py" "$HW/orion_hwcheck.py" /opt/orion/hardware/
for c in orion-displays orion-printers orion-check-hardware orion-gpu-env; do
  install -m 755 "$OS/lab/bin/$c" "/opt/orion/bin/$c"
  ln -sf "/opt/orion/bin/$c" "/usr/local/bin/$c"
done

say "Screens: kanshi and the screen watcher as user services; lid shut while docked = panel off, no sleep"
install -d -m 755 /etc/systemd/user /etc/systemd/system /etc/systemd/logind.conf.d
install -m 644 "$HW/systemd/orion-kanshi.service" "$HW/systemd/orion-displays.service" /etc/systemd/user/
install -m 644 "$HW/systemd/orion-papersize@.service" "$HW/systemd/orion-papersize.path" /etc/systemd/system/
install -m 644 "$HW/50-orion-lid.conf" /etc/systemd/logind.conf.d/50-orion-lid.conf
systemctl enable orion-papersize.path >/dev/null 2>&1 || true
# logind reads its settings when it starts; changing them under a running session is applied at the next boot.

say "Permissions: the session user may add printers and set the paper size (polkit), nothing more"
install -d -m 755 /etc/polkit-1/rules.d
sed "s/subject.user != \"orion\"/subject.user != \"$ORION_USER\"/" "$HW/50-orion-hardware.rules" \
  > /etc/polkit-1/rules.d/50-orion-hardware.rules
chmod 644 /etc/polkit-1/rules.d/50-orion-hardware.rules

say "Launcher: \"Printers\" and \"Scan documents\""
# system-config-printer's entry is already called "Printers". Simple Scan calls itself "Document Scanner": same
# file id in /usr/local/share/applications (read first), so nothing else changes.
install -d -m 755 /usr/local/share/applications
for f in simple-scan.desktop org.gnome.SimpleScan.desktop; do
  [ -f "/usr/share/applications/$f" ] || continue
  sed -e 's/^Name=Document Scanner$/Name=Scan documents/; s/^Name=Simple Scan$/Name=Scan documents/' \
    "/usr/share/applications/$f" > "/usr/local/share/applications/$f.tmp"
  if cmp -s "/usr/local/share/applications/$f.tmp" "/usr/local/share/applications/$f"; then
    rm -f "/usr/local/share/applications/$f.tmp"
  else
    mv "/usr/local/share/applications/$f.tmp" "/usr/local/share/applications/$f"
  fi
done
update-desktop-database -q /usr/local/share/applications 2>/dev/null || true

say "Paper size from the time zone (Letter in the US, Canada, Mexico...; A4 elsewhere)"
ORION_BASICS_DIR="$OS/basics" /opt/orion/bin/orion-printers paper auto --system

if id "$ORION_USER" >/dev/null 2>&1; then
  H="$(getent passwd "$ORION_USER" | cut -d: -f6)"
  [ -d "$H/.config" ] && install -d -o "$ORION_USER" -g "$ORION_USER" "$H/.config/kanshi" "$H/.config/orion"
fi

say "Firewall"
firewall
say "Done."
