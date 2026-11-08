# Orion on your phone

Two stages. Stage 1 works on any Android phone and is undone in one tap. Stage 2 is a hardened Pixel running
GrapheneOS, plus an optional "Pocket Orion" that keeps working when your PC is off.

The rules don't change on the phone: messages are only sent after you tap Send, approvals need your fingerprint,
and the camera and hot mic are off until you turn them on.

---

## Stage 1: Orion on any Android phone

### 1. Install the app
1. On the PC, double-click **Build Orion APK.cmd**. It builds `Orion-Mobile.apk` in the Orion folder and
   installs it if your phone is plugged in by USB.
2. No USB? Copy `Orion-Mobile.apk` to the phone (USB, cloud drive or email) and tap it. Android asks you to
   allow installs from that app once.

### 2. Pair it with your PC
1. **On the PC:** open Orion's menu and choose **Phone** (or Settings > Pair my phone on OrionOS). It shows a big
   QR code and a code.
2. **On the phone:** point the camera at the QR code and tap the link. (Or open Orion > Menu > Settings >
   **Pair with my PC**: your PC shows up in the list when both are on the same Wi-Fi; tap it and type the code.)
3. **Check the five words match** on both screens, then press **Confirm** on the PC. The phone moves on by itself.

Then tap **Test** in the phone's menu: it checks everything and shows one "Next" card.

If something gets in the way, the screens say so in plain words:
- Windows: "Windows Firewall stops your phone reaching this PC" -> press **Let my phone in** in the Phone panel
  (Windows asks for admin permission once; only your home Wi-Fi is let in).
- OrionOS lab inside VirtualBox: run **Open Phone Access.cmd** on Windows once, then press Pair again.
- "Your phone isn't on the same Wi-Fi as your PC": the phone is on mobile data. Join your home Wi-Fi, or use
  Tailscale when you're away. If the PC is asleep, Pocket Orion on the phone answers chat and voice meanwhile.
- The PC's address changed: nothing to do. The phone finds the same PC again on the Wi-Fi and checks the pairing.

The old way still works: `orion-ctl pair-phone` in an OrionOS terminal.

### 3. Make Orion part of the phone (all optional)
Each of these is a normal Android setting. Android's menu names differ a little between phone makers, so if a
label doesn't match exactly, search for it in the Settings search bar.

| What | Where (search Settings for) |
|---|---|
| Orion as your Home screen | "Default apps", then "Home app", pick Orion |
| Long-press power opens Orion | "Default apps", then "Digital assistant app", pick Orion |
| The black hole wallpaper (also on the lock screen) | Long-press the home screen, "Wallpaper", pick Orion's live wallpaper |
| One inbox for your messages | "Notification access" (or "Device & app notifications"), turn on Orion |

With notification access on, Orion reads incoming messages on the phone and drafts replies in your style.
Nothing is sent until you tap Send.

**Mirror inbox to my PC** is off by default. If you want your phone messages on the PC too, turn it on on the PC
(`python apps/daemon/phone_pair.py mirror on`, or the Orion settings). Mirrored messages are read-only on the PC
and are deleted after 7 days.

### 4. Send work to the PC (Missions)
Your PC's Mission Control runs coding work for you (a "mission": one job, one agent, its own branch and budget).
From the phone you can hand it work and keep an eye on it:

- **Home screen:** long-press empty sky, then **Send work to the PC**. Or two-finger swipe for agents, then
  **Missions**.
- **Share sheet:** share any text (an idea, a bug report, a link) and pick **Send work to PC**. The text lands in
  the box; nothing is sent until you tap **Send to PC**.
- **Voice or chat:** "Orion, have the PC build a price page" (also "get my computer to fix the tests", "send this
  to the PC: write release notes"). Orion queues it and says so.

New missions are only *queued*: nothing is merged from the phone. The Missions screen shows every mission's state,
what it has spent and its budget. **Pause**, **Resume** and **Stop** work with a tap (Stop asks twice).
**Approve review** and **Reject** need your fingerprint, every time. Robots on the home screen show what each
one has spent ("$1.20 of $5"); tap a mission robot to open it. If the PC doesn't have Mission Control yet, the
screen says "Mission Control isn't on this PC yet" - update Orion on the PC.

If Orion is frozen, the PC refuses new missions and Resume until you unfreeze it; Pause and Stop still work.

### Staying connected
The home screen's top line says how the link is: **Connected**, **Reconnecting…**, **PC asleep — using Orion on
this phone** (when Pocket Orion is set up), **Not on the same Wi-Fi**, or **Can't reach your PC — tap to fix**. The
PC's Phone panel shows the same for each phone, with when it was last seen and how good the link is.

The phone finds your PC again by itself when you switch between Wi-Fi and mobile data, when the PC gets a new
address, after the PC sleeps or Orion restarts. It tries your home address first, then the PC's Tailscale address
(if both have Tailscale), then looks for the PC on the Wi-Fi. It only moves to a new address after the PC proves it
holds the key saved when you paired. A phone clock a few minutes off is corrected automatically. An approval whose
answer gets lost on the way back is sent again safely: it is never applied twice and you don't need your
fingerprint again.

Still stuck? Tap the line (or **Test > Fix the connection**). It checks the Wi-Fi, the PC's port, the firewall,
the clocks and the PC's key, and says the one thing to do. The PC's Phone panel has the same button.

### Undo Stage 1
- Home screen: "Default apps", "Home app", pick your old launcher.
- Assistant: "Default apps", "Digital assistant app", pick your old one (or None).
- Wallpaper: pick any other wallpaper.
- Messages: turn Orion off in "Notification access".
- Everything: uninstall the app. On the PC, `orion-ctl phones` lists paired phones and
  `orion-ctl unpair-phone <id>` (or `all`) removes a phone's keys.

---

## Stage 2: a hardened phone (GrapheneOS on a Pixel 8 or newer)

GrapheneOS is a security-focused version of Android for Pixel phones. Orion runs on it unchanged.

**Use the official guide while you do this:** <https://grapheneos.org/install/web>. It is the source of truth;
the summary below only helps you know what's coming. If anything here differs from the official page, follow
the official page.

### Before you start
- **Everything on the phone is erased** (more than once). Back up photos, chats and 2-factor codes first.
- You need a computer with a supported browser (for example Chrome, Edge or Brave; not a private window) and
  the phone's USB-C cable, plugged straight into the computer, not a hub. The official page lists the supported
  systems, browsers and, on Linux, the extra USB setup.
- Buy an unlocked Pixel, not a carrier version: carrier versions often can't be unlocked.

### The steps (summary of the official web installer)
1. **Turn on OEM unlocking.** Turn on Developer options (Settings, About phone, tap "Build number" several
   times), then turn on "OEM unlocking" in Developer options. The phone needs internet for this once.
2. **Start the phone in its bootloader.** Turn it off, then hold volume down while it starts, until you see
   "Fastboot Mode". Don't press "Start".
3. **Connect it and unlock the bootloader** from the web installer page. Confirm on the phone with the volume
   and power buttons. *This erases the phone.*
4. **Download and flash** the release from the web installer page. Don't touch the phone while it works.
   *This replaces Android and erases the phone.*
5. **Lock the bootloader again** from the web installer page and confirm on the phone. *This erases it once
   more.* Locking matters: it turns on full verified boot, which is what makes the phone hard to tamper with.
6. **Start it** and go through setup. The last setup screen offers to turn OEM unlocking back off: leave that
   checked.
7. **Check it's genuine** (optional but good): the official page explains the boot key hash and the Auditor app.

### Google apps (optional)
GrapheneOS can run Google Play in a normal app sandbox ("sandboxed Google Play") if you need apps that depend on
it. Orion doesn't need it. Install it only if your other apps do, from GrapheneOS's own App Store app; see the
official usage guide: <https://grapheneos.org/usage#sandboxed-google-play>.

### Then install Orion
Same as Stage 1: install `Orion-Mobile.apk`, pair, and set Home / assistant / wallpaper / notification access.

---

## Pocket Orion (Stage 2 extra): Orion when the PC is off

Android 16 includes a **Linux terminal** that runs a small Debian system on the phone. Pocket Orion runs there:
voice (if the phone has the memory), Video Scout, your vault of notes, and a tiny local model (only on phones
that can spare 4 GB or more for Linux). It never does approvals, money, sending messages, agents, the hot mic or
anything on your PC.

### Turn on the Linux terminal
The terminal is part of Android 16 on Pixels, behind Developer options. Turn on Developer options (see above),
look for the Linux development environment option there, then open the **Terminal** app and let it download
Debian. Labels can change between Android versions; check your phone's help pages if you can't find it.
Linux only gets part of the phone's memory (often about a third).

### Install Pocket Orion
1. Copy the Orion folder (at least `apps/daemon` and `os/phone`) to the phone's **Download** folder.
   Inside the terminal the shared folder appears under `/mnt/shared` (check with `ls /mnt/shared`).
2. In the terminal, see what it would do first, then run it:

   ```sh
   bash /mnt/shared/Download/orion/os/phone/orion-pocket-setup.sh --dry-run
   bash /mnt/shared/Download/orion/os/phone/orion-pocket-setup.sh
   ```

   It installs into `~/orion-pocket` with its own Python environment, skips voice and the model if the phone is
   too small, asks before installing Ollama, and starts Pocket Orion as a user service (or gives you
   `~/orion-pocket/run-pocket.sh` if the terminal has no service manager).
3. **Allow the port.** When Pocket Orion starts listening on port 9121, Android asks whether to let the terminal
   open that port. Allow it. The Android side then reaches it at `http://127.0.0.1:9121`. You can review
   forwarded ports later in the Terminal app's settings. (If the app can't connect, start it with
   `ORION_POCKET_BIND=0.0.0.0 orion-pocket serve`; every request is still signed by your phone.)
4. **Pair the app with Pocket Orion:** run `orion-pocket pair` in the terminal. In the Orion app open
   **Phone setup** (chat screen, the gear, then "Phone setup and inbox") and find the **Pocket Orion** card:
   type the code and tap **Pair with Pocket Orion**. The app shows a fingerprint; check it matches, then run
   `orion-pocket confirm <fingerprint>` in the terminal. The card then says **On** (you can turn it off or forget it
   there).

When your PC can't be reached at all, chat, hold-to-talk, the assistant (long-press power), Scout links and inbox
drafts go to Pocket Orion instead, and every answer starts with **"PC is off — using Orion on this phone"**. The
home screen's status line says the same. A PC that is on but slow is never swapped out: you get its answer.

What never goes to Pocket Orion: approvals, freezing and unfreezing, agents, missions, the hot mic and the inbox
mirror. Pocket Orion refuses those even if asked. Approvals wait until the PC is back. Asking Pocket Orion to
"have the PC build ..." gets a plain "Your PC is off" instead.

Put `.md` or `.txt` guides in `~/orion-pocket/data/vault` for offline answers.

**Undo:** `systemctl --user disable --now orion-pocket`, then delete `~/orion-pocket`. Removing the Linux
terminal in Developer options deletes everything in it.

---

## For the phone app (developers)

- PC routes: `GET /phone/home`, `POST /phone/draft`, `POST /phone/inbox`, `POST /phone/hotmic`,
  `POST /phone/agent/{id}/{pause|stop|resume}` (see `apps/daemon/phone_pair.py`). Hot mic off works with the
  read key; hot mic on and agent controls need the fingerprint (sign) key. `/phone/inbox` answers 403 until the
  user turns the mirror on at the PC.
- Missions (`apps/daemon/phone_missions.py`, registered from `phone_api.register`; Mission Control itself is
  `apps/daemon/missions.py`, imported lazily):
  `GET /phone/missions` (read key) -> `{"available", "missions": [{"id","title","repo","agent","state","cost_usd",
  "budget_usd","started","branch","summary"}], "message"?}`; `POST /phone/mission` (read key)
  `{"title","repo"?,"agent"?,"budget_usd"?}` queues a mission with source "phone" and never merges;
  `POST /phone/mission/{id}/{pause|resume|stop}` (read key); `POST /phone/mission/{id}/{approve_review|reject}`
  (sign key only). `GET /phone/home` also carries `"missions"` (unfinished ones, for the robots) and agents may
  carry `cost_usd`. Chat and voice check "have the PC build ..." first (`phone_missions.from_words`).
  Phone side: `Missions.kt` (`MissionsApi`, `MissionsActivity`, share target "Send work to PC").
- Pocket fallback: `apps/mobile/.../PocketFallback.kt` (`chat`, `voice`, `scout`, `draft`, `shown`, `pair`,
  `ready`) and the Phone setup card `PocketCard.kt`. The PC is tried first; only when its port doesn't answer
  (and Pocket Orion is on, paired and answers `GET /pocket/hello` unsigned with `{"orion": "pocket"}`) does the
  same signed request go to `http://127.0.0.1:9121`. Answers from there carry `"pocket": true`. The saved PC
  address is never changed (`Lab.urlOverride`). Pocket Orion (`os/phone/pocket/orion_pocket.py`) answers 403 on
  `/phone/verdict`, `/phone/pending`, `/phone/card/do`, `/phone/freeze`, `/phone/unfreeze`, `/phone/hotmic`,
  `/phone/agent*`, `/phone/mission*` and `/phone/inbox`.
- Pairing from the HUD (`apps/daemon/phone_pairing_ui.py`, local port, daemon key): `POST /phones/pair/open` ->
  `{"code","url":"orion://pair?u=<address:9121>&c=<code>","qr_svg","expires","address","lan":{"ok","words","fix",
  "fix_words"}}`; `GET /phones/pair/status` -> `{"open","pending":[{"device_id","name","fingerprint","words"}],
  "phones","lan"}`; `POST /phones/{id}/confirm` `{"fingerprint"?}`; `POST /phones/pair/close`; `GET /phones`
  (name, `last_seen`, `online`, `battery`); `POST /phones/{id}/unpair`; `POST /phones/test-card`;
  `POST /phones/open-access` (Windows: runs `os/windows/Open-PhoneAccess.ps1 -ThisPC`). Phone side:
  `POST /phone/hello` (read key) `{"battery","charging","network","app"}` -> `{"pc":{"id","name"}}`. The five
  fingerprint words are the first five bytes of the fingerprint through one 256-word list (`WORDS` in
  `phone_pairing_ui.py`, `FpWords` in `PairPc.kt`; a test keeps them equal). The QR code is drawn by
  `apps/daemon/qr_svg.py` (pure Python). With the optional `zeroconf` package the PC announces `_orion._tcp`
  (TXT `id`, `name`) while pairing is open or a phone is paired; the phone finds it with `NsdManager`
  (`PcFinder`) and uses the `id` to find the same PC again after an address change (`PcFinder.heal`).
- Test screen (`PhoneTests.kt`): phone checks (fingerprint key, notification access, assistant, PC reachable,
  Pocket Orion, battery optimisation), then `GET /phone/tests` and `POST /phone/tests/{id}/result
  {"status","note"?,"facts"?}` for the PC items those checks answer; one "Next" card. A PC without
  `/phone/tests` gets "Update Orion on your PC". Menu: `OrionMenu.kt` (Talk, Approvals, Projects, Inbox, Test,
  Settings).
- Building: `Build Orion APK.cmd` -> `apps/mobile/build-apk.ps1` checks for the project files, Java 17+ (Android
  Studio's own), the Android SDK and API 34, builds `assembleDebug` and copies the APK to `Orion-Mobile.apk` in
  the Orion folder (the path is printed at the end). Bump `versionCode`/`versionName` in
  `apps/mobile/app/build.gradle.kts` for every APK you hand out (now 3 / 0.3.0).
