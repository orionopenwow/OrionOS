# 2. OrionOS on a USB stick

Start any PC from a stick and you're in Orion. Your stuff lives in an encrypted vault on the stick. The PC's own
drive isn't touched.

**You need:** a USB stick of 32 GB or more (USB 3 or a USB SSD is much faster). It will be **erased**. A PC that
starts in UEFI mode (most PCs from about 2012). **Time:** about 30 minutes.

1. On Windows, run **`Check if my PC is ready for Orion.cmd`**. Note the line **"Which OrionOS image"**:
   *standard*, or *nvidia* for NVIDIA graphics.
2. Double-click **`Make Orion USB.cmd`** (one admin prompt). For NVIDIA graphics run it from a terminal as
   `"Make Orion USB.cmd" -Flavour nvidia`.
3. It uses an image you built, or downloads the newest release (checksum checked; signature too if `minisign.exe`
   is installed). It lists **only USB drives**: type the stick's number, then type it again to confirm.
4. It writes the stick and reads it back to check. If Windows then says the stick needs formatting, click
   **Cancel** (Windows just can't read Linux drives).
5. Restart with the stick in and tap the boot-menu key (F12, F9, F11 or Esc; the check and
   `Prepare Laptop for Orion.cmd` tell you yours). Secure Boot can stay on.
6. Pick **OrionOS**. The first time, make the vault: a passphrase, plus a **recovery key to write on paper**.
   Then choose the orion-admin password and connect to Wi-Fi.
7. Orion opens and walks you through your phone, Orion's brain and voice.

**Updates:** Settings > Check for OrionOS updates. The stick holds two systems; if a new one doesn't start, the
next start goes back to the old one by itself (`orion-slot status` says what happened).

**Undo:** take the stick out; the PC starts Windows as before. To reuse the stick, format it in Windows
(Disk Management: delete its partitions, then make a new one).

*Tested so far in virtual machines only (QEMU with Secure Boot on, VirtualBox), not yet on a real laptop.*
