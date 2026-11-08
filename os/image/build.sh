#!/usr/bin/env bash
# Build the OrionOS 1.0 USB image.
#
#   sudo os/image/build.sh [--flavour standard|nvidia] [--out DIR] [--version X.Y.Z] [--reuse-rootfs]
#
# Output (in --out, default os/image/out):
#   orionos-<version>-<flavour>.img        write it to a USB stick (Orion USB Maker, balenaEtcher, Rufus "DD")
#   orionos-<flavour>-slot.tar             a whole-system update for sticks already in use (orion-slot update)
#   orionos-<flavour>.version, SHA256SUMS, and .minisig signatures when ORION_RELEASE_KEY is set
#
# The stick: GPT with
#   1  ORIONEFI  (FAT32, 256 MiB)  Ubuntu's signed shim + GRUB, so Secure Boot can stay on; grubenv for A/B
#   2  ORIONSYS  (ext4)            slots/a and slots/b, each a whole system: vmlinuz, initrd, filesystem.squashfs
#   3  OrionVault (made on first start in the rest of the stick; LUKS2 holding casper's "writable" persistence)
#
# Needs: Ubuntu 24.04 host (or CI ubuntu-24.04), root, ~25 GB free. No loop devices: the partitions are built as
# files (mkfs.ext4 -d, mtools) and stitched together, so it also runs in containers.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
FLAVOUR=standard
OUT="$HERE/out"
VERSION="${ORION_VERSION:-1.0.0}"
WORK="${ORION_IMAGE_WORK:-/var/tmp/orion-image}"
REUSE=0
MIRROR="${ORION_MIRROR:-http://archive.ubuntu.com/ubuntu}"
# NVIDIA flavour: the driver branch. 580 is current on Ubuntu 24.04 (570 is now only a pointer to it) and the last
# branch that still runs GTX 900/1000-series cards. The kernel part is Ubuntu's ready-built, signed module package
# (linux-modules-nvidia-<branch>-generic), so nothing is compiled and Secure Boot keeps working.
NVIDIA_PKG="${ORION_NVIDIA_PKG:-580}"
while [ $# -gt 0 ]; do
  case "$1" in
    --flavour) FLAVOUR="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --reuse-rootfs) REUSE=1; shift ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
done
ROOT="$WORK/rootfs"
step() { printf '\n\033[1;36m[orion-image] %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
mkdir -p "$WORK" "$OUT"

mounts=()
cleanup() { for ((i=${#mounts[@]}-1; i>=0; i--)); do umount -lf "${mounts[$i]}" 2>/dev/null || true; done; }
trap cleanup EXIT
bind() { mkdir -p "$ROOT/$1"; mount --bind "/$1" "$ROOT/$1"; mounts+=("$ROOT/$1"); }
in_root() { chroot "$ROOT" /usr/bin/env -i HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 \
  ${ORION_EXTRA_CA:+PIP_CERT=/etc/ssl/certs/ca-certificates.crt REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt} "$@"; }

if [ "${ORION_SQUASH_ONLY:-0}" != 1 ]; then   # (developers: skip to step 5 with an already-built rootfs)
# ------------------------------------------------------------------ 1. base system
if [ "$REUSE" = 1 ] && [ -x "$ROOT/usr/bin/apt-get" ]; then
  step "1/7 Reusing the root filesystem in $ROOT"
else
  step "1/7 Ubuntu 24.04 base (debootstrap)"
  rm -rf "$ROOT"
  debootstrap --arch=amd64 --components=main,restricted,universe,multiverse noble "$ROOT" "$MIRROR"
fi
cat > "$ROOT/etc/apt/sources.list.d/ubuntu.sources" <<EOF
Types: deb
URIs: $MIRROR
Suites: noble noble-updates noble-backports
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: http://security.ubuntu.com/ubuntu
Suites: noble-security
Components: main restricted universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
rm -f "$ROOT/etc/apt/sources.list"
for d in dev dev/pts proc sys run; do bind "$d"; done
rm -f "$ROOT/etc/resolv.conf"; cp -L /etc/resolv.conf "$ROOT/etc/resolv.conf"
# Build machines behind a TLS-inspecting proxy can pass its CA (removed again before squashing).
if [ -n "${ORION_EXTRA_CA:-}" ]; then
  cp "$ORION_EXTRA_CA" "$ROOT/usr/local/share/ca-certificates/orion-build-extra.crt"
  chroot "$ROOT" update-ca-certificates >/dev/null 2>&1 || true
fi
printf '#!/bin/sh\nexit 101\n' > "$ROOT/usr/sbin/policy-rc.d"; chmod 755 "$ROOT/usr/sbin/policy-rc.d"   # no services start while building

# ------------------------------------------------------------------ 2. kernel, live boot, Secure Boot, encryption, hardware
step "2/7 Kernel, live boot, Secure Boot chain, vault encryption, drivers"
in_root apt-get update -q
# grub2-common: grub-install and update-grub, which orion-install-disk runs on the new drive (not pulled in by
# --no-install-recommends on its own everywhere); os-prober finds Windows for the start menu.
PKGS="linux-generic casper initramfs-tools shim-signed grub-efi-amd64-signed grub-efi-amd64-bin grub-common grub2-common efibootmgr
  cryptsetup cryptsetup-initramfs plymouth plymouth-label locales tzdata keyboard-configuration console-setup
  sudo rsync parted dosfstools e2fsprogs os-prober minisign curl ca-certificates network-manager wpasupplicant
  intel-microcode amd64-microcode zram-tools"
[ "$FLAVOUR" = nvidia ] && PKGS="$PKGS nvidia-driver-$NVIDIA_PKG linux-modules-nvidia-$NVIDIA_PKG-generic"
in_root apt-get install -y -q --no-install-recommends $PKGS
if [ "$FLAVOUR" = nvidia ]; then
  # Make sure the driver really is in the image: the ready-built kernel module for the image's kernel, the user-space
  # driver, and kernel mode setting on (Sway needs it). nouveau stays out of the way.
  KV_NV="$(ls "$ROOT/lib/modules" | sort -V | tail -n1)"
  in_root modinfo -k "$KV_NV" nvidia >/dev/null 2>&1 || { echo "!! NVIDIA flavour: no nvidia kernel module for $KV_NV (linux-modules-nvidia-$NVIDIA_PKG-generic)"; exit 1; }
  in_root dpkg -s "nvidia-driver-$NVIDIA_PKG" >/dev/null 2>&1 || { echo "!! NVIDIA flavour: nvidia-driver-$NVIDIA_PKG did not install"; exit 1; }
  printf 'options nvidia-drm modeset=1 fbdev=1\n' > "$ROOT/etc/modprobe.d/orion-nvidia.conf"
  printf 'blacklist nouveau\noptions nouveau modeset=0\n' > "$ROOT/etc/modprobe.d/orion-nvidia-blacklist-nouveau.conf"
  echo "   NVIDIA driver $NVIDIA_PKG with the ready-built module for $KV_NV"
fi
in_root locale-gen en_US.UTF-8 >/dev/null
echo 'LANG=en_US.UTF-8' > "$ROOT/etc/default/locale"
# zram swap + logs in RAM: fewer writes, longer-lived sticks
printf 'ALGO=zstd\nPERCENT=50\n' > "$ROOT/etc/default/zramswap"
mkdir -p "$ROOT/etc/systemd/journald.conf.d"
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=64M\n' > "$ROOT/etc/systemd/journald.conf.d/90-orion-usb.conf"

# ------------------------------------------------------------------ 3. OrionOS itself (the same setup as the VM and laptop)
step "3/7 OrionOS (provision.sh, profile=image)"
rm -rf "$ROOT/opt/orion-src"; mkdir -p "$ROOT/opt/orion-src"
tar -C "$REPO" --exclude=node_modules --exclude=.git --exclude='os/image/out' --exclude='apps/desktop/src-tauri/target' \
  --exclude='apps/mobile/build' --exclude='apps/mobile/app/build' --exclude='apps/mobile/.gradle' --exclude=_to_delete \
  --exclude='*.apk' --exclude='docs/app' --exclude=__pycache__ --exclude='*.pyc' --owner=0 --group=0 -cf - . | tar -C "$ROOT/opt/orion-src" --no-same-owner -xf -
# Sources copied from Windows/WSL can carry 0700 permissions: make the tree readable like a git checkout.
chmod -R u+rwX,go+rX,go-w "$ROOT/opt/orion-src"
echo "$VERSION" > "$ROOT/opt/orion-src/BUNDLE_VERSION"
ls "$ROOT"/opt/orion-src/os/dist/*.deb >/dev/null 2>&1 || { echo "!! os/dist/*.deb (the HUD) is missing"; exit 1; }
in_root env ORION_PROFILE=image bash /opt/orion-src/os/lab/provision.sh

# ------------------------------------------------------------------ 4. USB-only parts: vault unlock, welcome, A/B slots, splash
step "4/7 USB parts: vault unlock, welcome, A/B updates, boot splash"
# Copy the USB-only files without touching the permissions of existing system folders (/etc, /usr, ...):
# a source tree with odd permissions once made /usr unreadable for every user except root.
tar -C "$HERE/files" --owner=0 --group=0 --mode='u+rwX,go+rX,go-w' -cf - . | tar -C "$ROOT" --no-overwrite-dir -xpf -
# unit files copied from a Windows checkout arrive marked executable; systemd warns about that at every boot
find "$ROOT/etc/systemd" "$ROOT/usr/lib/systemd" -type f \( -name '*.service' -o -name '*.timer' -o -name '*.path' \
  -o -name '*.socket' -o -name '*.target' \) -perm /111 -exec chmod 644 {} + 2>/dev/null || true
grep -q '^CRYPTSETUP=y' "$ROOT/etc/cryptsetup-initramfs/conf-hook" 2>/dev/null || echo 'CRYPTSETUP=y' >> "$ROOT/etc/cryptsetup-initramfs/conf-hook"
printf 'REPO=%s\nFLAVOUR=%s\n' "${ORION_RELEASE_REPO:-orionopenwow/OrionOS}" "$FLAVOUR" > "$ROOT/etc/orion/release.conf"
if [ -n "${ORION_RELEASE_KEY:-}" ] && [ -f "${ORION_RELEASE_KEY}.pub" ]; then
  tail -n1 "${ORION_RELEASE_KEY}.pub" > "$ROOT/etc/orion/release.pub"
  (echo "untrusted comment: OrionOS release key"; cat "$ROOT/etc/orion/release.pub") > "$ROOT/etc/orion/release.pub.tmp"
  mv "$ROOT/etc/orion/release.pub.tmp" "$ROOT/etc/orion/release.pub"
fi
in_root systemctl enable orion-welcome.service orion-slot-good.service NetworkManager.service zramswap.service >/dev/null 2>&1 || true
# orion-install-disk needs these inside the image (it copies this system to the drive and makes it start there)
for t in grub-install update-grub os-prober efibootmgr grub-editenv mkfs.btrfs cryptsetup sfdisk python3; do
  in_root sh -c "command -v $t" >/dev/null || { echo "!! $t is missing from the image: orion-install-disk can't work"; exit 1; }
done
[ -f "$ROOT/opt/orion/dualboot/orion_partsel.py" ] || { echo "!! /opt/orion/dualboot is missing (os/image/os-dualboot.sh)"; exit 1; }
# Casper would add a "ubuntu" live user with password-less sudo and auto-login: OrionOS doesn't want either.
rm -f "$ROOT"/usr/share/initramfs-tools/scripts/casper-bottom/*adduser* "$ROOT"/usr/share/initramfs-tools/scripts/casper-bottom/*autologin*
# Casper: no live user (OrionOS has its own accounts), and the right name.
printf 'export USERNAME=""\nexport USERFULLNAME=""\nexport HOST="orionos"\nexport BUILD_SYSTEM="Ubuntu"\nexport FLAVOUR="OrionOS"\n' > "$ROOT/etc/casper.conf"
mkdir -p "$ROOT/etc/plymouth" "$ROOT/etc/initramfs-tools/conf.d"
printf '[Daemon]\nTheme=orion\nShowDelay=0\n' > "$ROOT/etc/plymouth/plymouthd.conf"
echo FRAMEBUFFER=y > "$ROOT/etc/initramfs-tools/conf.d/splash"
in_root update-alternatives --install /usr/share/plymouth/themes/default.plymouth default.plymouth \
  /usr/share/plymouth/themes/orion/orion.plymouth 200 >/dev/null
in_root update-alternatives --set default.plymouth /usr/share/plymouth/themes/orion/orion.plymouth >/dev/null
KV="$(ls "$ROOT/lib/modules" | sort -V | tail -n1)"
in_root update-initramfs -c -k "$KV" 2>/dev/null || in_root update-initramfs -u -k "$KV"
# shrink: caches and per-machine identity
in_root apt-get clean
if [ -f "$ROOT/usr/local/share/ca-certificates/orion-build-extra.crt" ]; then
  rm -f "$ROOT/usr/local/share/ca-certificates/orion-build-extra.crt"; in_root update-ca-certificates --fresh >/dev/null 2>&1 || true
fi
rm -rf "$ROOT"/var/lib/apt/lists/* "$ROOT"/var/cache/apt/*.bin "$ROOT"/tmp/* "$ROOT"/var/tmp/* "$ROOT"/root/.cache
rm -f "$ROOT/usr/sbin/policy-rc.d" "$ROOT"/etc/ssh/ssh_host_*
: > "$ROOT/etc/machine-id"; rm -f "$ROOT/var/lib/dbus/machine-id"
ln -sf ../run/systemd/resolve/stub-resolv.conf "$ROOT/etc/resolv.conf"
cleanup; mounts=()
fi
KV="$(ls "$ROOT/lib/modules" | sort -V | tail -n1)"

# ------------------------------------------------------------------ 5. the system slot
step "5/7 Squashing the system (xz; several minutes)"
SLOT="$WORK/slot"; rm -rf "$SLOT"; mkdir -p "$SLOT"
cp "$ROOT/boot/vmlinuz-$KV" "$SLOT/vmlinuz"
cp "$ROOT/boot/initrd.img-$KV" "$SLOT/initrd"
rm -f "$SLOT/filesystem.squashfs"
mksquashfs "$ROOT" "$SLOT/filesystem.squashfs" -noappend -comp xz -b 1M -Xdict-size 100% \
  -wildcards -e 'proc/*' 'sys/*' 'dev/*' 'run/*' 'tmp/*' 'var/log/*' 'var/tmp/*' >/dev/null
du -sx --block-size=1 "$ROOT" | cut -f1 > "$SLOT/filesystem.size"
echo "$VERSION" > "$SLOT/VERSION"
bash "$HERE/scan-image.sh" "$ROOT" "$OUT/orionos-$VERSION-$FLAVOUR" || true   # Toolkit 19: SBOM + known holes (syft/grype, if installed)

# ------------------------------------------------------------------ 6. the disk image
step "6/7 Assembling the USB image"
IMG="$OUT/orionos-$VERSION-$FLAVOUR.img"
EFI_MB=256
SLOT_MB=$(( $(du -s --block-size=1M "$SLOT" | cut -f1) + 64 ))
SYS_MB=$(( SLOT_MB * 2 + 256 ))
# ESP: Ubuntu's signed shim first, then signed GRUB; grub.cfg chooses the slot.
ESP="$WORK/esp.img"; rm -f "$ESP"
mkfs.vfat -C -F 32 -n ORIONEFI "$ESP" $(( EFI_MB * 1024 )) >/dev/null
export MTOOLS_SKIP_CHECK=1
mmd -i "$ESP" ::/EFI ::/EFI/BOOT ::/EFI/ubuntu ::/EFI/orion
mcopy -i "$ESP" "$ROOT/usr/lib/shim/shimx64.efi.signed.latest" ::/EFI/BOOT/BOOTX64.EFI 2>/dev/null \
  || mcopy -i "$ESP" "$ROOT/usr/lib/shim/shimx64.efi.signed" ::/EFI/BOOT/BOOTX64.EFI
mcopy -i "$ESP" "$ROOT/usr/lib/shim/mmx64.efi" ::/EFI/BOOT/mmx64.efi
mcopy -i "$ESP" "$ROOT/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed" ::/EFI/BOOT/grubx64.efi
cp "$HERE/grub.cfg" "$WORK/grub.cfg"
# NVIDIA flavour: kernel mode setting on from the first moment (Sway can't use the card without it). Not on the
# "safe graphics" entry, which starts with nomodeset on purpose.
if [ "$FLAVOUR" = nvidia ]; then
  sed -i -E '/nomodeset/! s/(orion\.slot=\$\{boot_slot\})/\1 nvidia-drm.modeset=1 nvidia-drm.fbdev=1/' "$WORK/grub.cfg"
  grep -q 'nvidia-drm.modeset=1' "$WORK/grub.cfg" || { echo "!! grub.cfg: NVIDIA boot option not added"; exit 1; }
fi
mcopy -i "$ESP" "$WORK/grub.cfg" ::/EFI/BOOT/grub.cfg
mcopy -i "$ESP" "$WORK/grub.cfg" ::/EFI/ubuntu/grub.cfg
rm -f "$ROOT/tmp/grubenv"; chroot "$ROOT" grub-editenv /tmp/grubenv create; chroot "$ROOT" grub-editenv /tmp/grubenv set slot=a
mv "$ROOT/tmp/grubenv" "$WORK/grubenv"
mcopy -i "$ESP" "$WORK/grubenv" ::/EFI/orion/grubenv
bash "$HERE/art-boot.sh" esp "$ROOT" "$ESP"   # Art Update (art-boot): GRUB theme (stars, mark, Unbounded) into the ESP
# System partition: slot a filled, slot b empty until the first update.
SYSD="$WORK/sysdir"; rm -rf "$SYSD"; mkdir -p "$SYSD/slots/a" "$SYSD/slots/b"
cp -a "$SLOT/." "$SYSD/slots/a/"
SYS="$WORK/sys.img"; rm -f "$SYS"
mkfs.ext4 -q -F -L ORIONSYS -m 0 -O ^metadata_csum_seed -d "$SYSD" "$SYS" "${SYS_MB}M"
rm -rf "$SYSD"
# Stitch: 1 MiB gap, ESP, system, 1 MiB for the backup GPT.
TOTAL_MB=$(( 1 + EFI_MB + SYS_MB + 1 ))
rm -f "$IMG"; truncate -s "${TOTAL_MB}M" "$IMG"
sfdisk -q "$IMG" <<EOF
label: gpt
start=2048, size=$(( EFI_MB * 2048 )), type=U, name="ORIONEFI"
start=$(( 2048 + EFI_MB * 2048 )), size=$(( SYS_MB * 2048 )), type=L, name="ORIONSYS"
EOF
dd if="$ESP" of="$IMG" bs=1M seek=1 conv=notrunc,sparse status=none
dd if="$SYS" of="$IMG" bs=1M seek=$(( 1 + EFI_MB )) conv=notrunc,sparse status=none
rm -f "$ESP" "$SYS"

# ------------------------------------------------------------------ 7. update bundle, checksums, signatures
step "7/7 Update bundle, checksums, signatures"
tar -C "$SLOT" -cf "$OUT/orionos-$FLAVOUR-slot.tar" vmlinuz initrd filesystem.squashfs filesystem.size VERSION
echo "$VERSION" > "$OUT/orionos-$FLAVOUR.version"
(cd "$OUT" && sha256sum "$(basename "$IMG")" "orionos-$FLAVOUR-slot.tar" > "SHA256SUMS-$FLAVOUR")
if [ -n "${ORION_RELEASE_KEY:-}" ]; then
  for f in "$IMG" "$OUT/orionos-$FLAVOUR-slot.tar"; do
    minisign -S -s "$ORION_RELEASE_KEY" -m "$f" ${ORION_RELEASE_KEY_PASSWORD:+-W} </dev/null >/dev/null
  done
fi
ls -la "$OUT"
step "Done: $IMG"
