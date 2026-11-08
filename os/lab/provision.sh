#!/usr/bin/env bash
# OrionOS — orion-lab provisioning (VM 1).
# Turns a fresh Ubuntu Server 24.04 cloud image into the OrionOS appliance:
#   greetd autologin -> Sway -> Orion HUD full-screen on workspace 1, the daemon as a user service,
#   rootless Podman sandboxes for agent work, a RAM-disk for builds, inbound locked to the boardroom and
#   Tailscale, and an outbound VPN killswitch that ships OFF (orion-killswitch enable … turns it on).
#
# Run as root from the bundle:  bash /opt/orion-src/os/lab/provision.sh
# Safe to re-run: every step is idempotent. Log: /var/log/orion-provision.log
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # bundle root (has apps/, os/, dist/)
OS="$SRC/os"
ORION_USER=orion
AGENT_USER=orion-agent
BOARDROOM_IP="${ORION_BOARDROOM_IP:-10.77.0.11}"
TURBO_SIZE="${ORION_TURBO_SIZE:-1536M}"
WITH_VOICE="${ORION_WITH_VOICE:-0}"
# vm = the VirtualBox lab; baremetal = your laptop partition or USB SSD (os/baremetal/install-orion.sh);
# image = the OrionOS 1.0 USB image build (os/image/build.sh)
PROFILE="${ORION_PROFILE:-$(systemd-detect-virt -q 2>/dev/null && echo vm || echo baremetal)}"
export DEBIAN_FRONTEND=noninteractive

step() { printf '\n\033[1;36m[orion-lab] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

step "1/10 Hostname, clock ($PROFILE)"
if [ "$PROFILE" = vm ]; then
  hostnamectl set-hostname orion-lab
  # UTC until you pick a zone (Settings > Time zone); a re-provision keeps the one you picked (OS basics).
  [ -f "/home/$ORION_USER/.config/orion/timezone-chosen" ] || timedatectl set-timezone UTC || true
  grep -q "orion-lab" /etc/hosts || echo "127.0.1.1 orion-lab" >> /etc/hosts
  grep -q "orion-boardroom" /etc/hosts || echo "$BOARDROOM_IP orion-boardroom" >> /etc/hosts
else
  # Your own machine keeps your time zone; the name is what your phone and router see.
  if [ "$PROFILE" = baremetal ]; then hostnamectl set-hostname orionos || true; fi
  grep -q "orionos" /etc/hosts || echo "127.0.1.1 orionos" >> /etc/hosts
fi

step "2/10 Packages (Sway session, wlroots tools, Podman, OCR, Python)"
apt-get update -q
apt-get install -y -q --no-install-recommends \
  sway swayidle swaybg swaylock foot wofi mako-notifier libnotify-bin greetd \
  grim wtype wl-clipboard xdg-desktop-portal-wlr xwayland \
  pipewire pipewire-pulse wireplumber libportaudio2 \
  fonts-noto-core fonts-noto-color-emoji \
  podman uidmap slirp4netns fuse-overlayfs \
  tesseract-ocr python3 python3-venv python3-pip python3-dev build-essential \
  git curl jq ufw nftables unattended-upgrades ca-certificates \
  dbus-user-session \
  polkitd qrencode thunar thunar-archive-plugin tumbler papirus-icon-theme gnome-themes-extra wdisplays pavucontrol \
  cage python3-gi gir1.2-gtk-4.0 gir1.2-webkit-6.0
# VirtualBox's guest tools only on the VirtualBox lab (H7; real hardware and the USB image don't need them).
[ "$PROFILE" = vm ] && { apt-get install -y -q --no-install-recommends virtualbox-guest-utils || true; }
if [ "$PROFILE" != vm ]; then
  # Real hardware: Wi-Fi, Bluetooth, laptop power and brightness, every redistributable firmware.
  apt-get install -y -q --no-install-recommends network-manager bluez blueman power-profiles-daemon \
    brightnessctl linux-firmware wireless-regdb fwupd || true
fi

step "3/10 Users: $ORION_USER (the session, no sudo) and $AGENT_USER (sandboxed agent work, no sudo)"
id "$ORION_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "OrionOS" "$ORION_USER"
id "$AGENT_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "Orion agents" "$AGENT_USER"
usermod -aG video,render,input "$ORION_USER" || true
chmod 750 "/home/$ORION_USER" "/home/$AGENT_USER"   # the agent user must never read Orion's data dir
loginctl enable-linger "$AGENT_USER" || true   # agents' rootless containers outlive a login

step "4/10 Orion daemon -> /opt/orion (read-only to the session user)"
install -d -m 755 /opt/orion /opt/orion/bin /etc/orion
rm -rf /opt/orion/daemon /opt/orion/boardroom-client
cp -r "$SRC/apps/daemon" /opt/orion/daemon
rm -rf /opt/orion/daemon/.venv /opt/orion/daemon/__pycache__ /opt/orion/daemon/.env
[ -d /opt/orion/venv ] || python3 -m venv /opt/orion/venv
/opt/orion/venv/bin/pip install -q --upgrade pip
grep -v -E '^(kokoro|faster-whisper)' /opt/orion/daemon/requirements.txt > /tmp/orion-req.txt
/opt/orion/venv/bin/pip install -q -r /tmp/orion-req.txt
if [ "$WITH_VOICE" = "1" ]; then
  # CPU-only PyTorch keeps the download near 200 MB instead of the multi-GB CUDA build.
  /opt/orion/venv/bin/pip install -q torch --index-url https://download.pytorch.org/whl/cpu
  /opt/orion/venv/bin/pip install -q "kokoro>=0.8.4" "faster-whisper>=1.0.0"
fi
chown -R root:root /opt/orion
chmod -R a+rX /opt/orion

step "5/10 Orion HUD (.deb built for Ubuntu 24.04)"
DEB="$(ls "$SRC"/dist/*.deb "$OS"/dist/*.deb 2>/dev/null | head -n1 || true)"
if [ -n "$DEB" ]; then
  apt-get install -y -q "$DEB"
else
  echo "!! No HUD .deb in the bundle (dist/). The session will start, but without the HUD."
fi

step "6/10 OrionOS commands, daemon env, session files"
find "$OS/lab/bin" -maxdepth 1 -type f -exec install -m 755 {} /opt/orion/bin/ \;   # files only (skips __pycache__ and other folders)
for f in /opt/orion/bin/*; do ln -sf "$f" "/usr/local/bin/$(basename "$f")"; done
[ -f /etc/orion/daemon.env ] || install -m 644 "$OS/lab/daemon.env" /etc/orion/daemon.env
# settings added in later versions reach existing labs too (your own values are never overwritten)
grep -q '^ORION_SETUP_AGENT=' /etc/orion/daemon.env || printf '\n# First-run setup agent (each step needs your approval)\nORION_SETUP_AGENT=1\n' >> /etc/orion/daemon.env
# [B7] screen reading is opt-in now: the old system-wide "on" goes; a choice you made yourself lives in ~/.config/orion
sed -i 's/^ORION_CAPTURE_ENABLED=true$/ORION_CAPTURE_ENABLED=false/' /etc/orion/daemon.env
if [ "$PROFILE" != vm ]; then
  # On your own machine: no stand-in agents spamming test decisions, and Ollama runs right here.
  sed -i 's/^ORION_BOARDROOM_SIM_MINUTES=.*/ORION_BOARDROOM_SIM_MINUTES=0/; s#^OLLAMA_URL=.*#OLLAMA_URL=http://127.0.0.1:11434#' /etc/orion/daemon.env
fi
# The starry login screen (os/greeter). Whether it's used is your choice: orion-ctl login auto|password.
# [H10] /etc/greetd/config.toml is only rewritten while it's still Orion's (unchanged, or one of Orion's templates)
GREETD_OURS=0; sh "$OS/lab/orion-owned" may-write /etc/greetd/config.toml /opt/orion/greeter/greetd-auto.toml /opt/orion/greeter/greetd-password.toml && GREETD_OURS=1
install -d -m 755 /opt/orion/greeter /opt/orion/greeter/web
install -m 755 "$OS/greeter/orion-greeter" "$OS/greeter/orion-greeter-start" "$OS/greeter/orion-login-mode" \
  "$OS/greeter/greetd_config.py" "$OS/greeter/orion-login-auto-ok" /opt/orion/greeter/
install -m 644 "$OS/greeter/greetd_ipc.py" "$OS/greeter/greeter_logic.py" "$OS/greeter/login.conf" "$OS/greeter/greetd-password.toml" /opt/orion/greeter/
install -m 644 "$OS/lab/greetd.toml" /opt/orion/greeter/greetd-auto.toml
install -m 644 "$OS/greeter/web/"* /opt/orion/greeter/web/
usermod -aG video,render _greetd 2>/dev/null || true   # the login screen draws with the GPU when there is one
# Auto-login stays the default (the VM lab; the USB stick's vault passphrase already guards it). A re-run keeps your choice.
[ -f /etc/orion/login.conf ] || install -m 644 "$OS/greeter/login.conf" /etc/orion/login.conf
# The password is only asked once orion has one; until then auto-login at power-on (never a screen nobody can pass).
# Either way the graphical login screen ("Start Orion again" / "Rescue desktop") is what you see after logging out
# (B6: os/greeter/greetd_config.py; the text login is only its fallback).
if [ "$GREETD_OURS" != 1 ]; then
  echo "   /etc/greetd/config.toml was changed outside Orion, so it's left as it is (orion-ctl login resets it)"   # [H10]
else
  if grep -q '^LOGIN_MODE=password' /etc/orion/login.conf && [ "$(passwd -S "$ORION_USER" | awk '{print $2}')" = P ]; then
    LM=password; else LM=auto; fi
  python3 /opt/orion/greeter/greetd_config.py write "$LM" --user "$ORION_USER" || \
    install -m 644 "$([ "$LM" = password ] && echo "$OS/greeter/greetd-password.toml" || echo "$OS/lab/greetd.toml")" /etc/greetd/config.toml
fi
install -d -m 755 /etc/systemd/user
install -m 644 "$OS/lab/orion-daemon.service" /etc/systemd/user/orion-daemon.service
# Login items as supervised user services (sway-session.target; the Sway config only runs orion-session-start)
install -m 644 "$OS/lab/systemd/"*.service "$OS/lab/systemd/"*.target /etc/systemd/user/
H=/home/$ORION_USER
install -d -o "$ORION_USER" -g "$ORION_USER" "$H/.config" "$H/.config/sway" "$H/.config/foot" "$H/.config/mako" "$H/.config/orion"
bash "$OS/lab/orion-sway-config" "$H" "$ORION_USER" "$PROFILE"   # [H10] Orion's part in /etc/orion/sway/orion.conf; ~/.config/sway/config stays yours
install -m 644 -o "$ORION_USER" -g "$ORION_USER" "$OS/lab/foot.ini" "$H/.config/foot/foot.ini"
install -m 644 -o "$ORION_USER" -g "$ORION_USER" "$OS/lab/mako-config" "$H/.config/mako/config"
install -m 644 -o "$ORION_USER" -g "$ORION_USER" "$OS/theme/wallpaper.png" "$H/.config/sway/wallpaper.png"
# Orion theme for every GTK app, the launcher, and Orion's own entries in it.
install -d -o "$ORION_USER" -g "$ORION_USER" "$H/.config/gtk-3.0" "$H/.config/gtk-4.0" "$H/.config/wofi" \
  "$H/.local" "$H/.local/share" "$H/.local/share/applications" "$H/.config/Thunar"
for v in gtk-3.0 gtk-4.0; do
  install -m 644 -o "$ORION_USER" -g "$ORION_USER" "$OS/theme/$v/gtk.css" "$OS/theme/$v/settings.ini" "$H/.config/$v/"
done
install -m 644 -o "$ORION_USER" -g "$ORION_USER" "$OS/theme/wofi/style.css" "$OS/theme/wofi/config" "$H/.config/wofi/"
install -m 644 "$OS/lab/applications/"*.desktop /usr/share/applications/
printf 'file://%s/projects Projects\nfile://%s/Captures Captures\nfile:///mnt/orion_turbo Turbo\n' "$H" "$H" > "$H/.config/gtk-3.0/bookmarks"
chown "$ORION_USER:$ORION_USER" "$H/.config/gtk-3.0/bookmarks"
install -d -o "$ORION_USER" -g "$ORION_USER" "$H/projects" "$H/Captures"
[ -f "$H/.config/orion/daemon.env" ] || install -m 600 -o "$ORION_USER" -g "$ORION_USER" /dev/null "$H/.config/orion/daemon.env"
# Another login screen (Ubuntu Desktop's GDM, etc.) would fight greetd for the screen.
for dm in gdm3 gdm sddm lightdm; do systemctl disable "$dm" 2>/dev/null || true; done
ln -sf /usr/lib/systemd/system/greetd.service /etc/systemd/system/display-manager.service 2>/dev/null || true
# Orion's greeter owns the first console (Ubuntu's greetd unit expects tty7).
install -d /etc/systemd/system/greetd.service.d
printf '[Unit]\nAfter=getty@tty1.service\nConflicts=getty@tty1.service\n' > /etc/systemd/system/greetd.service.d/orion-tty1.conf
systemctl enable greetd
systemctl set-default graphical.target
ORION_USER="$ORION_USER" bash "$OS/image/art-boot.sh" provision   # Art Update (art-boot): boot hand-off, greeter shader, prompt, fetch, cursor, sound

echo "$PROFILE" > /etc/orion/profile
# Orion's first-run (password, screen reading, phone, brain, voice) opens once in the session on your own machine or
# the USB stick (orion-firstrun.service in sway-session.target); the VM lab skips it.
if [ "$PROFILE" = vm ]; then touch /etc/orion/no-firstrun; else rm -f /etc/orion/no-firstrun; fi

# Art Update desktop look: Orion Glass GTK theme, Orion icons, Thunar, VS Code theme, wallpapers, SwayFX (os/theme/install-desktop-theme.sh)
ORION_PROFILE="$PROFILE" ORION_USER="$ORION_USER" bash "$OS/theme/install-desktop-theme.sh" || echo "!! Orion desktop look step failed; the session still works"
# Complete plan step 1: Firefox (Mozilla's .deb), PDF/image/video/text/calculator/archive/office apps and default file associations (os/image/os-apps.sh)
ORION_PROFILE="$PROFILE" ORION_USER="$ORION_USER" bash "$OS/image/os-apps.sh" || echo "!! Everyday apps step failed; the session still works"
ORION_USER="$ORION_USER" bash "$OS/image/os-basics.sh" || echo "!! OS basics step failed; the session still works"   # OS basics: clock/time zone, screenshots, clipboard history, night light (os/basics/README.md)
ORION_USER="$ORION_USER" bash "$OS/image/os-settings.sh" || echo "!! Settings window step failed; the text menu still works"   # Complete plan step 2: the Settings window (os/settings), its root helpers and polkit rules
ORION_PROFILE="$PROFILE" ORION_USER="$ORION_USER" bash "$OS/image/os-hardware.sh" || echo "!! Displays & printing step failed; the session still works"   # Displays & printing: kanshi profiles per set of screens, CUPS driverless printing, scanning (os/hardware/README.md)
ORION_USER="$ORION_USER" bash "$OS/image/os-desktop.sh" || echo "!! Desktop (window buttons, Alt+Tab, tray, password window) step failed; the session still works"   # H5/H13: polkit agent, orion-windows, orion-keyboard, orion-shortcuts, orion-tray (os/image/os-desktop.sh)
# [look] Optional Hyprland session for Gravity mode, only when chosen (ORION_WITH_HYPRLAND=1); the default stays Sway
[ "${ORION_WITH_HYPRLAND:-0}" = 1 ] && { ORION_USER="$ORION_USER" bash "$OS/image/os-hyprland.sh" || echo "!! Hyprland session step failed; the Sway session still works"; }
ORION_PROFILE="$PROFILE" ORION_USER="$ORION_USER" bash "$OS/image/os-getapps.sh" || echo "!! Get apps step failed; the session still works"   # Complete plan step 2: Flatpak + Flathub, "Get apps", safe weekly app updates, "Share files" (os/getapps/README.md)
ORION_USER="$ORION_USER" bash "$OS/image/os-safety.sh" || echo "!! Safety net step failed; the session still works"
ORION_USER="$ORION_USER" bash "$OS/image/os-dualboot.sh" || echo "!! Dual boot / boot counting step failed; the session still works"   # [dualboot] Restart into Windows, Windows files, boot counting after updates (os/dualboot)   # Complete plan step 3: snapshots (btrfs/snapper, vault copies), kill switch root part (os/safety)
ORION_PROFILE="$PROFILE" ORION_USER="$ORION_USER" bash "$OS/image/os-everyone.sh" || echo "!! Accessibility/languages/guest/power step failed; the session still works"   # Complete plan step 6: screen reader, on-screen keyboard, high contrast, fcitx5 + emoji, guest account, laptop power (os/everyone/README.md)
bash "$OS/image/os-devtools.sh" || echo "!! Developer tools step failed; projects still run without just/mise"   # Toolkit: just + mise (Project Runbook), minisign (Release Butler)
ORION_PROFILE="$PROFILE" ORION_USER="$ORION_USER" bash "$OS/image/os-security.sh" || echo "!! Security check-up tools step failed; the session still works"   # Toolkit 19-21: osv-scanner, Lynis, AIDE, SMART, report timers
ORION_PROFILE="$PROFILE" ORION_USER="$ORION_USER" bash "$OS/image/os-rescue.sh" || echo "!! Rescue & privacy step failed; the session still works"   # B6/B7/H9/H11/H15: login screen buttons, Rescue panel, first password, phone port, saved logs

step "7/10 Turbo RAM-disk ($TURBO_SIZE at /mnt/orion_turbo)"
mkdir -p /mnt/orion_turbo
grep -q "/mnt/orion_turbo" /etc/fstab || \
  echo "tmpfs /mnt/orion_turbo tmpfs defaults,noatime,nosuid,nodev,mode=1777,size=$TURBO_SIZE 0 0" >> /etc/fstab
[ "$PROFILE" = image ] || mountpoint -q /mnt/orion_turbo || mount /mnt/orion_turbo

step "8/10 Agent sandboxes (rootless Podman as $AGENT_USER, one throwaway container per task)"
# Review H12: sudoers rule for exactly orion-sandbox, the shared agent work folder, ORION_AGENT_WORK_DIR (os/lab/sandbox-setup.sh)
ORION_USER="$ORION_USER" AGENT_USER="$AGENT_USER" bash "$OS/lab/sandbox-setup.sh" || echo "!! Agent sandbox step failed; commands run without it"

step "8b Install recipes (root runner; only the session user may start them, see 50-orion-recipes.rules)"
install -d -m 755 -o root -g root /opt/orion/recipes
install -m 755 -o root -g root "$OS/lab/recipes/run" /opt/orion/recipes/run
install -m 644 -o root -g root "$OS/lab/recipes/"*.sh /opt/orion/recipes/
install -m 644 "$OS/lab/orion-recipe@.service" /etc/systemd/system/orion-recipe@.service
install -d -m 755 /etc/polkit-1/rules.d
install -m 644 "$OS/lab/50-orion-recipes.rules" /etc/polkit-1/rules.d/50-orion-recipes.rules
install -d -m 755 /var/lib/orion /var/lib/orion/recipes
[ "$PROFILE" = image ] || systemctl daemon-reload

step "9/10 Firewall: inbound only boardroom/phone->9121 and SSH over Tailscale; killswitch installed OFF"
# [H10] a clean slate only the first time; afterwards only Orion's own rules are refreshed and yours are kept
if [ -f /etc/orion/ufw-managed ]; then bash "$OS/common/orion-ufw-forget"; else ufw --force reset >/dev/null; touch /etc/orion/ufw-managed; fi
ufw default deny incoming
ufw default allow outgoing
ufw allow in from "$BOARDROOM_IP" to any port 9121 proto tcp comment "boardroom verdicts"
# Phones on your home Wi-Fi arrive through the Windows port-forward ("Open Phone Access.cmd"), which VirtualBox
# delivers from OrionNet's gateway. Every request on 9121 must still be signed by a paired key.
ufw allow in from 10.77.0.1 to any port 9121 proto tcp comment "Orion Mobile over Wi-Fi (signed requests only)"
# [H11] On your own machine the local network may reach 9121 only on networks you marked Home
# (orion-ctl home-network on), or for 10 minutes while pairing (orion-ctl pair-phone). Closed on unknown networks.
# os/privacy/orion-phone-port + its NetworkManager hook keep that rule in step (installed by os/image/os-rescue.sh).
ufw allow in on tailscale0 to any port 22 proto tcp comment "SSH over Tailscale only"
ufw allow in on tailscale0 to any port 9121 proto tcp comment "Orion Mobile (signed requests only)"
bash "$OS/image/os-hardware.sh" firewall || echo "!! printer discovery rule not added"   # Displays & printing: mDNS replies from printers/scanners, local network only (os/hardware/README.md)
if [ "$PROFILE" = image ]; then sed -i 's/^ENABLED=.*/ENABLED=yes/' /etc/ufw/ufw.conf   # turns on at first boot
else ufw --force enable; fi
[ "$PROFILE" = image ] || /opt/orion/privacy/orion-phone-port sync >/dev/null 2>&1 || true   # [H11] Home networks
install -m 644 "$OS/common/killswitch.nft.tmpl" /etc/orion/killswitch.nft.tmpl
install -m 755 "$OS/common/orion-killswitch" /usr/local/bin/orion-killswitch
if ! command -v tailscale >/dev/null; then
  bash "$OS/common/install-tailscale.sh" || echo "!! Tailscale install failed; run it later: sudo bash $OS/common/install-tailscale.sh"   # signed apt repo, no curl|sh
fi

step "10/10 Base hardening (automatic security updates, SSH keys only)"
ORION_PROFILE="$PROFILE" bash "$OS/common/harden.sh"

echo "$(date -u +%FT%TZ) provisioned from bundle $(cat "$SRC/BUNDLE_VERSION" 2>/dev/null || echo dev)" > /etc/orion/provisioned
[ "$GREETD_OURS" != 1 ] || sh "$OS/lab/orion-owned" record /etc/greetd/config.toml || true   # [H10] what Orion wrote, so a later change by you is noticed
# [B3] signed updates (orion-update) need the release key; a tree that carries one brings it the first time
[ -s /etc/orion/release.pub ] || [ ! -s "$OS/image/release.pub" ] || install -m 644 "$OS/image/release.pub" /etc/orion/release.pub
step "Done. Reboot to enter OrionOS (cloud-init reboots automatically on first boot)."
