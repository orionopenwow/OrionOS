#!/usr/bin/env bash
# OrionOS "Everyone can use it" (docs/ORIONOS_COMPLETE_PLAN.md Part 1 and step 6): accessibility, typing other
# languages and emoji, a guest account and extra people, laptop power. What each piece does: os/everyone/README.md.
#
#   bash os/image/os-everyone.sh        (as root: inside the image chroot, on the lab VM, or on an installed OrionOS)
#
# Called from os/lab/provision.sh step 6. Safe to re-run.
#   ORION_PROFILE=vm|baremetal|image    vm: no Chinese/Japanese/Korean engines (big), hibernation never offered
#   ORION_USER=orion                    the session user whose terminal gets the accessibility include file
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OS="$SRC/os"
E="$OS/everyone"
PROFILE="${ORION_PROFILE:-baremetal}"
ORION_USER="${ORION_USER:-orion}"
GUEST="${ORION_GUEST_USER:-guest}"
export DEBIAN_FRONTEND=noninteractive

say() { printf '\033[1;36m[os-everyone] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
install_pkgs() {   # install what's missing; a package the mirror doesn't have is reported, not fatal
  local missing="" p
  for p in "$@"; do dpkg -s "$p" >/dev/null 2>&1 || missing="$missing $p"; done
  [ -n "$missing" ] || return 0
  # shellcheck disable=SC2086
  apt-get install -y -q --no-install-recommends $missing || {
    for p in $missing; do apt-get install -y -q --no-install-recommends "$p" || echo "!! $p isn't available; skipped"; done; }
}

say "Packages: screen reader, on-screen keyboard, input methods, power modes"
apt-get update -q || true
install_pkgs orca at-spi2-core speech-dispatcher espeak-ng wvkbd gnome-themes-extra \
  fcitx5 fcitx5-frontend-gtk3 fcitx5-frontend-gtk4 fcitx5-frontend-qt5 fcitx5-m17n wofi wtype wl-clipboard libnotify-bin
if [ "$PROFILE" != vm ]; then
  install_pkgs fcitx5-chinese-addons fcitx5-mozc fcitx5-hangul power-profiles-daemon
fi

say "The tools -> /opt/orion/everyone, /usr/local/sbin/orion-guest-home"
install -d -m 755 /opt/orion/everyone /opt/orion/greeter /etc/orion
install -m 644 "$E/orion_everyone.py" /opt/orion/everyone/orion_everyone.py
install -m 755 -o root -g root "$E/orion-people-admin" "$E/orion-guest-start" "$E/orion-guest-login" /opt/orion/everyone/
install -m 755 -o root -g root "$E/orion-guest-home" /usr/local/sbin/orion-guest-home
install -m 755 -o root -g root "$E/orion-login-next" /opt/orion/greeter/orion-login-next
for c in orion-access orion-typing orion-power orion-people; do   # provision step 6 links these too; here for re-runs
  [ -f "$OS/lab/bin/$c" ] && install -m 755 "$OS/lab/bin/$c" "/opt/orion/bin/$c" && ln -sf "/opt/orion/bin/$c" "/usr/local/bin/$c"
done

say "High-contrast theme -> /usr/share/themes/Orion-Glass-HighContrast"
rm -rf /usr/share/themes/Orion-Glass-HighContrast
cp -r "$OS/theme/gtk/Orion-Glass-HighContrast" /usr/share/themes/
chmod -R a+rX /usr/share/themes/Orion-Glass-HighContrast

say "Guest account: polkit, PAM (no password, RAM-disk home wiped at log-out), the login screen's guest hand-off"
install -d -m 755 /usr/share/polkit-1/actions /etc/polkit-1/rules.d /etc/tmpfiles.d
install -m 644 "$E/polkit/ai.orion.everyone.policy" /usr/share/polkit-1/actions/ai.orion.everyone.policy
sed "s/subject.user != \"guest\"/subject.user != \"$GUEST\"/" "$E/polkit/51-orion-everyone.rules" \
  > /etc/polkit-1/rules.d/51-orion-everyone.rules
chmod 644 /etc/polkit-1/rules.d/51-orion-everyone.rules
printf 'd /run/orion-login 0770 root _greetd -\n' > /etc/tmpfiles.d/orion-login.conf
PAM=/etc/pam.d/greetd
if [ -f "$PAM" ] && ! grep -q orion-guest "$PAM"; then
  { head -n1 "$PAM"
    printf '# orion-guest (os/everyone): the guest signs in without a password and gets a RAM-disk home, wiped at log-out\n'
    printf 'auth     sufficient pam_succeed_if.so quiet user = %s\n' "$GUEST"
    printf 'session  required   pam_exec.so quiet /usr/local/sbin/orion-guest-home\n'
    tail -n +2 "$PAM"; } > "$PAM.orion" && mv "$PAM.orion" "$PAM"
elif [ ! -f "$PAM" ]; then
  echo "!! $PAM is missing: the guest account can't sign in until greetd is installed (re-run this step)"
fi
# Put orion-login-next in front of greetd's login command (only [default_session]; the auto-login stays as it is).
# The repo's templates are left alone, so a failed step here never changes how the computer logs in.
for f in /etc/greetd/config.toml /opt/orion/greeter/greetd-auto.toml /opt/orion/greeter/greetd-password.toml; do
  [ -f "$f" ] || continue
  grep -q orion-login-next "$f" && continue
  awk '/^\[/{sec=$0} sec=="[default_session]" && /^command = "/ {sub(/^command = "/, "command = \"/opt/orion/greeter/orion-login-next ")} {print}' \
    "$f" > "$f.orion" && mv "$f.orion" "$f" && chmod 644 "$f"
done

say "Home folders for new people and for the guest (/etc/orion/person-skel, /etc/orion/guest-skel)"
make_skel() {
  local d="$1"
  rm -rf "$d"
  install -d -m 755 "$d" "$d/.config" "$d/.config/sway" "$d/.config/foot" "$d/.config/mako" "$d/.config/orion" \
    "$d/.config/gtk-3.0" "$d/.config/gtk-4.0" "$d/.config/wofi" "$d/.config/orion/sway.d" "$d/projects" "$d/Captures"
  # [H10] a short config of their own that includes Orion's (/etc/orion/sway/orion.conf, os/lab/orion-sway-config)
  printf '# Your Sway settings. OrionOS'"'"'s own come from the line below; add yours after it.\ninclude /etc/orion/sway/orion.conf\n' > "$d/.config/sway/config"
  install -m 644 "$OS/lab/foot.ini" "$d/.config/foot/foot.ini"
  python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import orion_everyone as E
print(E.render_foot_access(E.clean_access({})), end="")' "$E" > "$d/.config/foot/orion-access.ini"
  install -m 644 "$OS/lab/mako-config" "$d/.config/mako/config"
  [ -f "$OS/theme/wallpaper.png" ] && install -m 644 "$OS/theme/wallpaper.png" "$d/.config/sway/wallpaper.png"
  for v in gtk-3.0 gtk-4.0; do install -m 644 "$OS/theme/$v/gtk.css" "$OS/theme/$v/settings.ini" "$d/.config/$v/"; done
  install -m 644 "$OS/theme/wofi/style.css" "$OS/theme/wofi/config" "$d/.config/wofi/"
  install -m 600 /dev/null "$d/.config/orion/daemon.env"
}
make_skel /etc/orion/person-skel
# (first-run for new people: orion-firstrun.service in sway-session.target, the same as the main account)
make_skel /etc/orion/guest-skel
G=/etc/orion/guest-skel/.config/orion
printf '# Guest session: Orion runs without agents, messages or approvals (apps/daemon/everyone.py)\nORION_GUEST_MODE=1\nORION_CAPTURE_ENABLED=0\n' > "$G/daemon.env"
date -u +%FT%TZ > "$G/firstrun-done"
echo off > "$G/clipboard-history"
chmod 700 /etc/orion/guest-skel

say "The session user's terminal reads the accessibility file"
H="$(getent passwd "$ORION_USER" | cut -d: -f6 || true)"
if [ -n "$H" ] && [ -d "$H/.config" ]; then
  install -d -o "$ORION_USER" -g "$ORION_USER" "$H/.config/foot"
  [ -f "$H/.config/foot/orion-access.ini" ] || install -m 644 -o "$ORION_USER" -g "$ORION_USER" \
    /etc/orion/person-skel/.config/foot/orion-access.ini "$H/.config/foot/orion-access.ini"
fi

say "Laptop power: sleep, then hibernate after 2 hours (only where hibernation works; Settings > Power)"
install -d -m 755 /etc/systemd/sleep.conf.d
printf '[Sleep]\n# OrionOS (os/everyone): with "Sleep, then hibernate" on, a sleeping laptop saves to disk after 2 hours\nHibernateDelaySec=2h\n' \
  > /etc/systemd/sleep.conf.d/60-orion.conf
if [ "$PROFILE" = image ]; then
  # The USB stick never hibernates (its memory would land outside the vault); undo the choice if a copy had it.
  sed -i 's/=suspend-then-hibernate$/=suspend/' /etc/systemd/logind.conf.d/60-orion-settings.conf 2>/dev/null || true
fi

say "Done. Super+Alt+S screen reader, Super+Alt+K keyboard, Super+Alt+C contrast, Super+. emoji; Settings has the rest."
