#!/usr/bin/env bash
# OrionOS complete plan, step 1 (docs/ORIONOS_COMPLETE_PLAN.md, Part 1): a web browser and everyday file apps,
# so every common file opens with something on day one.
#   - Firefox from Mozilla's own APT repo (the .deb, pinned above Ubuntu's snap stub; os/lab/recipes/firefox.sh),
#     with Orion defaults in /etc/firefox/policies/policies.json (os/apps/firefox/policies.json)
#   - Document Viewer (Evince): PDF, PostScript, DjVu, XPS, comics
#   - Image Viewer (Eye of GNOME) + WebP/AVIF/HEIF support
#   - mpv for video and music (os/apps/mpv/mpv.conf: hardware decoding, Orion colours)
#   - Text Editor (Mousepad), Calculator (GNOME Calculator), Archive Manager (File Roller + zip/7z tools)
#   - LibreOffice Writer, Calc and Impress (ORION_WITH_OFFICE=0 leaves it out; then "orion-ctl install office")
#   - default apps for every common file type (/etc/xdg/mimeapps.list and the orion user's mimeapps.list)
#   - Super+B opens the browser (os/apps/sway-apps.conf -> /etc/sway/config.d/)
# Why these apps: all GTK 3 (they pick up Orion Glass; Calculator is GTK 4) and native Wayland, so they look and
# behave right under Sway; Evince/Eye of GNOME/Mousepad are mouse-friendly (zathura and imv are keyboard-only).
#
# Called by os/lab/provision.sh for every Orion desktop profile (image, vm, baremetal). Never for the boardroom,
# which must stay browser-free (scripts/vm/verify-boardroom.sh). Run as root. Safe to re-run.
#   ORION_WITH_OFFICE=1|0        LibreOffice (default 1)
#   ORION_FIREFOX_SOURCE=auto|mozilla|ppa
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(cd "$HERE/.." && pwd)"
APPS="$OS/apps"
ORION_USER="${ORION_USER:-orion}"
PROFILE="${ORION_PROFILE:-$(cat /etc/orion/profile 2>/dev/null || echo baremetal)}"
WITH_OFFICE="${ORION_WITH_OFFICE:-1}"
H="/home/$ORION_USER"
export DEBIAN_FRONTEND=noninteractive
say() { printf '\033[1;34m[orion-apps] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
case "$PROFILE" in
  *boardroom*) say "boardroom profile: no browser or apps here (it must stay browser-free)"; exit 0 ;;
esac
APT_INSTALL=(apt-get install -y -q --no-install-recommends)

say "Everyday apps: documents, images, video/music, text, calculator, archives"
"${APT_INSTALL[@]}" xdg-utils desktop-file-utils shared-mime-info \
  evince eog webp-pixbuf-loader libavif-gdk-pixbuf heif-gdk-pixbuf \
  libheif-plugin-libde265 mpv mousepad gnome-calculator file-roller thunar-archive-plugin unzip zip 7zip xz-utils zstd
if [ "$PROFILE" != vm ]; then
  # Video decoding on the graphics chip (AMD and older Intel: Mesa; newer Intel: intel-media). The VM has no GPU.
  "${APT_INSTALL[@]}" mesa-va-drivers intel-media-va-driver || echo "!! hardware video decoding drivers not installed"
fi

if [ "$WITH_OFFICE" = 1 ]; then
  say "LibreOffice Writer, Calc, Impress (GTK 3 look)"
  "${APT_INSTALL[@]}" libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-gtk3 \
    libreoffice-style-colibre fonts-crosextra-carlito fonts-crosextra-caladea \
    || echo "!! LibreOffice could not be installed (later: orion-ctl install office)"
fi

say "Firefox (Mozilla's .deb, never the snap)"
if sh "$OS/lab/recipes/firefox.sh"; then :; else
  echo "!! Firefox could not be installed now (its download sites were not reachable)."
  echo "!! The rest of OrionOS works; install it later with: orion-ctl install firefox"
fi
say "Firefox defaults: dark, no telemetry/studies/sponsored tiles, DuckDuckGo + Firefox Home, tracking protection everywhere, HTTPS-only"
# /etc/firefox/policies wins over the package's own distribution/policies.json, and survives Firefox updates.
install -d -m 755 /etc/firefox /etc/firefox/policies
install -m 644 "$APPS/firefox/policies.json" /etc/firefox/policies/policies.json

say "mpv defaults (hardware decoding, Orion colours)"
install -d -m 755 /etc/mpv
install -m 644 "$APPS/mpv/mpv.conf" /etc/mpv/mpv.conf

say "Launcher names and the browser key"
# The launcher shows each app's Name; a few say nothing about what the app does. Same file id, so the file
# associations still work; /usr/local/share/applications comes before /usr/share/applications.
install -d -m 755 /usr/local/share/applications
rename_app() {   # rename_app ID.desktop "Old name" "New name"
  local src="/usr/share/applications/$1"
  [ -f "$src" ] || return 0
  sed -e "s/^Name=$2\$/Name=$3/" "$src" > "/usr/local/share/applications/$1"
}
rename_app org.xfce.mousepad.desktop "Mousepad" "Text Editor"
rename_app org.gnome.FileRoller.desktop "File Roller" "Archive Manager"
install -d -m 755 /etc/sway/config.d
install -m 644 "$APPS/sway-apps.conf" /etc/sway/config.d/60-orion-apps.conf

say "Default apps for every common file type"
# Only apps that are really installed are written, so nothing points at a missing program.
D=/usr/share/applications
have() { [ -f "/usr/local/share/applications/$1" ] || [ -f "$D/$1" ]; }
defaults() {   # defaults APP.desktop mime/type...  (the browser is always written: it may be installed later)
  local app="$1"; shift
  if have "$app" || [ "$app" = "$BROWSER" ]; then for m in "$@"; do printf '%s=%s;\n' "$m" "$app"; done
  else echo "!! $app is not installed; its file types are left without a default" >&2; fi
}
BROWSER=firefox.desktop
PDF=org.gnome.Evince.desktop
IMAGES=org.gnome.eog.desktop
MEDIA=mpv.desktop
TEXT=org.xfce.mousepad.desktop
ARCHIVES=org.gnome.FileRoller.desktop
FILES=thunar.desktop
have "$FILES" || FILES=orion-files.desktop
CSV_APP="$TEXT"; have libreoffice-calc.desktop && CSV_APP=libreoffice-calc.desktop
MIMEAPPS="$(mktemp)"
{
  echo "# OrionOS default apps (written by os/image/os-apps.sh; your own ~/.config/mimeapps.list wins)."
  echo "# Try with Craft (complete plan, step 5): Orion Studio will add PdfCraft / PhotoCraft here under"
  echo "# [Added Associations] (so \"Open With\" offers them), and make one the default once it is stable."
  echo "[Default Applications]"
  defaults "$BROWSER" text/html application/xhtml+xml application/x-xpinstall x-scheme-handler/http \
    x-scheme-handler/https x-scheme-handler/about x-scheme-handler/unknown
  defaults "$PDF" application/pdf application/x-pdf application/x-bzpdf application/x-gzpdf application/postscript \
    application/x-bzpostscript application/x-gzpostscript image/x-eps application/x-dvi image/vnd.djvu \
    image/vnd.djvu+multipage application/oxps application/vnd.ms-xpsdocument application/x-cbr application/x-cbz \
    application/x-cb7 application/vnd.comicbook+zip application/vnd.comicbook-rar
  defaults "$IMAGES" image/png image/jpeg image/pjpeg image/gif image/webp image/bmp image/x-bmp image/tiff \
    image/svg+xml image/svg+xml-compressed image/x-icon image/vnd.microsoft.icon image/avif image/heif image/heic \
    image/x-portable-pixmap image/x-portable-graymap image/x-portable-bitmap image/x-portable-anymap image/x-tga \
    image/x-xbitmap image/x-xpixmap image/jxl
  defaults "$MEDIA" video/mp4 video/x-m4v video/x-matroska video/webm video/quicktime video/x-msvideo video/avi \
    video/mpeg video/x-mpeg2 video/mp2t video/ogg video/x-ogm+ogg video/x-flv video/3gpp video/3gpp2 \
    video/x-ms-wmv video/x-ms-asf video/vnd.avi video/x-theora+ogg \
    audio/mpeg audio/mp3 audio/x-mp3 audio/mp4 audio/x-m4a audio/aac audio/x-aac audio/flac audio/x-flac \
    audio/ogg audio/x-vorbis+ogg audio/opus audio/x-opus+ogg audio/wav audio/x-wav audio/vnd.wave audio/webm \
    audio/x-matroska audio/x-ms-wma audio/x-aiff audio/aiff audio/midi audio/x-ape audio/x-mpegurl \
    audio/mpegurl audio/x-scpls application/x-matroska application/ogg
  defaults "$TEXT" text/plain text/x-log text/markdown text/x-markdown text/x-readme application/json \
    application/x-yaml application/yaml text/x-yaml application/xml text/xml application/toml text/x-toml \
    text/x-python text/x-python3 application/x-shellscript text/x-shellscript text/x-csrc text/x-chdr \
    text/x-c++src text/x-c++hdr text/x-java text/x-rust text/rust text/javascript application/javascript \
    application/x-typescript text/x-typescript text/css text/x-scss text/x-go text/x-lua text/x-php \
    application/x-php text/x-sql application/sql text/x-makefile text/x-cmake text/x-diff text/x-patch \
    text/x-csharp text/x-kotlin text/x-ruby application/x-ruby text/x-perl application/x-perl \
    application/x-desktop text/x-dockerfile text/vnd.trolltech.linguist text/x-gettext-translation \
    application/x-wine-extension-ini text/x-ini
  defaults "$CSV_APP" text/csv text/tab-separated-values
  defaults "$ARCHIVES" application/zip application/x-zip-compressed application/x-tar application/x-compressed-tar \
    application/x-bzip-compressed-tar application/x-bzip2-compressed-tar application/x-xz-compressed-tar \
    application/x-zstd-compressed-tar application/x-lzma-compressed-tar application/gzip application/x-gzip \
    application/x-bzip application/x-bzip2 application/x-xz application/x-lzma application/zstd \
    application/x-7z-compressed application/vnd.rar application/x-rar application/x-rar-compressed \
    application/x-cpio application/x-java-archive application/java-archive application/vnd.debian.binary-package \
    application/x-deb application/x-rpm application/x-iso9660-image application/x-arj application/x-lha
  defaults "$FILES" inode/directory
  if [ "$WITH_OFFICE" = 1 ]; then
    defaults libreoffice-writer.desktop application/msword application/vnd.ms-word \
      application/vnd.openxmlformats-officedocument.wordprocessingml.document \
      application/vnd.openxmlformats-officedocument.wordprocessingml.template \
      application/vnd.oasis.opendocument.text application/vnd.oasis.opendocument.text-template application/rtf text/rtf \
      application/vnd.ms-works application/x-abiword application/vnd.wordperfect
    defaults libreoffice-calc.desktop application/vnd.ms-excel \
      application/vnd.openxmlformats-officedocument.spreadsheetml.sheet \
      application/vnd.openxmlformats-officedocument.spreadsheetml.template \
      application/vnd.ms-excel.sheet.macroEnabled.12 application/vnd.oasis.opendocument.spreadsheet \
      application/vnd.oasis.opendocument.spreadsheet-template
    defaults libreoffice-impress.desktop application/vnd.ms-powerpoint \
      application/vnd.openxmlformats-officedocument.presentationml.presentation \
      application/vnd.openxmlformats-officedocument.presentationml.slideshow \
      application/vnd.oasis.opendocument.presentation application/vnd.oasis.opendocument.presentation-template
  fi
  echo
  echo "[Added Associations]"
  # Second choices in "Open With": PDFs and pictures in the browser, web pages as text.
  printf '%s\n' "application/pdf=$PDF;$BROWSER;" "image/svg+xml=$IMAGES;$BROWSER;" "text/html=$BROWSER;$TEXT;"
} > "$MIMEAPPS"
install -d -m 755 /etc/xdg
install -m 644 "$MIMEAPPS" /etc/xdg/mimeapps.list
if id "$ORION_USER" >/dev/null 2>&1; then
  install -d -o "$ORION_USER" -g "$ORION_USER" "$H/.config"
  # Write the user's list only when it's new or still exactly what OrionOS wrote last time: choices made with
  # "Open With" (which edit this file) are never overwritten.
  U="$H/.config/mimeapps.list" LAST=/var/lib/orion/mimeapps.last
  if [ ! -f "$U" ] || { [ -f "$LAST" ] && cmp -s "$U" "$LAST"; }; then
    install -m 644 -o "$ORION_USER" -g "$ORION_USER" "$MIMEAPPS" "$U"
    install -d -m 755 /var/lib/orion
    install -m 644 "$MIMEAPPS" "$LAST"
  fi
fi
rm -f "$MIMEAPPS"
update-desktop-database -q "$D" /usr/local/share/applications 2>/dev/null || true
update-mime-database /usr/share/mime 2>/dev/null || true
# System-wide "which browser" for programs that ask Debian's alternatives instead of xdg.
[ -x /usr/bin/firefox ] && update-alternatives --set x-www-browser /usr/bin/firefox >/dev/null 2>&1 || true
say "Done"
