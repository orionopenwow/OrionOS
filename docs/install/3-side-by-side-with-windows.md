# 3. OrionOS side by side with Windows

OrionOS gets its own part of the drive. At every start a menu lets you pick OrionOS or Windows.

**You need:** the OrionOS USB stick from guide 2, about 100 GB free on C: (at least 25 GB for OrionOS, plus room
for Windows), your **BitLocker recovery key** if the drive is encrypted, and a cable or charger nearby.
**Time:** 1-2 hours.

1. **Check:** run `Check if my PC is ready for Orion.cmd`. You want *Ready for side by side* or *Ready to replace
   Windows*. Fix anything it lists first.
2. **Back up** anything you can't lose (an external drive, or `Move my stuff to Orion.cmd`, guide 4 step 2).
3. **Prepare:** run **`Prepare Laptop for Orion.cmd`** (one admin prompt). It shows your BitLocker recovery key
   and stops until you type SAVED, turns off Windows Fast Startup (with your OK), tells you your boot-menu key,
   and opens Disk Management: right-click C: > **Shrink Volume**, type the size it shows, click **Shrink**.
   Leave the new "Unallocated" space empty.
   When it then starts downloading Ubuntu, close the window: the OrionOS stick replaces that older route.
4. **Start from the stick** (guide 2, steps 5-7) and set it up.
5. In OrionOS: **Settings > Install OrionOS to this PC** (asks for the orion-admin password), or
   `sudo orion-install-disk`. Pick the PC's drive, then **1) Alongside what's there**. It only uses the free space;
   Windows' partitions aren't touched. Say yes to encryption and snapshots (both recommended).
6. Take the stick out and restart. By default **Windows stays first** (safest with BitLocker) and OrionOS is added as
   the second boot choice. To switch:
   - From Windows: double-click **`Restart into Orion.cmd`** (restarts into OrionOS once).
   - From OrionOS: **Settings > Updates > Restart into Windows** (or the Rescue panel button).
   - Or press your boot-menu key while the PC starts and pick **OrionOS**.
   The installer can make OrionOS first instead. It explains the BitLocker risk and asks you to type KEY.
7. If Windows asks for the BitLocker recovery key once, type the one you saved.
8. **Bring my stuff:** before step 4, in Windows, run `Move my stuff to Orion.cmd` and pick **0) this PC's Windows
   drive**: it puts just the move kit (apps, Chrome set-up for Firefox, Wi-Fi names) in C:\OrionMove. On OrionOS's
   first start, **Bring my stuff** finds it on the Windows drive (read-only; it asks for the admin password, and
   for the BitLocker recovery key if the drive is locked) and sets everything up after one yes. Your files stay
   where they are: Settings > **Open Windows files** shows them.
9. **Games already on Windows:** Settings > **Games** (or `orion-games`) finds your Steam, Epic and WoW games on
   the Windows drive and copies them for Steam, Heroic or Battle.net, so they aren't downloaded again (they can't
   run straight off the Windows drive: OrionOS keeps it read-only).

**Undo:** start Windows, open Disk Management, delete the two OrionOS partitions (after C:, with no drive letter:
one of 1 GB and one using the rest of the space you freed), then right-click C: > Extend Volume. The OrionOS entry in the start menu is
harmless; your PC's firmware settings can remove it.

*Tested so far in virtual machines only. On a real laptop, keep your recovery key and a backup at hand.*
