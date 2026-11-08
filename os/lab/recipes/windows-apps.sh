# Run Windows apps on OrionOS: Bottles (Windows apps in their own sandboxed "bottles", using Wine) and Steam (your
# Steam library; Proton runs most Windows games). Both from Flathub, installed system-wide like Get apps does.
# Lutris (more game stores) is optional: Get apps > Games. Start it with: orion-ctl install windows-apps
# (or Get apps > Run Windows apps). Safe to re-run.
set -eu
export DEBIAN_FRONTEND=noninteractive
command -v flatpak >/dev/null || { echo "Flatpak isn't installed (os/image/os-getapps.sh sets it up)."; exit 1; }
flatpak remotes --system --columns=name | grep -qx flathub || { echo "Flathub isn't set up (os/image/os-getapps.sh)."; exit 1; }
# Ids checked against github.com/flathub/<id> on 2026-10-17 (os/getapps/catalog.json lists them too).
flatpak install --system -y --noninteractive flathub com.usebottles.bottles com.valvesoftware.Steam
# Game controllers (Steam Controller, DualShock, Switch Pro...) need Steam's udev rules on the system itself.
if apt-cache show steam-devices >/dev/null 2>&1; then apt-get install -y -q steam-devices || true; fi
echo "Bottles and Steam are installed. Windows apps: open Bottles, make a bottle, add the program."
echo "Games: open Steam > Settings > Compatibility > turn on Steam Play for all titles."
