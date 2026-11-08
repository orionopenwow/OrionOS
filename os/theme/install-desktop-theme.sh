#!/usr/bin/env bash
# OrionOS Art Update, stage 4 (OS side): the desktop look for normal Linux apps.
#   - "Orion Glass" GTK 3/4 theme (+ the opaque "Orion Glass Solid" twin for plain Sway) as the default
#   - "Orion" icon theme (Orion folders; everything else from Papirus-Dark) as the default
#   - Thunar set up per the plan: toolbar back/forward/up, breadcrumb path pill, search, Places/Devices/Network
#     sidebar, status bar, large icons; device names "Vault" and "Orion USB"
#   - VS Code "Event Horizon" theme (installed when VS Code / VSCodium is present, otherwise shipped)
#   - wallpapers / lock screen stills (os/theme/wallpapers, rendered from the black hole shader)
#   - SwayFX (os/image/swayfx.sh) and its glass settings (/etc/sway/orion-fx.conf)
#
# Called by os/lab/provision.sh (all profiles). Run as root. Safe to re-run.
#   ORION_WITH_SWAYFX=1|0   build SwayFX (default: yes, except in the VirtualBox lab where Sway uses the
#                           software renderer and SwayFX can't run)
set -euo pipefail
THEME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(cd "$THEME/.." && pwd)"
ORION_USER="${ORION_USER:-orion}"
PROFILE="${ORION_PROFILE:-$(cat /etc/orion/profile 2>/dev/null || echo baremetal)}"
H="/home/$ORION_USER"
export DEBIAN_FRONTEND=noninteractive
say() { printf '\033[1;35m[orion-theme] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

say "Packages: Thunar's devices/trash/network (gvfs, udisks2), icon tools"
apt-get install -y -q --no-install-recommends gvfs gvfs-backends udisks2 libglib2.0-bin gtk-update-icon-cache \
  papirus-icon-theme gnome-themes-extra adwaita-icon-theme librsvg2-common fontconfig >/dev/null \
  || echo "!! some theme packages could not be installed (the theme still works)"

say "GTK themes -> /usr/share/themes (Orion-Glass, Orion-Glass-Solid)"
for t in Orion-Glass Orion-Glass-Solid; do
  rm -rf "/usr/share/themes/$t"
  cp -r "$THEME/gtk/$t" "/usr/share/themes/$t"
done
chmod -R a+rX /usr/share/themes/Orion-Glass /usr/share/themes/Orion-Glass-Solid

say "Icon theme -> /usr/share/icons/Orion"
rm -rf /usr/share/icons/Orion
cp -r "$THEME/icons/Orion" /usr/share/icons/Orion
chmod -R a+rX /usr/share/icons/Orion
gtk-update-icon-cache -q -f /usr/share/icons/Orion 2>/dev/null || true

# Fonts (Figtree / Unbounded / Martian Mono, OFL) are bundled by art-core in os/theme/fonts.
if [ -d "$THEME/fonts" ] && ls "$THEME/fonts" | grep -qiE '\.(ttf|otf)$'; then
  say "Fonts -> /usr/share/fonts/truetype/orion"
  install -d /usr/share/fonts/truetype/orion
  find "$THEME/fonts" -type f \( -iname '*.ttf' -o -iname '*.otf' -o -iname '*LICENSE*' -o -iname 'OFL*' \) \
    -exec install -m 644 {} /usr/share/fonts/truetype/orion/ \;
  fc-cache -f >/dev/null 2>&1 || true
fi

say "System-wide defaults: Orion Glass, Orion icons, Figtree, belt-dot window buttons"
SETTINGS='[Settings]
gtk-application-prefer-dark-theme=1
gtk-theme-name=Orion-Glass
gtk-icon-theme-name=Orion
gtk-font-name=Figtree 10
gtk-cursor-theme-size=24
gtk-decoration-layout=close,minimize,maximize:minimize,maximize,close
gtk-enable-animations=1'
for v in gtk-3.0 gtk-4.0; do install -d "/etc/$v"; printf '%s\n' "$SETTINGS" > "/etc/$v/settings.ini"; done
# GSettings defaults (what GTK reads under Wayland), whatever backend is in use.
cat > /usr/share/glib-2.0/schemas/90_orion-desktop.gschema.override <<'EOF'
[org.gnome.desktop.interface]
gtk-theme='Orion-Glass'
icon-theme='Orion'
font-name='Figtree 10'
document-font-name='Figtree 10'
monospace-font-name='Martian Mono 10'
color-scheme='prefer-dark'
cursor-size=24

[org.gnome.desktop.wm.preferences]
button-layout='close,minimize,maximize:minimize,maximize,close'
EOF
glib-compile-schemas /usr/share/glib-2.0/schemas 2>/dev/null || true
# Qt apps follow the GTK look where they can.
grep -q '^QT_QPA_PLATFORMTHEME=' /etc/environment 2>/dev/null || echo 'QT_QPA_PLATFORMTHEME=gtk3' >> /etc/environment

say "Device names in Thunar: Vault (the encrypted persistence) and Orion USB"
cat > /etc/udev/rules.d/61-orion-devices.rules <<'EOF'
# OrionOS: friendly names and icons for the stick's partitions in Thunar's Devices list (udisks2 hints).
ENV{ID_FS_LABEL}=="OrionVault", ENV{UDISKS_NAME}="Vault", ENV{UDISKS_ICON_NAME}="orion-vault"
ENV{ID_FS_LABEL}=="writable", ENV{DM_NAME}=="orionvault*", ENV{UDISKS_NAME}="Vault", ENV{UDISKS_ICON_NAME}="orion-vault"
ENV{ID_FS_LABEL}=="ORIONSYS", ENV{UDISKS_NAME}="Orion USB", ENV{UDISKS_ICON_NAME}="orion-usb"
# The boot partition is never something to open or eject by hand.
ENV{ID_FS_LABEL}=="ORIONEFI", ENV{UDISKS_IGNORE}="1"
EOF

say "Sway glass settings (/etc/sway/orion-fx.conf) and lock-screen still"
install -d /etc/sway
install -m 644 "$OS/lab/sway-fx.conf" /etc/sway/orion-fx.conf
install -d /usr/share/backgrounds/orion
if ls "$THEME"/wallpapers/*.png >/dev/null 2>&1 || ls "$THEME"/wallpapers/*.jpg >/dev/null 2>&1; then
  find "$THEME/wallpapers" -maxdepth 1 -type f \( -name '*.png' -o -name '*.jpg' \) -exec install -m 644 {} /usr/share/backgrounds/orion/ \;
fi
# Stable names the session uses (sway-config: desktop; /etc/swaylock/config: lock screen).
for pair in "desktop:orion-blackhole-4k" "lock:orion-interstellar-4k"; do
  name="${pair%%:*}"; src="${pair#*:}"
  for ext in png jpg; do
    if [ -f "/usr/share/backgrounds/orion/$src.$ext" ]; then ln -sf "$src.$ext" "/usr/share/backgrounds/orion/$name.$ext"; fi
  done
done
# swaylock reads this only when the user has no ~/.config/swaylock/config; orion-lock's colours still apply.
if ls /usr/share/backgrounds/orion/lock.* >/dev/null 2>&1; then
  install -d /etc/swaylock
  printf '# OrionOS lock screen still (Art Update): rendered from the black hole shader.\nimage=%s\nscaling=fill\n' \
    "$(ls /usr/share/backgrounds/orion/lock.* | head -n1)" > /etc/swaylock/config
fi

say "VS Code \"Event Horizon\" theme"
install -d /opt/orion/vscode
VSIX="$(ls "$THEME"/vscode-event-horizon/event-horizon-*.vsix 2>/dev/null | sort -V | tail -n1 || true)"
if [ -n "$VSIX" ]; then
  install -m 644 "$VSIX" /opt/orion/vscode/
  for pair in code:Code codium:VSCodium; do
    ed="${pair%%:*}"; cfg="$H/.config/${pair#*:}/User"
    command -v "$ed" >/dev/null && id "$ORION_USER" >/dev/null 2>&1 || continue
    if runuser -u "$ORION_USER" -- env HOME="$H" "$ed" --install-extension "/opt/orion/vscode/$(basename "$VSIX")" --force >/dev/null 2>&1; then
      say "  installed for $ed"
    else
      echo "!! $ed --install-extension failed (install it later from /opt/orion/vscode)"
    fi
    install -d -o "$ORION_USER" -g "$ORION_USER" "$(dirname "$cfg")" "$cfg"
    if [ ! -f "$cfg/settings.json" ]; then
      printf '{\n  "workbench.colorTheme": "Event Horizon"\n}\n' > "$cfg/settings.json"
      chown "$ORION_USER:$ORION_USER" "$cfg/settings.json"
    fi
  done
fi

if id "$ORION_USER" >/dev/null 2>&1; then
  say "Thunar for $ORION_USER: breadcrumbs, status bar, large Orion folders"
  X="$H/.config/xfce4/xfconf/xfce-perchannel-xml"
  install -d -o "$ORION_USER" -g "$ORION_USER" "$H/.config/xfce4" "$H/.config/xfce4/xfconf" "$X"
  [ -f "$X/thunar.xml" ] || install -m 644 -o "$ORION_USER" -g "$ORION_USER" "$THEME/thunar/thunar.xml" "$X/thunar.xml"
fi

if [ "${ORION_WITH_SWAYFX:-$([ "$PROFILE" = vm ] && echo 0 || echo 1)}" = 1 ]; then
  say "SwayFX (compiled; several minutes)"
  ORION_MIRROR="${ORION_MIRROR:-http://archive.ubuntu.com/ubuntu}" bash "$OS/image/swayfx.sh" \
    || echo "!! SwayFX could not be built: OrionOS uses plain Sway with opaque glass windows (Orion Glass Solid)"
fi
say "Done"
