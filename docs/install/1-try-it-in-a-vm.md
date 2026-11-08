# 1. Try OrionOS in a VM

OrionOS runs in a window on Windows. Nothing on your PC changes.

**You need:** Windows 10 or 11, VirtualBox 7 (free; `winget install Oracle.VirtualBox`), 8 GB of memory or more,
about 35 GB free, and the OrionOS VM image parts in `os\image\out\vm\` (`orionos-1.0.0.vdi.001`, `.002`, ...).
**Time:** about 15 minutes.

1. Double-click **`Try OrionOS VM.cmd`** in the Orion folder.
2. It joins the image parts into one disk, checks its checksum, and grows it to 32 GB (it only uses what's written).
3. It makes a VM called "OrionOS 1.0 (test)" (UEFI, up to 4 CPUs, half your memory up to 8 GB) and starts it.
4. In the boot menu pick **OrionOS** (or **Try Orion** to keep nothing). Black screen? Restart the VM and pick
   **OrionOS (safe graphics)**.
5. The first start offers to make the encrypted vault, then asks for the orion-admin password and opens Orion.
6. Press the **right Ctrl** key to get the mouse back to Windows.
7. **Bring my stuff (optional):** in Windows, run `Move my stuff to Orion.cmd`, type a VirtualBox shared folder (or
   pick a USB drive you pass to the VM) and choose "just the move kit". In the VM, the first start (or
   `orion-move-in bring`) finds it, then installs your most-used apps and sets Firefox up like your Chrome.

**Slow?** Windows' own hypervisor (Hyper-V) makes VirtualBox slow. `Start OrionOS.cmd` can turn it off.

**Undo:** run `Try OrionOS VM.cmd -Reset` to delete the test VM and start again, or delete
"OrionOS 1.0 (test)" in VirtualBox (Remove > Delete all files). A newer image replaces the test VM by itself.

**The lab VMs** (`orion-lab` and `orion-boardroom`, from `Create OrionOS VMs.cmd`) are separate; this doesn't
touch them.

*Tested: this is the VM path, so yes, in VirtualBox.*
