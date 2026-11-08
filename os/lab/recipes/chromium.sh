# Chromium, an optional second browser (not installed by default; Firefox is OrionOS's browser).
# Ubuntu 24.04's "chromium-browser" is only a snap stub, so this uses the Flatpak from Flathub, the same place
# "Get apps" will install from. Start it with: orion-ctl install chromium. Safe to re-run.
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -q --no-install-recommends flatpak xdg-desktop-portal-gtk ca-certificates
flatpak remote-add --system --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --system -y --noninteractive flathub org.chromium.Chromium
echo "Chromium installed. It appears in the launcher (Super+D) as Chromium; Firefox stays the default browser."
