#!/usr/bin/env bash
# OrionOS: build SwayFX (blur, rounded corners, shadows, dimmed background windows) for Ubuntu 24.04.
#
#   bash os/image/swayfx.sh            (as root, inside the image chroot or on an installed OrionOS)
#
# Why it's built like this (checked 2026-10-16):
#   SwayFX 0.6   needs wlroots 0.20, which needs libdrm >= 2.4.129, xkbcommon >= 1.8, pixman >= 0.46,
#                libdisplay-info >= 0.2 and wayland-protocols >= 1.47. Too much of the graphics stack to replace.
#   SwayFX 0.5.3 (Sway 1.11 base) needs wlroots 0.19 + scenefx 0.4, which need wayland >= 1.23.1, pixman >= 0.43
#                and libinput >= 1.26. 24.04 ships wayland 1.22, pixman 0.42 and libinput 1.25; everything else
#                (libdrm 2.4.125, wayland-protocols 1.45 from noble-updates) is new enough.  <- this script
#   SwayFX 0.4   is the last one that builds on 24.04's own wlroots 0.17 (Sway 1.9 base). Fallback: SWAYFX=0.4.
# So 0.5.3 is the newest release that builds cleanly. The four newer libraries (sources from the Ubuntu archive,
# the versions later Ubuntu releases ship) are built into a private prefix, /opt/swayfx, and only SwayFX links to
# them (RPATH). Nothing in /usr is replaced: every other app keeps Ubuntu's libraries, and plain Sway
# (/usr/bin/sway, from provision.sh) stays installed as the fallback. orion-session picks SwayFX when it can.
#
# Result: /opt/swayfx/bin/sway (+ swaymsg, swaybar, swaynag), linked as /usr/local/bin/swayfx.
# Build files go to /var/tmp/swayfx-build and are removed afterwards, as are the -dev packages it installed.
set -euo pipefail

SWAYFX="${SWAYFX:-0.5.3}"
PREFIX="${SWAYFX_PREFIX:-/opt/swayfx}"
MIRROR="${ORION_MIRROR:-http://archive.ubuntu.com/ubuntu}"
WORK="${SWAYFX_WORK:-/var/tmp/swayfx-build}"
export DEBIAN_FRONTEND=noninteractive

# name|version|source|sha256  (git sources: sha = the release tag's commit)
case "$SWAYFX" in
  0.5.3) PARTS="wayland|1.24.0|$MIRROR/pool/main/w/wayland/wayland_1.24.0.orig.tar.gz|c6d416e207d4d80d2577c316ce1554062b37f6e8776f81f25fb4f5a3515590f7
pixman|0.46.4|$MIRROR/pool/main/p/pixman/pixman_0.46.4.orig.tar.gz|d09c44ebc3bd5bee7021c79f922fe8fb2fb57f7320f55e97ff9914d2346a591c
libinput|1.28.1|$MIRROR/pool/main/libi/libinput/libinput_1.28.1.orig.tar.gz|a13f8c9a7d93df3c85c66afd135f0296701d8d32f911991b7aa4273fdd6a42a3
wlroots|0.19.2|$MIRROR/pool/universe/w/wlroots/wlroots_0.19.2.orig.tar.bz2|804bd3eab10f03556628c4e977351b0b1b17bc34084342dfaff5086c54ce1fa0
scenefx|0.4.1|https://github.com/wlrfx/scenefx.git|0f069970b410195aa5908d98c6fb41f3f0cd6e76
swayfx|0.5.3|https://github.com/wlrfx/swayfx.git|660c1197b6626326ce35a0241a6a00e80dddcbd6" ;;
  0.4)   PARTS="scenefx|0.1|https://github.com/wlrfx/scenefx.git|
swayfx|0.4|https://github.com/wlrfx/swayfx.git|" ;;
  *) echo "swayfx.sh: no recipe for SwayFX $SWAYFX (known: 0.5.3, 0.4)"; exit 2 ;;
esac

say() { printf '\033[1;35m[swayfx] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
if [ -x "$PREFIX/bin/sway" ] && [ "$(cat "$PREFIX/VERSION" 2>/dev/null)" = "$SWAYFX" ] && [ "${SWAYFX_REBUILD:-0}" != 1 ]; then
  say "SwayFX $SWAYFX already in $PREFIX"; exit 0
fi

say "Build dependencies"
BUILD_DEPS="build-essential meson ninja-build pkg-config git ca-certificates curl bzip2 xz-utils
  libwayland-dev wayland-protocols libdrm-dev libgbm-dev libegl-dev libgles-dev libxkbcommon-dev libudev-dev
  libseat-dev libdisplay-info-dev libliftoff-dev hwdata libxcb1-dev libxcb-composite0-dev libxcb-icccm4-dev
  libxcb-render0-dev libxcb-res0-dev libxcb-xfixes0-dev libxcb-ewmh-dev libxcb-render-util0-dev xwayland
  libmtdev-dev libevdev-dev libjson-c-dev libpango1.0-dev libcairo2-dev libgdk-pixbuf-2.0-dev libpcre2-dev
  scdoc libexpat1-dev libxml2-dev libffi-dev liblcms2-dev"
[ "$SWAYFX" = 0.4 ] && BUILD_DEPS="$BUILD_DEPS libwlroots-dev"   # 0.4 uses Ubuntu's own wlroots 0.17
apt-get update -q
# Remember what was already there, so only what this script added is removed afterwards.
dpkg-query -W -f='${Package}\n' | sort > /tmp/swayfx-pkgs-before
apt-get install -y -q --no-install-recommends $BUILD_DEPS

rm -rf "$WORK"; mkdir -p "$WORK"; cd "$WORK"
LIBDIR="$PREFIX/lib"
export PKG_CONFIG_PATH="$LIBDIR/pkgconfig:$PREFIX/share/pkgconfig"
export PATH="$PREFIX/bin:$PATH"            # the newer wayland-scanner
export LDFLAGS="-Wl,-rpath,$LIBDIR -Wl,--enable-new-dtags"
MESON_COMMON=(--prefix="$PREFIX" --libdir=lib --buildtype=release -Dwrap_mode=nodownload)

fetch() {  # name version source sha -> ./name
  local n="$1" v="$2" src="$3" sha="$4"
  if [[ "$src" == *.git ]]; then
    git -c advice.detachedHead=false clone -q --depth 1 --branch "$v" "$src" "$n"
    if [ -n "$sha" ] && [ "$(git -C "$n" rev-parse HEAD)" != "$sha" ]; then echo "!! $n $v: tag moved"; exit 1; fi
  else
    curl -fsSL -o "$n.tar" "$src"
    echo "$sha  $n.tar" | sha256sum -c --quiet - || { echo "!! $n $v: checksum mismatch"; exit 1; }
    mkdir "$n"; tar -xf "$n.tar" -C "$n" --strip-components=1; rm "$n.tar"
  fi
}

build() {  # name -> meson options for it
  case "$1" in
    wayland)  echo "-Dtests=false -Ddocumentation=false -Ddtd_validation=false -Dicon_directory=/usr/share/icons" ;;
    pixman)   echo "-Dtests=disabled -Ddemos=disabled -Dgtk=disabled -Dlibpng=disabled" ;;
    libinput) echo "-Dlibwacom=false -Ddebug-gui=false -Dtests=false -Ddocumentation=false -Dinstall-tests=false
                    -Dudev-dir=$LIBDIR/udev" ;;
    wlroots)  echo "-Dexamples=false -Drenderers=gles2 -Dbackends=drm,libinput -Dallocators=gbm -Dxwayland=enabled
                    -Dsession=enabled -Dcolor-management=enabled -Dlibliftoff=disabled -Dicon_directory=/usr/share/icons" ;;
    scenefx)  echo "-Dexamples=false" ;;
    swayfx)   echo "-Dman-pages=enabled -Dtray=disabled -Ddefault-wallpaper=false -Dfish-completions=false
                    -Dzsh-completions=false -Dbash-completions=false" ;;
  esac
}

while IFS='|' read -r name ver src sha; do
  [ -n "$name" ] || continue
  say "$name $ver"
  fetch "$name" "$ver" "$src" "$sha"
  # SwayFX 0.4 builds against Ubuntu's own wlroots 0.17 (no private libraries).
  # shellcheck disable=SC2046
  meson setup "$name/_b" "$name" "${MESON_COMMON[@]}" $(build "$name") >"$WORK/$name.log" 2>&1 \
    || { tail -40 "$WORK/$name.log"; exit 1; }
  ninja -C "$name/_b" >>"$WORK/$name.log" 2>&1 || { tail -40 "$WORK/$name.log"; exit 1; }
  ninja -C "$name/_b" install >>"$WORK/$name.log" 2>&1
done <<< "$PARTS"

# The private libraries never go into ld.so.conf: only SwayFX (RPATH) loads them.
rm -rf "$PREFIX/include" "$LIBDIR/pkgconfig" "$PREFIX/share/pkgconfig" "$PREFIX/share/aclocal" "$LIBDIR"/*.a
strip --strip-unneeded "$PREFIX"/bin/* "$LIBDIR"/*.so.* 2>/dev/null || true
echo "$SWAYFX" > "$PREFIX/VERSION"
ln -sf "$PREFIX/bin/sway" /usr/local/bin/swayfx
install -d /usr/share/wayland-sessions
cat > /usr/share/wayland-sessions/swayfx.desktop <<EOF
[Desktop Entry]
Name=SwayFX (OrionOS glass)
Comment=Sway with blur, rounded corners and shadows
Exec=$PREFIX/bin/sway
Type=Application
EOF

say "Checking that every library SwayFX needs is present"
missing="$(for f in "$PREFIX"/bin/* "$LIBDIR"/*.so*; do ldd "$f" 2>/dev/null; done | awk '/not found/{print $1}' | sort -u)"
[ -z "$missing" ] || { echo "!! missing libraries: $missing"; exit 1; }
# Keep the runtime packages SwayFX links to, then drop the -dev packages and tools this script added.
for so in $(for f in "$PREFIX"/bin/* "$LIBDIR"/*.so*; do ldd "$f" 2>/dev/null; done | awk '$3 ~ /^\// && $3 !~ /^\/opt\//{print $3}' | sort -u); do
  dpkg -S "$(readlink -f "$so")" 2>/dev/null || dpkg -S "$so" 2>/dev/null || true
done | cut -d: -f1 | sort -u | xargs -r apt-mark manual >/dev/null
apt-mark manual hwdata xwayland >/dev/null 2>&1 || true
dpkg-query -W -f='${Package}\n' | sort > /tmp/swayfx-pkgs-after
added="$(comm -13 /tmp/swayfx-pkgs-before /tmp/swayfx-pkgs-after | grep -E -- '-dev$|^(meson|ninja-build|scdoc|wayland-protocols|cmake)$' || true)"
[ -z "$added" ] || apt-get purge -y -q $added >/dev/null
apt-get autoremove -y -q >/dev/null
rm -f /tmp/swayfx-pkgs-before /tmp/swayfx-pkgs-after
missing="$(for f in "$PREFIX"/bin/* "$LIBDIR"/*.so*; do ldd "$f" 2>/dev/null; done | awk '/not found/{print $1}' | sort -u)"
[ -z "$missing" ] || { echo "!! cleanup removed libraries SwayFX needs: $missing"; exit 1; }
cd /; rm -rf "$WORK"
"$PREFIX/bin/sway" --version
say "SwayFX $SWAYFX installed in $PREFIX ($(du -sh "$PREFIX" | cut -f1))"
