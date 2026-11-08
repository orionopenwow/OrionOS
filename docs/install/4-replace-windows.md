# 4. Replace Windows with OrionOS

Your files, bookmarks, Wi-Fi networks and app list move over; then OrionOS takes the whole drive.

**You need:** the check saying *Ready to replace Windows*; a USB drive or external disk big enough for your files
(exFAT or NTFS; FAT32 can't hold files over 4 GB); the OrionOS USB stick from guide 2 (a different device from
that drive: making the stick erases it); your BitLocker recovery key if you have one.
**Time:** 2-4 hours, mostly copying.

1. **Check:** run `Check if my PC is ready for Orion.cmd`. Read its app list: anything under *No replacement yet*
   stays behind. Keep the saved report.
2. **Move my stuff:** plug in the external drive and run **`Move my stuff to Orion.cmd`**. Pick the drive. It
   copies Documents, Desktop, Pictures, Videos, Music (Downloads if you say so), any extra folder you add (your
   WoW 1.12 client, projects), your browsers' bookmark files (never passwords or cookies), your Wi-Fi network
   names, and your app list, into a folder called **OrionMove** with a checksum for every file. It also makes the
   **move kit**: your apps ranked by how much you really use them, and your Chrome set-up for Firefox (bookmarks,
   extensions and their Firefox versions, search engines, Tampermonkey scripts). For every Tampermonkey script,
   first do Tampermonkey > Utilities > Zip > Export in Chrome; it's picked up from Downloads. Passwords, cookies,
   sign-ins and history are **never** read: the kit's README.txt shows how to export your passwords yourself. Wi-Fi
   *passwords* only if you say yes, locked with a passphrase you choose (run it as administrator for those).
   Files only in OneDrive's cloud are downloaded as they're copied. **Nothing on Windows is deleted.**
3. **Look:** open the OrionMove folder and check your important files are there.
4. **The boardroom VM** (if you use one): Move my stuff offers to copy its disk. Shut it down first.
5. **Make the stick** (guide 2) and start the PC from it. Set up the vault, admin password and Wi-Fi.
6. **Install:** Settings > **Install OrionOS to this PC**. Pick the PC's drive, then **2) Erase this whole drive**,
   and type ERASE. This is the point of no return for Windows. Say yes to encryption and snapshots.
7. Take the stick out and restart into OrionOS.
8. **Move in:** plug in the OrionMove drive. The first-run window offers **Bring my stuff** (later: Settings >
   **Bring my stuff from Windows**, or `orion-move-in bring`). One summary, one yes, then with progress: it checks
   every file, copies them into your home folders (never over a file already there), imports your bookmarks,
   adds your Wi-Fi networks, installs the apps you used most, makes Firefox your default browser, adds your
   extensions' Firefox versions (Firefox asks you to turn each one on), and opens a page with an Install link per
   Tampermonkey script. Stop any time; running it again carries on.
9. **Boardroom:** `orion-boardroom-vm import /media/.../OrionMove/boardroom/orion-boardroom.vdi` (or
   `orion-boardroom-vm create` for a new one), then `orion-ctl pair-boardroom`.
10. **Windows apps and games:** Get apps > Run Windows apps (Bottles). Games: Settings > **Games** (or the Orion
    menu > More > Games, or `orion-games`) sets up Steam (Proton), Heroic for Epic games such as Rocket League,
    Lutris with Battle.net for World of Warcraft, and your own WoW 1.12 client folder.

**Undo:** before step 6, everything is undoable: just keep using Windows. After step 6 Windows is gone; to get it
back, reinstall it from Microsoft's USB tool (microsoft.com/software-download) and copy your files back from the
OrionMove drive (they're ordinary files). Keep that drive until you're sure, then wipe it: it holds copies of
your files.

*Tested so far: OrionOS's installer in virtual machines only. Move my stuff and orion-move-in: automated tests and
PowerShell on Linux, not yet a real Windows PC. Do this first on a computer you can afford to redo.*
