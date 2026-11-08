#!/usr/bin/env bash
# OrionOS rescue & privacy basics (review items B6, B7, H9, H11, H15; files in os/rescue, os/privacy, os/greeter).
#
#   bash os/image/os-rescue.sh      (as root: inside the image chroot, on the lab VM, or on an installed OrionOS)
#
# Called from os/lab/provision.sh. Safe to re-run.
#   /opt/orion/rescue/                 the Rescue panel (orion-rescue), the Rescue desktop session, saved-logs helper
#   /usr/local/bin/orion-rescue        Ctrl+Alt+Delete / Super+Escape; opens by itself when the Orion screen keeps
#                                      crashing (os/lab/bin/orion-hud)
#   /etc/pam.d/greetd                  "Start Orion again" without a password while OrionOS starts without one
#                                      (orion-login-auto-ok: only LOGIN_MODE=auto, only the login user)
#   /opt/orion/privacy/                first password (first-run), the phone port (pairing / Home networks)
#   NetworkManager hook                port 9121 follows the networks marked Home
#   /opt/orion/safety/orion-approval-check, /opt/orion/getapps/orion-install-gate   phone approvals checked by root
#   orion-journal-vault.service        USB stick: saved, size-capped logs while the vault is unlocked
#   orion-log2ram.service (+ timer)    USB stick (profile image): those logs live in RAM, copied to the vault hourly
#   ORION_USER=orion ORION_PROFILE=vm|baremetal|image
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OS="$SRC/os"
ORION_USER="${ORION_USER:-orion}"
PROFILE="${ORION_PROFILE:-$(cat /etc/orion/profile 2>/dev/null || echo baremetal)}"
say() { printf '\033[1;36m[os-rescue] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
rules() {   # install a polkit rule file for the real session user name
  sed "s/subject.user != \"orion\"/subject.user != \"$ORION_USER\"/; s/subject.user == \"orion\"/subject.user == \"$ORION_USER\"/" "$1" > "$2"
  chmod 644 "$2"
}

say "Rescue panel and Rescue desktop"
install -d -m 755 /opt/orion/rescue /opt/orion/rescue/orion_rescue
install -m 755 "$OS/rescue/orion-rescue" "$OS/rescue/orion-rescue-session" "$OS/rescue/orion-journal-vault" /opt/orion/rescue/
install -m 755 "$OS/rescue/orion-log2ram" /opt/orion/rescue/   # [v1gaps] logs in RAM on the USB stick
install -m 644 "$OS/rescue/sway-rescue.conf" /opt/orion/rescue/
install -m 644 "$OS/rescue/orion_rescue/"*.py /opt/orion/rescue/orion_rescue/
ln -sf /opt/orion/rescue/orion-rescue /usr/local/bin/orion-rescue
grep -q '^RESCUE_CMD=' /etc/orion/login.conf 2>/dev/null || \
  printf "# Started by the login screen's Rescue desktop button\nRESCUE_CMD=/opt/orion/rescue/orion-rescue-session\n" >> /etc/orion/login.conf

say "Login screen: the buttons work without a password while OrionOS starts without one"
PAM=/etc/pam.d/greetd
if [ -f "$PAM" ] && ! grep -q orion-login-auto-ok "$PAM"; then
  { head -n1 "$PAM"
    printf '# orion (os/greeter/orion-login-auto-ok): while LOGIN_MODE=auto, the login user starts without a password\n'
    printf 'auth     sufficient pam_exec.so quiet /opt/orion/greeter/orion-login-auto-ok\n'
    tail -n +2 "$PAM"; } > "$PAM.orion" && mv "$PAM.orion" "$PAM" && chmod 644 "$PAM"
fi
chown root:root /etc/orion/login.conf 2>/dev/null && chmod 644 /etc/orion/login.conf 2>/dev/null || true

say "Privacy: first password, phone port"
install -d -m 755 /opt/orion/privacy /usr/share/polkit-1/actions /etc/polkit-1/rules.d
install -m 755 -o root -g root "$OS/privacy/orion-first-password" "$OS/privacy/orion-phone-port" /opt/orion/privacy/
install -m 644 "$OS/privacy/ai.orion.privacy.policy" /usr/share/polkit-1/actions/
rules "$OS/privacy/50-orion-privacy.rules" /etc/polkit-1/rules.d/50-orion-privacy.rules
if [ -d /etc/NetworkManager ] || [ "$PROFILE" != vm ]; then
  install -d -m 755 /etc/NetworkManager/dispatcher.d
  install -m 755 -o root -g root "$OS/privacy/60-orion-phone-port" /etc/NetworkManager/dispatcher.d/60-orion-phone-port
fi

say "Phone approvals checked by root (unfreeze, installs without a person at the keyboard)"
install -d -m 755 /opt/orion/safety /opt/orion/getapps /etc/orion/phone-keys /var/lib/orion
install -m 755 -o root -g root "$OS/safety/orion-approval-check" /opt/orion/safety/orion-approval-check
install -m 755 -o root -g root "$OS/getapps/orion-install-gate" /opt/orion/getapps/orion-install-gate
install -m 644 "$OS/getapps/ai.orion.getapps.policy" /usr/share/polkit-1/actions/

say "Saved logs on the USB stick's vault; locked-in log limits elsewhere"
install -m 644 "$OS/rescue/orion-journal-vault.service" /etc/systemd/system/orion-journal-vault.service
systemctl enable orion-journal-vault.service 2>/dev/null || \
  { install -d /etc/systemd/system/sysinit.target.wants
    ln -sf /etc/systemd/system/orion-journal-vault.service /etc/systemd/system/sysinit.target.wants/; }

# [v1gaps] USB stick only: logs in RAM, copied to the vault hourly and at shutdown (orion-log2ram; agrees with the
# saved logs above: orion-journal-vault caps the journal to fit the RAM disk when this runs)
if [ "$PROFILE" = image ]; then
  say "USB stick: logs in RAM, copied to the vault every hour"
  install -m 644 "$OS/rescue/orion-log2ram.service" "$OS/rescue/orion-log2ram-sync.service" \
    "$OS/rescue/orion-log2ram-sync.timer" /etc/systemd/system/
  systemctl enable orion-log2ram.service orion-log2ram-sync.timer 2>/dev/null || {
    install -d /etc/systemd/system/sysinit.target.wants /etc/systemd/system/timers.target.wants
    ln -sf /etc/systemd/system/orion-log2ram.service /etc/systemd/system/sysinit.target.wants/
    ln -sf /etc/systemd/system/orion-log2ram-sync.timer /etc/systemd/system/timers.target.wants/; }
fi

say "Done. Ctrl+Alt+Delete opens the Rescue panel; the login screen offers Start Orion again / Rescue desktop."
