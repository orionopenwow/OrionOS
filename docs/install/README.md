# Installing OrionOS

> **New here?** On Windows, double-click **Start Here.cmd** in the Orion folder: it sets everything up step by step. To check that it all works, see [docs/TESTING.md](../TESTING.md).

Short guides, from "just look at it" to "OrionOS is my only system". Each one says what you need, how long it
takes, and how to undo it. Start with the check, whatever you plan to do.

**Step 0: is my PC ready?** Double-click **`Check if my PC is ready for Orion.cmd`** in the Orion folder. It
changes nothing. It checks start-up mode, Secure Boot, free space, BitLocker, memory, your Wi-Fi, graphics, sound
and touchpad chips, and your apps, then says one of: *Ready to replace Windows*, *Ready for side by side*, or
*Keep Windows for now because ...*. The report is also saved to your Desktop.

| Guide | What it does to your PC | Time |
|---|---|---|
| [1. Try it in a VM](1-try-it-in-a-vm.md) | Nothing: OrionOS runs in a window on Windows | 15 min |
| [2. USB stick](2-usb-stick.md) | Nothing: the PC starts from the stick; your stuff lives encrypted on the stick | 30 min |
| [3. Side by side with Windows](3-side-by-side-with-windows.md) | Adds OrionOS next to Windows; pick one at every start | 1-2 hours |
| [4. Replace Windows](4-replace-windows.md) | Moves your stuff over, then OrionOS takes the whole drive | 2-4 hours |
| [5. Your phone](5-phone.md) | Orion Mobile on Android: approvals with your fingerprint | 15 min |

## What's been tested so far

> **Read this first.** OrionOS itself (the USB stick, updates and rollback, installing to a drive) has so far been
> tested **in virtual machines only** (QEMU with Secure Boot, and VirtualBox), not yet on a real laptop. The
> Windows helpers new in this pass (*Check if my PC is ready*, *Move my stuff*) and the OrionOS side
> (`orion-move-in`, `orion-boardroom-vm`, `orion-wow-client`, *Run Windows apps*) have been checked with automated
> tests and PowerShell on Linux, **not yet on a real Windows PC or a real OrionOS install**. Until that happens,
> keep Windows (guides 1-3) on any computer you depend on, and keep your OrionMove drive until you've checked
> everything arrived.

## Running Windows apps and the boardroom on OrionOS

- **Windows apps:** Get apps > **Run Windows apps**: Bottles (Windows programs), Steam with Proton (games),
  Lutris (other game stores). WoW 1.12 on your own server: `orion-wow-client setup` (your own client folder; Orion
  never ships game files).
- **Games you already have:** Settings > **Games**, the Orion menu > More > **Games**, or `orion-games`. It finds
  games on the Windows drive (dual boot) and here, and sets up Steam (Proton), Heroic (Epic, e.g. Rocket League),
  Lutris's official Battle.net script (World of Warcraft) and your WoW 1.12 client folder; GameMode and MangoHud
  are optional. Anti-cheat: most games run, but some refuse online play on Linux and any game can break for a
  while after an update (orion-games shows notes; online lookups only if you turn them on).
- **Bring my stuff** (`Move my stuff to Orion.cmd`, then `orion-move-in bring`): your most-used apps installed,
  Firefox set up like your Chrome (bookmarks, extensions, Tampermonkey scripts), never passwords or cookies.
- **The boardroom** (the separate sign-off machine, a VirtualBox VM on Windows today) runs on OrionOS itself with
  KVM: `orion-boardroom-vm create`, or `orion-boardroom-vm import` to bring the one from Windows with its signing
  key (Move my stuff copies its disk if you ask). It's managed by orion-admin only; Orion's own user and its agents
  can't reach it. A boardroom on the same computer is less separate than a second machine; your phone's
  fingerprint approvals are the other half of the safety net.
