#!/usr/bin/env bash
# Orion's own parts as one signed bundle, for installed OrionOS systems (orion-update; review B3):
#   orion-bundle.tar.gz           the source tree OrionOS sets itself up from (/opt/orion-src: apps/daemon, the HUD
#                                 package in os/dist, the OS scripts), with BUNDLE_VERSION inside
#   orion-bundle.tar.gz.minisig   signed with the release key; the signed "trusted comment" says
#                                 "orion-bundle <version>", so the version can't be swapped
#   orion-bundle.version          the version, for "is there something newer?" (not trusted on its own)
#
#   os/image/make-bundle.sh <out dir> <version>     (ORION_RELEASE_KEY = minisign secret key file, optional;
#                                                    ORION_RELEASE_KEY_PASSWORD its password)
# "My builds" channel: put the three files in /var/lib/orion/my-builds (signed with your own key) and choose
# Settings > Updates > channel "My builds".
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
OUT="${1:?usage: make-bundle.sh <out dir> <version>}"; VERSION="${2:?usage: make-bundle.sh <out dir> <version>}"
case "$VERSION" in *[!A-Za-z0-9._+-]*|"") echo "bad version: $VERSION"; exit 2 ;; esac
mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd)"
ls "$REPO"/os/dist/*.deb >/dev/null 2>&1 || { echo "!! os/dist/*.deb (the HUD) is missing"; exit 1; }
STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
# the same files the USB image puts in /opt/orion-src (os/image/build.sh step 3)
tar -C "$REPO" --exclude=node_modules --exclude=.git --exclude='os/image/out' --exclude='apps/desktop/src-tauri/target' \
  --exclude='apps/mobile/build' --exclude='apps/mobile/app/build' --exclude='apps/mobile/.gradle' --exclude=_to_delete \
  --exclude='*.apk' --exclude='docs/app' --exclude=__pycache__ --exclude='*.pyc' --exclude='apps/daemon/.env' \
  --exclude='.orion-signing' --exclude='*.key' -cf - . | tar -C "$STAGE" -xf -
chmod -R u+rwX,go+rX,go-w "$STAGE"
echo "$VERSION" > "$STAGE/BUNDLE_VERSION"
for need in os/lab/provision.sh apps/daemon/main.py; do [ -f "$STAGE/$need" ] || { echo "!! $need is missing"; exit 1; }; done
tar -C "$STAGE" --owner=0 --group=0 --numeric-owner --sort=name -czf "$OUT/orion-bundle.tar.gz" .
echo "$VERSION" > "$OUT/orion-bundle.version"
(cd "$OUT" && sha256sum orion-bundle.tar.gz > SHA256SUMS-bundle)
if [ -n "${ORION_RELEASE_KEY:-}" ]; then
  if [ -n "${ORION_RELEASE_KEY_PASSWORD:-}" ]; then
    printf '%s\n' "$ORION_RELEASE_KEY_PASSWORD" | minisign -S -s "$ORION_RELEASE_KEY" -m "$OUT/orion-bundle.tar.gz" -t "orion-bundle $VERSION"
  else
    minisign -S -W -s "$ORION_RELEASE_KEY" -m "$OUT/orion-bundle.tar.gz" -t "orion-bundle $VERSION"
  fi
else
  echo "(no ORION_RELEASE_KEY: the bundle is unsigned, and installed systems will refuse it)"
fi
ls -la "$OUT"/orion-bundle*
