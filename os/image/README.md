# OrionOS 1.0: the USB stick

Put OrionOS on a USB stick, start almost any PC from it, and you're in Orion. Your stuff lives in an encrypted
vault on the stick, updates swap the whole system and roll back by themselves if something breaks, and "Install
OrionOS to this PC" copies it onto the computer when you're ready.

## Making a stick (Windows)

1. Double-click **`Make Orion USB.cmd`** in the Orion folder (one admin prompt).
2. It uses an image you built, or downloads the newest release from GitHub (checksum and signature checked).
3. It lists only USB drives. Type the stick's number twice; it's erased and written, then read back to check.

Any 32 GB+ USB 3 stick works; a fast one (or a USB SSD) makes everything snappier. balenaEtcher, or Rufus in
"DD image" mode, also work with the `.img`.

## Starting a PC from it

Plug it in, restart, tap the boot-menu key (F12, F9, F11 or Esc; `Prepare Laptop for Orion.cmd` tells you yours).
Secure Boot can stay on: the stick uses Ubuntu's Microsoft-signed boot chain.

| Menu entry | What happens |
|---|---|
| **OrionOS** | Asks for your vault passphrase on the black-hole screen; everything you do is saved, encrypted. The first time, it offers to make the vault. |
| **Try Orion (nothing is saved)** | Runs from memory; the vault isn't touched; everything is gone at shutdown. |
| OrionOS (safe graphics) | For screens that stay black. |
| OrionOS, copy to memory first | 16 GB+ RAM: loads the whole system into memory, then runs faster than the stick. |

**First start:** make the vault (passphrase + a recovery key to write down), choose the orion-admin password,
connect to Wi-Fi. Then Orion opens and walks you through pairing your phone, Orion's brain (local or API key) and
voice.

**Lost the passphrase?** Type the recovery key instead. Without either, nobody can open the vault, us included.

## Updates and rollback

The stick holds two systems, slot a and slot b. **Settings > Check for OrionOS updates** (or
`sudo orion-slot update`) downloads the new release into the slot you're not using, checks its signature, and
switches on the next start. If the new system doesn't reach the Orion screen, the start after that goes back to
the old one by itself; `orion-slot status` says what happened. Your vault isn't touched by updates.

## Installing to the PC

**Settings > Install OrionOS to this PC** (`sudo orion-install-disk`):

- **Alongside Windows** uses only unallocated space (run `Prepare Laptop for Orion.cmd` in Windows first to make
  room). Windows stays as it is and appears in the start menu.
- **Whole drive** erases it (you type ERASE).
- It copies the system you're running, including what's in your vault, and offers to encrypt it (recommended).

## Building the image

On Ubuntu 24.04 (or the **OrionOS image** GitHub Action, which builds Standard and NVIDIA and publishes a release):

```bash
sudo os/image/build.sh --flavour standard --version 1.0.0     # ~25 GB free, 30-60 minutes
os/image/test-boot.sh start os/image/out/orionos-1.0.0-standard.img   # QEMU, Secure Boot on
os/image/test-boot.sh shot first                               # screenshot
```

`--flavour nvidia` adds NVIDIA's driver (`nvidia-driver-580` with Ubuntu's ready-built, signed kernel module
`linux-modules-nvidia-580-generic`; another branch with `ORION_NVIDIA_PKG=...`), turns on `nvidia-drm.modeset=1`
in the boot menu and modprobe settings, and stops the build if the module isn't really in the image.

Signed updates: make a key once (`minisign -G -p os/image/release.pub -s orion.key`), commit `release.pub`, and add
the secret key and its password as the `ORION_RELEASE_KEY` / `ORION_RELEASE_KEY_PASSWORD` repository secrets. Sticks
built by the workflow then only accept updates signed with that key.

## What's where

| Path | What |
|---|---|
| `build.sh` | debootstrap → kernel + casper + signed shim/GRUB → `os/lab/provision.sh` (profile image) → squashfs → GPT image |
| `grub.cfg` | boot menu and the A/B try-once logic (state in `/EFI/orion/grubenv`) |
| `files/` | copied into the system: vault unlock in the initramfs, `orion-vault`, `orion-welcome`, `orion-slot`, `orion-install-disk`, the boot splash |
| `package-release.sh` | splits the image and update bundle into <2 GB parts for GitHub releases |
| `test-boot.sh` | boots an image in QEMU with Secure Boot (Microsoft keys) as a USB stick |
| `art-boot.sh`, `grub-theme/` | Art Update boot chain: the GRUB theme (fonts made with grub-mkfont at build time), and the provisioning of the greeter shader, prompt, `orion` fetch, cursor and boot sound |

**The boot splash** (`files/usr/share/plymouth/themes/orion`) plays a frame sequence rendered from the black hole
shader: run `node os/theme/boot/render-frames.mjs` (or `make -C os/theme/boot frames`) before building, after the
shader changes. It loops the star fall while the initramfs is still working (finding the stick, the vault), forms
the hole once the real root is mounted, plays faster when the progress estimate says the boot is nearly done, and
always leaves the hand-off frame on screen for the login screen / HUD. Without frames it falls back to the 1.0
picture. `orion-still` is the reduced-motion version (GRUB adds `plymouth.splash=orion-still` when `motion=still`
is in grubenv, which `orion-ctl login reduced-motion on` sets).

## Limits of 1.0

- UEFI PCs only (about 2012 onward); old BIOS-only machines can't start it.
- NVIDIA graphics need the NVIDIA image; others fall back to software rendering (use "safe graphics").
- Wi-Fi adapters without free firmware won't connect; the hardware check names them.
