# Discord's official Linux package, straight from discord.com.
set -eu
export DEBIAN_FRONTEND=noninteractive
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fL --retry 3 -o "$TMP/discord.deb" "https://discord.com/api/download?platform=linux&format=deb"
[ "$(dpkg-deb -f "$TMP/discord.deb" Package)" = "discord" ] || { echo "download isn't the Discord package"; exit 1; }
apt-get install -y -q "$TMP/discord.deb"
