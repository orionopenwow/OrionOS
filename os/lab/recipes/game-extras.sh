# Game extras for OrionOS (orion-games extras, Settings > Games): GameMode (lets a game ask for full speed while it
# runs; from Ubuntu) and MangoHud (an on-screen frame-rate overlay; the Flathub Vulkan layer that Flatpak Steam,
# Heroic and Lutris use, on the same runtime branch as Steam). Both optional. POSIX sh (the recipe runner uses sh).
# Safe to re-run.
set -eu
export DEBIAN_FRONTEND=noninteractive
if apt-cache show gamemode >/dev/null 2>&1; then
  apt-get install -y -q --no-install-recommends gamemode || echo "GameMode couldn't be installed now."
else
  apt-get update -q >/dev/null 2>&1 || true
  apt-get install -y -q --no-install-recommends gamemode || echo "GameMode couldn't be installed now."
fi
if command -v flatpak >/dev/null && flatpak remotes --system --columns=name | grep -qx flathub; then
  # MangoHud's Flatpak layer must match the runtime branch of the launcher (checked: github.com/flathub/
  # org.freedesktop.Platform.VulkanLayer.MangoHud has branch/25.08 and branch/26.08 on 2026-11-07).
  BRANCH=""
  for app in com.valvesoftware.Steam com.heroicgameslauncher.hgl net.lutris.Lutris; do
    rt="$(flatpak info --system --show-runtime "$app" 2>/dev/null || true)"   # org.freedesktop.Platform/x86_64/25.08
    [ -n "$rt" ] && { BRANCH="${rt##*/}"; break; }
  done
  case "$BRANCH" in
    [0-9][0-9].[0-9][0-9]) flatpak install --system -y --noninteractive flathub "org.freedesktop.Platform.VulkanLayer.MangoHud//$BRANCH" \
                             || echo "MangoHud couldn't be installed now." ;;
    *) echo "Install Steam, Heroic or Lutris first; MangoHud is added to match it." ;;
  esac
fi
echo "Done. In Steam, a game's Properties > Launch options:  gamemoderun %command%"
echo "Add MANGOHUD=1 in front to see the frame-rate overlay:  MANGOHUD=1 gamemoderun %command%"
echo "Heroic and Lutris: each game's settings have GameMode and MangoHud switches."
