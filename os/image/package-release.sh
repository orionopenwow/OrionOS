#!/usr/bin/env bash
# Package a built image for a GitHub release (files must be under 2 GB each):
#   orionos-<v>-<flavour>.img.tar.xz.part-aa, -ab …  + orionos-<flavour>-img.parts   (Orion USB Maker joins them)
#   orionos-<flavour>-slot.tar.part-aa …             + orionos-<flavour>-slot.parts  (orion-slot update joins them)
#   .minisig for the joined files, SHA256SUMS-<flavour>, orionos-<flavour>.version
# Usage: os/image/package-release.sh <out dir> <flavour>     (ORION_RELEASE_KEY = minisign secret key, optional)
set -euo pipefail
OUT="$1"; FL="$2"; cd "$OUT"
IMG="$(ls -t orionos-*-"$FL".img | head -n1)"
PART=1900M
tar -cJf "$IMG.tar.xz" --sparse "$IMG"
split -b "$PART" -a 2 "$IMG.tar.xz" "$IMG.tar.xz.part-"
ls "$IMG.tar.xz.part-"* | xargs -n1 basename > "orionos-$FL-img.parts"
split -b "$PART" -a 2 "orionos-$FL-slot.tar" "orionos-$FL-slot.tar.part-"
ls "orionos-$FL-slot.tar.part-"* | xargs -n1 basename > "orionos-$FL-slot.parts"
sha256sum "$IMG.tar.xz" "orionos-$FL-slot.tar" > "SHA256SUMS-$FL"
if [ -n "${ORION_RELEASE_KEY:-}" ]; then
  for f in "$IMG.tar.xz" "orionos-$FL-slot.tar"; do
    if [ -n "${ORION_RELEASE_KEY_PASSWORD:-}" ]; then printf '%s\n' "$ORION_RELEASE_KEY_PASSWORD" | minisign -S -s "$ORION_RELEASE_KEY" -m "$f"
    else minisign -S -W -s "$ORION_RELEASE_KEY" -m "$f"; fi
  done
fi
ls -la
