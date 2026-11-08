#!/usr/bin/env bash
# Art Update, stage 2 (boot chain) and stage 5 (command line): the parts that need installing.
#
#   art-boot.sh provision        run by os/lab/provision.sh (as root, inside the system being set up):
#                                fonts, the login screen's shader, the prompt, `orion` fetch, boot sound, Orion
#                                cursor, swaylock/Qt themes, and the splash -> login screen hand-off
#   art-boot.sh esp ROOT ESP     run by os/image/build.sh while it assembles the stick: the GRUB theme (fonts are
#                                made with grub-mkfont inside ROOT) copied into the ESP image
#
# Everything it installs comes from the repo; nothing is downloaded except Ubuntu packages (qt5ct, qt6ct).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
OS="$REPO/os"
THEME="$OS/theme"
say() { printf '  [art-boot] %s\n' "$*"; }

fonts_dir() { echo "$THEME/fonts"; }
# first font file in os/theme/fonts whose name matches $1 (prefers Regular / variable)
find_font() {
  local d; d="$(fonts_dir)"; [ -d "$d" ] || return 1
  find "$d" -type f \( -iname "*$1*.ttf" -o -iname "*$1*.otf" \) 2>/dev/null \
    | awk '{ r = 5; if ($0 ~ /\[wght\]|[Vv]ariable/) r = 0; else if ($0 ~ /Regular|-400/) r = 1; print r "\t" $0 }' \
    | sort | head -n1 | cut -f2-
}

provision() {
  local ORION_USER="${ORION_USER:-orion}"
  local H="/home/$ORION_USER"
  local S=/usr/share/orion
  install -d -m 755 "$S" "$S/shell" "$S/qt" "$S/brand" /usr/share/sounds/orion

  # ---- fonts (art-core bundles them in os/theme/fonts; OFL)
  if [ -d "$(fonts_dir)" ]; then
    install -d -m 755 /usr/share/fonts/truetype/orion
    find "$(fonts_dir)" -type f \( -iname '*.ttf' -o -iname '*.otf' \) -exec install -m 644 {} /usr/share/fonts/truetype/orion/ \;
    find "$(fonts_dir)" -maxdepth 2 -type f \( -iname 'OFL*' -o -iname 'LICENSE*' \) -exec install -m 644 {} /usr/share/fonts/truetype/orion/ \; 2>/dev/null || true
    fc-cache -f /usr/share/fonts/truetype/orion >/dev/null 2>&1 || true
  else
    say "no os/theme/fonts yet: terminal and splash use the fallback fonts"
  fi

  # ---- the login screen on the shared black hole shader
  if [ -f "$THEME/blackhole/blackhole.frag" ] && [ -d /opt/orion/greeter/web ]; then
    local layout="$OS/image/files/usr/share/plymouth/themes/orion/frames/layout.json"
    python3 "$OS/greeter/make-shader-js.py" "$THEME/blackhole/blackhole.frag" "$REPO/apps/desktop/public/black-hole-core.png" \
      /opt/orion/greeter/web/shader.js ${layout:+"$layout"} && chmod 644 /opt/orion/greeter/web/shader.js
  else
    say "no os/theme/blackhole/blackhole.frag: the login screen keeps its 2D sky"
  fi

  # ---- command line: prompt, long-command star, `orion` fetch (the command itself is in os/lab/bin)
  install -m 644 "$THEME/shell/orion.bash" "$THEME/shell/starship.toml" "$THEME/shell/fastfetch.jsonc" \
    "$THEME/shell/orion-logo.ansi" "$S/shell/"
  grep -q 'orion/shell/orion.bash' /etc/bash.bashrc 2>/dev/null || \
    printf '\n# OrionOS prompt and long-command notices (Art Update)\n[ -r /usr/share/orion/shell/orion.bash ] && . /usr/share/orion/shell/orion.bash\n' >> /etc/bash.bashrc
  # starship and fastfetch aren't in Ubuntu 24.04's archive: the prompt and `orion` fall back to their own versions.
  apt-get install -y -q --no-install-recommends qt5ct qt6ct >/dev/null 2>&1 || say "qt5ct/qt6ct not installed (Qt apps keep their default look)"

  # ---- the mark (until art-core's brand mark lands at the same path), boot sound, lock screen, Qt, cursor
  [ -f "$S/brand/mark.png" ] || install -m 644 "$OS/image/grub-theme/mark.png" "$S/brand/mark.png"
  install -m 644 "$THEME/sounds/boot.ogg" "$THEME/sounds/chime.ogg" /usr/share/sounds/orion/
  install -m 644 "$THEME/swaylock/config" "$S/swaylock.conf"
  [ -f "$THEME/lock.png" ] && install -m 644 "$THEME/lock.png" "$S/lock.png"
  install -m 644 "$THEME/qt/Orion.conf" "$S/qt/Orion.conf"
  rm -rf /usr/share/icons/Orion
  cp -a "$THEME/cursor/Orion" /usr/share/icons/Orion
  local alias target
  while read -r alias target; do
    case "$alias" in ''|'#'*) continue ;; esac
    ln -sfn "$target" "/usr/share/icons/Orion/cursors/$alias"
  done < "$THEME/cursor/Orion/aliases.txt"
  rm -f /usr/share/icons/Orion/aliases.txt
  chmod -R a+rX /usr/share/icons/Orion
  # the session reads this (orion-session): Qt through qt5ct with Orion's colours, the Orion cursor everywhere
  install -d -m 755 /etc/orion
  cat > /etc/orion/session.env <<'ENV'
# OrionOS session environment (os/image/art-boot.sh). Sourced by orion-session before Sway starts.
export QT_QPA_PLATFORMTHEME=qt5ct
export XCURSOR_THEME=Orion
export XCURSOR_SIZE=24
ENV
  if id "$ORION_USER" >/dev/null 2>&1; then
    local v
    for v in qt5ct qt6ct; do
      install -d -o "$ORION_USER" -g "$ORION_USER" "$H/.config/$v"
      [ -f "$H/.config/$v/$v.conf" ] || install -m 644 -o "$ORION_USER" -g "$ORION_USER" "$THEME/qt/$v.conf" "$H/.config/$v/$v.conf"
    done
    install -d -o "$ORION_USER" -g "$ORION_USER" "$H/.icons" "$H/.icons/default"
    printf '[Icon Theme]\nInherits=Orion\n' > "$H/.icons/default/index.theme"
    chown "$ORION_USER:$ORION_USER" "$H/.icons/default/index.theme"
  fi
  # GTK apps read the cursor from gsettings / settings.ini (art-desktop owns the GTK theme; only the cursor here)
  local ini
  for ini in "$H/.config/gtk-3.0/settings.ini" "$H/.config/gtk-4.0/settings.ini"; do
    [ -f "$ini" ] || continue
    if grep -q '^gtk-cursor-theme-name=' "$ini"; then sed -i 's/^gtk-cursor-theme-name=.*/gtk-cursor-theme-name=Orion/' "$ini"
    else printf 'gtk-cursor-theme-name=Orion\n' >> "$ini"; fi
  done

  # ---- splash -> login screen / HUD: keep the splash's last frame on screen until greetd's screen takes over,
  # instead of flashing the text console in between (the same thing GDM does).
  if command -v plymouth >/dev/null 2>&1; then
    install -d /etc/systemd/system/greetd.service.d
    cat > /etc/systemd/system/greetd.service.d/orion-splash.conf <<'UNIT'
# OrionOS: hand the boot splash's last frame straight to the login screen / HUD (os/image/art-boot.sh)
[Unit]
After=plymouth-start.service
Conflicts=plymouth-quit.service
After=plymouth-quit.service
OnFailure=plymouth-quit.service

[Service]
ExecStartPre=-/usr/bin/plymouth quit --retain-splash
UNIT
    # Ubuntu's greetd.service waits for plymouth-quit-wait (= until the splash has quit), but with the hand-off above
    # greetd is the one that quits the splash, and it conflicts with plymouth-quit: a deadlock (boot hangs on
    # "plymouth-quit-wait ... no limit"). Use a copy of the unit without that wait, like GDM's unit.
    if [ -f /usr/lib/systemd/system/greetd.service ]; then
      sed -e 's/[[:space:]]*plymouth-quit-wait\.service//' \
          -e '1i # OrionOS copy of greetd.service without "After=plymouth-quit-wait.service" (os/image/art-boot.sh)' \
          /usr/lib/systemd/system/greetd.service > /etc/systemd/system/greetd.service
    fi
  fi
  say "provisioned (fonts, greeter shader, prompt, fetch, sound, cursor, lock, Qt, splash hand-off)"
}

esp() {
  local ROOT="$1" ESP="$2" T
  T="$(mktemp -d "${TMPDIR:-/tmp}/orion-grub-theme.XXXXXX")"
  cp "$OS/image/grub-theme/"{background,mark,select_w,select_c,select_e}.png "$OS/image/grub-theme/theme.txt" "$T/"
  # art-core's brand mark replaces the provisional one when it exists
  for m in "$THEME/brand/mark-128.png" "$THEME/brand/mark.png"; do [ -f "$m" ] && { cp "$m" "$T/mark.png"; break; }; done
  # Fonts: grub-mkfont from the image (grub-common is installed there). Unbounded for the title, Figtree for the menu;
  # DejaVu stands in for any that aren't bundled yet. The names are fixed so theme.txt always finds them.
  local un fg fb=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
  un="$(find_font unbounded || true)"; fg="$(find_font figtree || true)"
  mkdir -p "$ROOT/tmp/orion-fonts"
  [ -n "$un" ] && cp "$un" "$ROOT/tmp/orion-fonts/unbounded.ttf"
  [ -n "$fg" ] && cp "$fg" "$ROOT/tmp/orion-fonts/figtree.ttf"
  mk() {   # name size source-in-root out   (Latin, punctuation and arrows only: small files, sane metrics)
    chroot "$ROOT" grub-mkfont -r 0x20-0x7E,0xA0-0x17F,0x2010-0x2027,0x2190-0x2193 -n "$1" -s "$2" \
      -o "/tmp/orion-fonts/$4" "$3" >/dev/null 2>&1
  }
  local usrc=/tmp/orion-fonts/unbounded.ttf fsrc=/tmp/orion-fonts/figtree.ttf
  [ -n "$un" ] || usrc="$fb"
  [ -n "$fg" ] || fsrc="$fb"
  if mk "Orion Unbounded" 32 "$usrc" orion-unbounded-32.pf2 && mk "Orion Figtree" 16 "$fsrc" orion-figtree-16.pf2 \
     && mk "Orion Figtree" 20 "$fsrc" orion-figtree-20.pf2; then
    cp "$ROOT"/tmp/orion-fonts/*.pf2 "$T/"
  else
    say "grub-mkfont failed: the GRUB menu stays plain text"
    rm -rf "$T" "$ROOT/tmp/orion-fonts"; return 0
  fi
  rm -rf "$ROOT/tmp/orion-fonts"
  export MTOOLS_SKIP_CHECK=1
  mmd -i "$ESP" ::/EFI/orion/theme 2>/dev/null || true
  mcopy -o -i "$ESP" "$T"/* ::/EFI/orion/theme/
  rm -rf "$T"
  say "GRUB theme in the ESP (/EFI/orion/theme)"
}

case "${1:-}" in
  provision) provision ;;
  esp) shift; esp "$@" ;;
  *) echo "usage: art-boot.sh provision | esp ROOT ESP_IMAGE"; exit 2 ;;
esac
