#!/usr/bin/env bash
# OrionOS complete plan, step 2 (docs/ORIONOS_COMPLETE_PLAN.md Part 1 "App store" and "File sharing", Part 3
# "updates are tested, then rolled back"): Flatpak with Flathub, the "Get apps" window, safe weekly app updates,
# and "Share files" (LocalSend). What each piece does and why: os/getapps/README.md.
#
#   bash os/image/os-getapps.sh        (as root: inside the image chroot, on the lab VM, or an installed OrionOS)
#
# Called by os/lab/provision.sh for every Orion desktop profile (image, vm, baremetal); never for the boardroom.
# Safe to re-run. Nothing is downloaded from Flathub here: the remote is set up from the repo file shipped in
# os/getapps/flathub.flatpakrepo (Flathub's own file, key checked), and apps are only ever installed by the owner.
#   ORION_USER=orion      the session user (gets the weekly update timer and LocalSend's launcher route)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(cd "$HERE/.." && pwd)"
GA="$OS/getapps"
ORION_USER="${ORION_USER:-orion}"
PROFILE="${ORION_PROFILE:-$(cat /etc/orion/profile 2>/dev/null || echo baremetal)}"
H="/home/$ORION_USER"
FLATHUB_FPR=6E5C05D979C76DAF93C081354184DD4D907A7CAE
export DEBIAN_FRONTEND=noninteractive
say() { printf '\033[1;34m[os-getapps] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
case "$PROFILE" in
  *boardroom*) say "boardroom profile: no app store here"; exit 0 ;;
esac

say "Packages: Flatpak, the settings portal (dark mode and icons for Flatpak apps), the window's GTK 4 stack"
PKGS="flatpak xdg-desktop-portal-gtk python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libnotify-bin iproute2 ufw"
missing=""
for p in $PKGS; do dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
if [ -n "$missing" ]; then
  apt-get update -q
  # shellcheck disable=SC2086
  apt-get install -y -q --no-install-recommends $missing
fi

say "Flathub remote (system-wide, from the shipped repo file; key $FLATHUB_FPR)"
# Check the shipped file's key before trusting it, so a damaged or swapped file is never installed: the key's
# SHA-256 is pinned here, and when gpg is present its fingerprint is checked too.
FLATHUB_KEY_SHA256=f4d6c33e44d9627ad3d2e32a5c8de25769b52f6454157e2db233e7d35d358a9a
KEYSUM="$(sed -n 's/^GPGKey=//p' "$GA/flathub.flatpakrepo" | sha256sum | cut -d' ' -f1)"
[ "$KEYSUM" = "$FLATHUB_KEY_SHA256" ] || { echo "!! the shipped Flathub key has changed ($KEYSUM); remote not added"; exit 1; }
if command -v gpg >/dev/null; then
  G="$(mktemp -d)"
  FPR="$(sed -n 's/^GPGKey=//p' "$GA/flathub.flatpakrepo" | base64 -d | GNUPGHOME="$G" gpg --batch --with-colons \
         --show-keys 2>/dev/null | awk -F: '/^fpr:/{print $10; exit}')"
  rm -rf "$G"
  [ "$FPR" = "$FLATHUB_FPR" ] || { echo "!! the shipped Flathub key isn't Flathub's ($FPR); remote not added"; exit 1; }
fi
if ! flatpak remotes --system --columns=name | grep -qx flathub; then
  # remote-add reads the local file; the "couldn't update extra metadata" warning without network is harmless.
  flatpak remote-add --system --if-not-exists --from flathub "$GA/flathub.flatpakrepo" 2>&1 | grep -v '^Warning: Could not update extra metadata' || true
fi
flatpak remotes --system --columns=name | grep -qx flathub || { echo "!! Flathub remote could not be added"; exit 1; }

say "Get apps and Share files -> /opt/orion/getapps, commands orion-apps and orion-share"
install -d -m 755 /opt/orion /opt/orion/getapps /opt/orion/bin
for f in orion_apps.py orion_share.py orion_share_firewall.py getapps_ui.py share_ui.py getapps.css catalog.json \
         flathub.flatpakrepo README.md; do
  install -m 644 -o root -g root "$GA/$f" /opt/orion/getapps/
done
for b in orion-apps orion-share; do
  install -m 755 -o root -g root "$GA/bin/$b" /opt/orion/bin/$b
  ln -sf "/opt/orion/bin/$b" "/usr/local/bin/$b"
done
install -m 644 "$GA/applications/orion-getapps.desktop" "$GA/applications/orion-share.desktop" /usr/share/applications/
update-desktop-database -q /usr/share/applications 2>/dev/null || true

say "Permissions: install and update apps from Flathub without a password; LocalSend's port toggle (polkit)"
install -d -m 755 /etc/polkit-1/rules.d
install -m 644 "$GA/50-orion-getapps.rules" /etc/polkit-1/rules.d/50-orion-getapps.rules

say "Share files: LocalSend's firewall port, closed by default and at every boot"
install -m 644 "$GA/systemd/orion-share-firewall.service" "$GA/systemd/orion-share-firewall-reset.service" /etc/systemd/system/
install -d /etc/systemd/system/multi-user.target.wants
ln -sf /etc/systemd/system/orion-share-firewall-reset.service /etc/systemd/system/multi-user.target.wants/
# a re-run on a live system closes it too (the share window opens it again while LocalSend runs)
if [ -d /run/systemd/system ] && ! systemd-detect-virt -q --chroot 2>/dev/null; then
  systemctl daemon-reload || true
  systemctl stop orion-share-firewall.service 2>/dev/null || true
fi

say "Weekly safe app updates for $ORION_USER (level 2: update, check, roll back if broken, then tell you)"
install -d -m 755 /etc/systemd/user
install -m 644 "$GA/systemd/orion-apps-update.service" "$GA/systemd/orion-apps-update.timer" /etc/systemd/user/
if id "$ORION_USER" >/dev/null 2>&1; then
  # Only the session user's timer (not every account: the agent user may not install anything).
  install -d -o "$ORION_USER" -g "$ORION_USER" "$H/.config" "$H/.config/systemd" "$H/.config/systemd/user" \
    "$H/.config/systemd/user/timers.target.wants" "$H/.local" "$H/.local/share" "$H/.local/share/applications"
  ln -sf /etc/systemd/user/orion-apps-update.timer "$H/.config/systemd/user/timers.target.wants/orion-apps-update.timer"
  chown -h "$ORION_USER:$ORION_USER" "$H/.config/systemd/user/timers.target.wants/orion-apps-update.timer"
  # LocalSend's own launcher goes through Share files (so the port opens while it runs).
  install -m 644 -o "$ORION_USER" -g "$ORION_USER" "$GA/applications/org.localsend.localsend_app.desktop" \
    "$H/.local/share/applications/org.localsend.localsend_app.desktop"
  # The settings file: written once; Settings (and `orion-apps config`) change it.
  if [ ! -f "$H/.config/orion/apps.json" ]; then
    install -d -o "$ORION_USER" -g "$ORION_USER" "$H/.config/orion"
    printf '{\n  "auto_update": "auto"\n}\n' > "$H/.config/orion/apps.json"
    chown "$ORION_USER:$ORION_USER" "$H/.config/orion/apps.json"
  fi
fi

say "Sway: Get apps and Share files float in the middle"
install -d -m 755 /etc/sway/config.d
install -m 644 "$GA/sway-getapps.conf" /etc/sway/config.d/61-orion-getapps.conf

# Flatpak apps and the Orion look (README "How Flatpak apps look"): they get dark mode, Orion's icons and cursor,
# and Orion's fonts from the host through the settings portal (gsettings set by os/theme/install-desktop-theme.sh).
# The Orion Glass GTK theme itself can't reach inside their sandbox without a Flatpak theme package, so nothing
# is overridden here.
say "Done. Open \"Get apps\" from the launcher, or run: orion-apps open"
