# LibreOffice Writer, Calc and Impress (Word, Excel and PowerPoint files), for systems built without it
# (ORION_WITH_OFFICE=0). Start it with: orion-ctl install office. Safe to re-run.
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends libreoffice-writer libreoffice-calc libreoffice-impress \
  libreoffice-gtk3 libreoffice-style-colibre fonts-crosextra-carlito fonts-crosextra-caladea
# Make office files open in LibreOffice (only where the system list has no default for them yet).
L=/etc/xdg/mimeapps.list
[ -f "$L" ] || printf '[Default Applications]\n' > "$L"
add() { grep -q "^$1=" "$L" || sed -i "/^\[Default Applications\]/a $1=$2;" "$L"; }
for m in application/msword application/vnd.openxmlformats-officedocument.wordprocessingml.document \
  application/vnd.oasis.opendocument.text application/rtf; do add "$m" libreoffice-writer.desktop; done
for m in application/vnd.ms-excel application/vnd.openxmlformats-officedocument.spreadsheetml.sheet \
  application/vnd.oasis.opendocument.spreadsheet; do add "$m" libreoffice-calc.desktop; done
for m in application/vnd.ms-powerpoint application/vnd.openxmlformats-officedocument.presentationml.presentation \
  application/vnd.oasis.opendocument.presentation; do add "$m" libreoffice-impress.desktop; done
update-desktop-database -q /usr/share/applications 2>/dev/null || true
