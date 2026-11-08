#!/usr/bin/env bash
# Supply-Chain Scan for the OrionOS image (docs/ORION_TOOLKIT_PLAN.md tool 19), called by build.sh:
#
#   bash os/image/scan-image.sh <rootfs dir> <output prefix>
#
# Writes <prefix>-sbom.json (syft: every package in the image) and <prefix>-vulns.json (grype: known holes),
# which "Orion, is the USB image safe to ship?" reads. Only runs when syft and grype are installed; it never
# stops the build. Install them from their GitHub releases (Apache-2.0) if you want image scans.
set -uo pipefail
ROOT="${1:?rootfs dir}"
PREFIX="${2:?output prefix}"
say() { printf '\033[1;36m[scan-image] %s\033[0m\n' "$*"; }
if ! command -v syft >/dev/null || ! command -v grype >/dev/null; then
  say "syft/grype not installed: the image isn't scanned (Orion will say it can't tell yet)."
  exit 0
fi
say "Listing the image's packages (syft)"
if ! syft "dir:$ROOT" -q -o "syft-json=$PREFIX-sbom.json"; then
  echo "!! syft failed; no image scan this time"; exit 0
fi
say "Checking them for known holes (grype)"
if grype "sbom:$PREFIX-sbom.json" -q -o json > "$PREFIX-vulns.json.tmp"; then
  mv -f "$PREFIX-vulns.json.tmp" "$PREFIX-vulns.json"
  say "Report: $PREFIX-vulns.json"
else
  rm -f "$PREFIX-vulns.json.tmp"; echo "!! grype failed; no image scan this time"
fi
exit 0
