# 5. Your phone (Orion Mobile)

Orion Mobile puts Orion on your Android phone: approvals with your fingerprint, alerts, chat, and drafts of
replies (never sent without your tap). The full guide is **[ORION_PHONE_GUIDE.md](../ORION_PHONE_GUIDE.md)**.

**You need:** an Android phone (Android 11 or newer) on the same Wi-Fi as your OrionOS computer.
**Time:** about 15 minutes.

1. On Windows, double-click **`Build Orion APK.cmd`** to make the app, then copy the `.apk` to the phone and
   open it (allow "install unknown apps" for your file manager once).
2. On OrionOS: **Settings > Pair my phone** (or `orion-ctl pair-phone`). The first-run window offers this too.
3. In Orion Mobile, enter what the PC shows (address and pairing code). Check both screens show the same key
   fingerprint.
4. Big decisions now wait for your fingerprint on the phone.

**Orion on Windows today?** `Open Phone Access.cmd` lets the phone reach the lab VM over Wi-Fi.

**Undo:** in Orion Mobile, **Unpair this phone**; on the PC, `orion-ctl unpair-phone all`. Uninstall the app like
any other.
