# Orion on Android, v2: research and decision plan

Date: 2026-11-07. Builds on the existing three-stage plan (the earlier stage plan,
`docs/ORION_PHONE_GUIDE.md`) and the built phone app (`apps/mobile`, version 0.3.0, targetSdk 34).
Research only: no repository was changed.

---

## Executive summary

- **You can get "complete control" of an Android phone without rooting it.** On a Google Pixel, you can build the
  whole operating system yourself (from GrapheneOS, the hardened open-source Android), sign it with **your own
  keys**, and then **lock the bootloader again** with your key installed. The phone then refuses to boot
  anything you didn't sign. That gives you more control than root does (you own the kernel, the system, the
  permissions and the update channel). It also keeps verified boot and the hardware-backed keys that Orion's
  fingerprint approvals depend on.
- **Root (Magisk / KernelSU / APatch) is the wrong tool for Orion's approval phone.** Rooting needs an unlocked
  bootloader. The hardware then reports the phone as "unlocked", stricter Play Integrity checks fail (banking,
  Wallet), and any program that gets root can change what the screen shows while you put your finger down.
  That breaks the trust the whole Orion approval system rests on. Keep root for a spare lab phone, if anywhere.
- **The most useful step for the next few weeks is not a new OS.** It is turning the existing APK into a
  **Device Owner** app (one `adb` command on a freshly reset phone). This is Android's built-in enterprise "admin"
  mode. With it Orion can install and update apps silently (including updating itself), grant its own
  permissions, stay running, set the always-on VPN and lock down settings. None of this needs root, unlocking or
  breaking your banking apps. Add **hardware key attestation at pairing**, so the PC can prove the phone's keys
  live in the secure chip and that the phone is locked and running the OS it expects. Add a **persistent PC
  link** using self-hosted UnifiedPush/ntfy and Tailscale. Together these deliver most of "instantly connects
  and reprograms itself" right away.
- **The phone can reprogram itself safely, using the same pattern as the desktop.** The PC builds each change in
  a copy, tests it (emulator plus the phone), shows a ghost preview, and the change waits for your fingerprint.
  It then installs as a signed update and rolls back automatically if it is unhealthy. There are three levels:
  data and skills (instant), app code (a signed APK, possible on stock Android today), and system and kernel
  (a signed A/B OTA, only on your own OS build). A small **Guardian** and the key/approval code form a
  protected core that never changes itself.
- **What we don't know yet: your exact phone model.** The decision tree in section 7 covers the options.
  Pixels 8 through 10a can do everything. Pixel 6/6 Pro support ended in October 2026. Pixel 11 isn't confirmed
  for GrapheneOS. Samsung removed bootloader unlocking in One UI 8, so Samsung phones stay on the APK route.

## Recommendation

1. **Now (Stage 1.5, about 2 to 4 weeks of build work, safe, reversible):** "Orion Max APK" on your current phone.
   It adds Device Owner mode, silent self-updates with a Guardian and automatic rollback, attestation at pairing,
   the persistent PC link (ntfy/UnifiedPush + Tailscale + Wi-Fi/Bluetooth wake-ups), an opt-in "phone hands"
   accessibility service and self-change levels U and A. **Risk:** setting Device Owner needs a phone with no
   accounts on it (normally right after a factory reset), so back up first. Nothing gets unlocked and banking
   keeps working.
2. **Next (Stage 2, one evening plus about a week of testing, low risk on a supported Pixel):** if you have, or
   buy, a **Pixel 8a / 9a / 10 / 10a**, install **official GrapheneOS** with the web installer and relock it. Make
   Orion the Device Owner during that first setup (the phone is empty anyway). Run Pocket Orion in the Linux
   terminal. **The trade:** Google Wallet tap-to-pay doesn't work on GrapheneOS, and some banking apps that
   need Google's "certified device" check fail. Check your banks' apps first (section 11).
3. **Later, only after Stage 2 has run well for a month (Stage 3, about 4 to 8 weeks to start, then about 1 to 2
   days a month to maintain):** "Orion OS Mobile" is your **own-signed GrapheneOS-based build** with Orion
   built in as a privileged system app, your own OTA server on the PC, your own key installed and the
   bootloader relocked. This gives the "complete kernel control" you asked for, done safely. Build it on a
   **separate lab Pixel first** (an older Pixel 7/7a is fine for that), with a Linux build box (64 GB RAM, 1 to 2
   TB NVMe).
4. **Don't** root the phone that signs Orion approvals, and don't buy a Samsung if you want Stage 2 or 3.

---

## 1. Where we start (what's already built)

| Built (apps/mobile, os/phone) | Notes for v2 |
|---|---|
| Launcher with the living black hole, live wallpaper, glass drawer, desktop-mode layout | Kept in every option. |
| Assistant role (long-press power), hold-to-talk, Quick Settings tiles | Kept. |
| One inbox via notification listener; drafts; send only on tap; 7-day local store; optional mirror to PC | Kept. Stage 3 can add SMS/dialer roles natively. |
| Call screening role | Kept. |
| `Keys.kt`: READ key and SIGN key in StrongBox/TEE; SIGN needs a fresh fingerprint for every use and is wiped if fingerprints change | This is the root of trust. **Gap: no key attestation is sent to the PC yet** (no `attest` anywhere in `phones.py`, `phone_pair.py` or the Kotlin). Add it (section 4). |
| Pairing: QR + code + five words + mDNS + pinned PC key | Good. targetSdk 37 will need `ACCESS_LOCAL_NETWORK` for mDNS/LAN (section 6). |
| Missions, agents, Freeze, approvals by fingerprint | Kept. |
| `PollJob` (15-minute JobScheduler) + `LiveService` (special-use foreground service) | Replace polling with a persistent link + push (section 6). |
| Pocket Orion in the Android Linux terminal (port 9121, signed, refuses approvals/money/agents) | Kept. Correction: the terminal can get up to about two-thirds of RAM (slider), not "about a third"; the VM may be killed when the screen is off (section 3a). |
| Desktop self-change: `self_program.py` (copy, tests, ghost, approve, swap, probation, rollback) + `protected_core.py` | The pattern is reused for the phone (section 5). The phone files to protect are added to `protected_core.json`. |

---

## 2. What "complete kernel control" means in practice

When people say "kernel control" on Android they usually mean one of four things:

| You want to... | Root on stock | Own-signed OS (Stage 3) |
|---|---|---|
| Change kernel settings (CPU governor, I/O, memory, zram), sysfs tunables | Yes, at runtime, from any root process | Yes, in your build's `init` scripts and kernel config. Can also be changed at runtime by a privileged Orion service you wrote, within SELinux rules you wrote. |
| Add kernel features (eBPF programs, modules, config flags) | Partly: module loading via KernelSU LKM, eBPF via root | Fully: you build the kernel from source (Pixel kernels are open source and GrapheneOS builds its own), ship eBPF programs through Android's bpfloader in the image, and change config. |
| Give Orion system powers (silent installs, settings, clipboard, status bar, lock screen, calls) | Yes, via `su` or LSPosed hooks (fragile) | Yes, as a proper privileged system app with exactly the permissions you list (`privapp-permissions` allowlist), SELinux domains and SystemUI changes. |
| Let the phone change all of the above by itself | Yes, but nothing checks it. Any root process can, malware included. | Yes, **only through signed, fingerprint-approved A/B updates with automatic fallback**. Nothing changes the running system in place. |

So **"complete control" = owning the source, the signing keys, the build and the update channel**. Root gives
runtime power but throws away the locks. An own-signed build gives the same power or more, and keeps the locks
with you holding the only key. That is the safe version of what you asked for.

Kernel self-changes are deliberately **build-time, not run-time**: the phone (or the PC for it) changes the
kernel source or config in a copy, builds it, tests it on the lab phone or emulator, and ships it as a signed OTA.
The bootloader falls back to the previous slot if it doesn't boot (section 5). Loading arbitrary code into the
running kernel stays impossible, by design.

---

## 3. The options, compared honestly

### Summary table

| | a. Max APK (stock ROM; Device Owner, Shizuku optional) | b. Root on stock-ish ROM | c1. LineageOS | c2. Official GrapheneOS + Orion APK/Device Owner | d. Own-signed GrapheneOS fork = Orion OS Mobile |
|---|---|---|---|---|---|
| Control you get | Large: enterprise admin powers, silent installs, own permissions, always-on VPN, settings lockdown, accessibility "hands", assistant, launcher. No SystemUI or lock screen replacement, no kernel. | Everything at runtime | Large (unlocked, optional root) | Same as (a), on a hardened OS | **Total**: OS, kernel, SystemUI, permissions, updates |
| Verified boot | Yes (green, Google key) | **No** (unlocked, orange) | Usually no (unlocked); relock only on a few phones | **Yes** (yellow, GrapheneOS key, verifiable with Auditor) | **Yes** (yellow, **your** key) |
| Hardware key attestation (that the PC can verify) | Yes: locked, verified, Google chain | Reports **unlocked**; can't be trusted | Unlocked | Yes: locked, GrapheneOS key | Yes: locked, **your key hash**, pinned by your PC |
| Play Integrity "device"/"strong" (banking, Wallet) | Passes | **Fails** (hiding tricks fragile, and since May 2025 hardware-backed) | Fails | Fails Google certification; many banks still work with sandboxed Play; **no Google Wallet tap-to-pay** | Same as GrapheneOS, minus apps that allow-list GrapheneOS's own keys |
| Fingerprint-approval trust | Strong | **Broken**: a root process can fake what the prompt is approving | Weak | Strongest available | Strong (as strong as how you guard your keys) |
| Stability | High | Medium: bootloops from mismatched kernels/modules; OTAs break root | Medium/high | High | Depends on you; A/B fallback protects |
| Update burden | None (Google updates the OS; Orion updates itself) | Re-patch every monthly OTA | Weekly/monthly flashes | None (GrapheneOS updates over the air) | **You**: rebase and build each GrapheneOS release (several a month); about 1 to 2 days a month with automation |
| Effort to start | About 2 to 4 weeks of dev on the existing app | An evening (plus forever upkeep) | An evening | An evening plus testing | 4 to 8 weeks plus a build box |
| Phones | Any Android 11+ (Pixel, Samsung, all) | Unlockable phones (not Samsung on One UI 8+, not carrier Pixels) | 100+ models | Pixel 6a to 10a (8+ recommended); Motorola from 2027 | Same as GrapheneOS (Pixels) |
| Reversible | Yes (remove admin or factory reset) | Re-lock wipes; Samsung Knox fuse is permanent | Re-flash stock | Re-flash stock with the Android Flash Tool | Re-flash stock |

### a. Always-on APK with maximum non-root powers (Stage 1.5)

What each power gives Orion. Most are already in the app (marked *built*).

| Power | How you get it | What Orion does with it |
|---|---|---|
| Default launcher | Settings role (*built*) | Black hole home, desktop mode on a monitor |
| Default assistant | Role (*built*) | Long-press power opens Orion; VoiceInteractionService |
| Notification listener | Settings (*built*) | One inbox, reply through each app's reply action |
| Call screening, and optionally default SMS / dialer roles | Roles | Unknown-caller screening; SMS in the inbox (default SMS role); call log |
| **Device Owner** (Android Enterprise "fully managed") | `adb shell dpm set-device-owner com.orion.mobile/.OrionAdmin` on a phone with **no accounts** (just after reset, or during GrapheneOS's first setup) | Silent install and update of any APK, including Orion itself and the Guardian. Grant or deny runtime permissions (device owners can still auto-grant camera/mic/location unless opted out at provisioning, which Orion should do; [Bayton](https://bayton.org/android/android-enterprise-faq/managing-runtime-permissions/)). Always-on VPN (Tailscale/WireGuard). User restrictions (block installs from unknown sources except Orion, block USB debugging when not needed, block adding accounts). Lock-task kiosk mode. System update policy (delay OTAs during a demo). Security and network logging into Orion's privacy dashboard. Reboot. Hide or suspend apps (focus modes). Install CA certificates. |
| One-time ADB grants (no Device Owner needed) | `adb shell pm grant com.orion.mobile android.permission.WRITE_SECURE_SETTINGS`, `adb shell dumpsys deviceidle whitelist +com.orion.mobile`, `adb shell appops set com.orion.mobile RUN_ANY_IN_BACKGROUND allow` | These survive reboots. Orion can then turn its own accessibility service on or off, switch the default assistant, change secure settings and stay out of Doze. |
| Shizuku (optional) | Started over wireless debugging; must be restarted after each reboot without root ([Shizuku](https://github.com/RikkaApps/Shizuku)) | Shell-level APIs on demand (`input`, `pm`, `settings`, `cmd`). Useful for a "power tools" panel, but **Device Owner + one-time grants cover most of it permanently**, so Shizuku is optional. |
| Accessibility service ("phone hands"), opt-in | Settings. **Sideloaded apps must first be allowed in App info > "Allow restricted settings"** (Android 13+, tightened in 15; [Android Authority](https://androidauthority.com/android-15-restricted-settings-sideloading-3481098)) | Read the screen as a UI tree, tap and type for macros, volume-key hotkeys (`onKeyEvent`), and screenshots without MediaProjection (`takeScreenshot`, API 30+). Off by default, with a visible "Orion hands on" chip. **Android 17 Advanced Protection blocks accessibility for apps that aren't real accessibility tools** ([The Hacker News, 2026-10-02](https://thehackernews.com/2026/10/android-17-advanced-protection-locks.html)). If you turn on Advanced Protection, this feature stops. |
| Foreground service + battery exemption | `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` (*built*); use FGS type `connectedDevice` for the PC link | Persistent PC link. Note Android 15: `BOOT_COMPLETED` receivers can't start microphone, camera or dataSync foreground services, so the hot word can't start itself at boot ([Android 15 FGS changes](https://developer.android.com/about/versions/15/changes/foreground-service-types)). |
| Companion Device Manager | Associate with the PC (Bluetooth LE or Wi-Fi filter) | Gets `REQUEST_COMPANION_RUN_IN_BACKGROUND` and `..._START_FOREGROUND_SERVICES_FROM_BACKGROUND`. Android binds Orion's service when the PC comes into Bluetooth range ([CDM docs](https://developer.android.com/guide/topics/connectivity/companion-device-pairing)). |
| **Silent self-update (stock Android 12+)** | `PackageInstaller` session with `USER_ACTION_NOT_REQUIRED`. Conditions: target API 29+, installer is the installer of record **or is updating itself**, declares `UPDATE_PACKAGES_WITHOUT_USER_ACTION` ([F-Droid #2380](https://gitlab.com/fdroid/fdroidclient/-/issues/2380)) | Level A self-change (section 5) works **even without Device Owner**. Device Owner extends it to the Guardian and other apps. |
| Linux terminal (AVF) | Developer options > Linux development environment (Pixels on Android 16/17; Samsung S26 with One UI 8.5) | Pocket Orion (*built*). Facts: memory slider up to about 2/3 of RAM; GPU acceleration on newer Pixels (Pixel 10 Pro yes, Pixel 6 software-only); VM can be killed when the screen is off; no kernel modules; nftables broken (use iptables-legacy); **the default `droid` user has a known password and passwordless sudo** ([AVF field guide, tested 2026-05-26](https://cdn.jsdelivr.net/gh/ferrumclaudepilgrim/claude-code-android@main/docs/avf-guide.md)). The setup script should change the password and add an iptables rule allowing only the forwarded port. |
| Desktop mode | Stable on Pixels since Android 16 QPR3 (March 2026) ([TechCabal](https://techcabal.com/2026/06/16/android-17-features)) | Orion glass desktop on a monitor (*built*) |

**Limits of the APK route:** no lock screen replacement (glance card only), no custom status bar, no clipboard
reading in the background, no OS/kernel changes, Orion can still be force-stopped by the user (that's a feature),
and the hot word costs battery (software listening in a foreground service; the green mic dot shows it).

**New 2026 risk: Google developer verification.** From 30 Sept 2026, certified Android phones in Brazil,
Indonesia, Singapore and Thailand only install apps from registered developers without extra steps. It goes
global in 2027. A free "limited distribution" account (up to 20 devices, no ID) launched in August 2026.
Unverified apps need an "advanced flow" with a 24-hour wait. **ADB installs are exempt**
([Android Authority timeline](https://www.androidauthority.com/android-sideloading-changes-timeline-3679204/);
[Awesome Android Root, verified 2026-10-14](https://awesome-android-root.pages.dev/rooting-guides/root-framework-comparison)).
GrapheneOS isn't Google-certified, so it is very likely unaffected (not officially stated). **Action:** register
Orion's signing key under a limited-distribution account before 2027, so the self-updating APK keeps installing
on stock phones.

### b. Root on a stock-ish ROM

- **Magisk** patches `boot`/`init_boot`; Zygisk modules (LSPosed) hook into app processes. **KernelSU** grants root
  from a kernel module (LKM on GKI kernels; ships an `android17-6.18` module). **APatch** patches the kernel image
  but **doesn't support 6.18 kernels, so it doesn't cover Android 17 devices**
  ([comparison, verified 2026-10-14](https://awesome-android-root.pages.dev/rooting-guides/root-framework-comparison)).
- **What breaks:** the bootloader must be unlocked, so hardware attestation reports `deviceLocked=false`. Play
  Integrity's DEVICE verdict has needed hardware-backed proof of a locked bootloader on Android 13+ since May
  2025, and STRONG also needs a security patch from the last year
  ([Android Authority](https://www.androidauthority.com/google-play-integrity-hardware-attestation-3561592/)).
  Banking, Wallet and some games stop working. Google rotated the attestation root in February 2026, which broke
  older spoofing setups. Samsung: rooting trips Knox permanently (Wallet/Pay, Secure Folder, Samsung Pass gone).
  KMI or patch-level mismatches cause bootloops, and every monthly OTA needs re-patching.
- **Why it's wrong for Orion specifically:** the SIGN key stays inside StrongBox even with root (root can't copy
  it out). But root can run code that changes **what Orion shows you** while you approve, so you approve "$5"
  and it signs "$5000". It can also inject touches and read every app's data. Orion's rule "anything
  money/public/irreversible waits for the fingerprint" only means something if the code drawing the prompt is
  trusted. On an unlocked phone the PC can't tell a clean phone from a tampered one.
- **Verdict:** not on the roadmap. If you want to play with LSPosed or eBPF, do it on a **separate lab phone**
  that is never paired as an approver. Orion's PC would pair it with the READ key only (section 4 policy).
- avbroot ([GitHub](https://www.github.com/chenxiaolong/avbroot)) can root **and** relock with a custom key. It
  still hands root to whatever asks for it, and it warns that turning "OEM unlocking" off while rooted can
  hard-brick the phone. Not recommended for the approval phone.

### c. Custom ROMs

- **LineageOS 23 (Android 16)** shipped in October 2025 for 100+ devices
  ([Android Central](https://www.androidcentral.com/apps-software/android-os/lineageos-23-brings-android-16-to-over-100-devices-despite-googles-holdups)).
  It's great for old or unsupported phones. It normally runs unlocked (no verified boot, attestation shows
  unlocked) and root is optional. It weakens the approval trust, as in (b).
- **GrapheneOS**: hardened (memory tagging, hardened malloc, stricter sandbox), no root by design, relocked
  bootloader, verified boot with its own key, Auditor app for hardware-attestation checks, sandboxed Google Play
  as an option. Ported to **Android 17 on release day (June 2026)**
  ([GrapheneOS forum](https://discuss.grapheneos.org/d/36469-grapheneos-has-been-ported-to-android-17-and-official-releases-are-coming-soon)).
  Supported devices and end of support ([Swappa, 2026-09-19](https://swappa.com/blog/grapheneos-guide-which-pixel-to-buy/)):

  | Pixel | Support ends |
  |---|---|
  | 6 / 6 Pro | Oct 2026 (ended) |
  | 6a | Jul 2027 |
  | 7 / 7 Pro | Oct 2027 |
  | 7a / Fold | May / Jun 2028 |
  | 8 / 8 Pro / 8a | Oct 2030 / May 2031 |
  | 9 / 9 Pro / 9a | Aug 2031 / Apr 2032 |
  | 10 / 10 Pro / 10a | Aug 2032 / Mar 2033 |
  | 11 | **Not confirmed**: being re-evaluated over memory tagging ([Android Headlines, 2026-09-04](https://www.androidheadlines.com/2026/09/the-pixel-11-series-might-actually-end-up-with-grapheneos-support.html)) |

  Pixel 10 got stable builds in January 2026
  ([PiunikaWeb](https://piunikaweb.com/2026/01/21/grapheneos-finally-rolls-out-stable-builds-for-pixel-10-series/)).
  Motorola announced a partnership at MWC on 1 March 2026; the first Motorola phone with GrapheneOS is planned for
  2027 ([9to5Google](https://9to5google.com/2026/03/01/motorola-confirms-grapheneos-partnership-for-a-future-smartphone-porting-features/),
  [9to5Google 2026-08-24](https://9to5google.com/2026/08/24/motorola-razr-fold-ultra-grapheneos-support-coming/)).
- **CalyxOS / /e/OS**: privacy ROMs with microG. Weaker hardening and slower patches than GrapheneOS. /e/OS often
  runs unlocked on non-Pixels. They add nothing Orion needs over GrapheneOS.
- **Upstream headwinds (2026):** Google now publishes AOSP source only twice a year (Q2 and Q4). Monthly security
  patches continue on a security-only branch
  ([PiunikaWeb](https://piunikaweb.com/2026/01/07/android-aosp-source-code-q2-and-q4-pixel-monthly-security-patches-unchanged/)).
  New Pixels and quarterly features reach custom ROMs later. GrapheneOS also says some Android 17 QPR1 APIs stayed
  Pixel-only (reported September 2026). Samsung removed bootloader unlocking in One UI 8
  ([SamMobile](https://www.sammobile.com/news/say-goodbye-to-your-custom-roms-as-one-ui-8-kills-bootloader-unlock/)).

### d. Full Orion OS (Stage 3): own-signed GrapheneOS-based build

**What changes compared with official GrapheneOS + Device Owner:**

| Gains | Costs |
|---|---|
| Orion is a **privileged system app** (`/system_ext/priv-app` or `/product/priv-app`) with an explicit `privapp-permissions` allowlist ([AOSP](https://source.android.com/docs/core/permissions/perms-allowlist)): `INSTALL_PACKAGES`, `MANAGE_ROLLBACKS` (Android's own automatic app rollback), `READ_CLIPBOARD_IN_BACKGROUND` (clipboard sync and clipboard alchemy), `WRITE_SECURE_SETTINGS`, `STATUS_BAR`, `REBOOT`, update_engine access for OTAs, `persistent` (never killed), `CAPTURE_AUDIO_HOTWORD` / hot word through the platform's sandboxed hotword path | You maintain a fork: rebase and build every GrapheneOS release (several a month), plus monthly security patches |
| **Orion SystemUI**: the black hole lock screen, a status bar with Orion's mic/camera/hands indicators, Orion Quick Settings, notification shade = Orion inbox | Lose apps that allow-list GrapheneOS's official keys via hardware attestation. Your key is unknown to them. |
| Kernel and SELinux: your config, sysfs tuning in init, your eBPF programs (for example per-app network accounting for the privacy dashboard) | Key custody is now your job: lose the keys and you must unlock and wipe to change keys; leak them and anyone can sign an "update" |
| Your OTA server on the PC; system-level self-change with A/B rollback (section 5) | Build box: 64 GB RAM minimum and about 400 GB of space per AOSP tree; a full build takes about 6 hours on a 6-core machine ([AOSP requirements](https://source.android.com/docs/setup/requirements)). Plan on 1 to 2 TB NVMe and 16+ cores. |
| Verified boot with **your** key; PC verifies the phone by attestation (`verifiedBootKey` = your key hash) | Pixel-only until the Motorola device. Pixel 11 needs a firmware update before custom ML-DSA keys can be registered (avbroot notes). |

GrapheneOS asks forks not to use its name or logo, so the build is branded "Orion OS Mobile" and credits
GrapheneOS as its base.

---

## 4. The security model on the phone (applies to all options)

**Root of trust today:** the SIGN key (P-256) is generated in StrongBox (or the TEE), needs a strong-biometric
check for every use, and is invalidated when fingerprints change. The PC pins its public key at pairing.

**Add now (Stage 1.5): key attestation at pairing.**
1. When creating READ and SIGN, call `setAttestationChallenge(nonce_from_pc)`.
2. Send the certificate chain (`KeyStore.getCertificateChain`) with the pairing request.
3. The PC (`phones.py`, protected core) checks:
   - the chain ends at Google's hardware attestation root (including the root rotated in February 2026);
   - `attestationSecurityLevel`/`keymasterSecurityLevel` = StrongBox or TEE (not software);
   - the SIGN key has `userAuthType` = biometric, timeout 0, and is invalidated on enrollment;
   - RootOfTrust: `deviceLocked = true`, and either `verifiedBootState = VERIFIED` (stock) or `SELF_SIGNED` with
     `verifiedBootKey` equal to a pinned key (the GrapheneOS official key, or **your own Orion OS key** in
     Stage 3);
   - `osPatchLevel` is no more than about 3 months old (warn) or 12 months (refuse protected-core approvals);
   - `attestationApplicationId` = `com.orion.mobile` with your signing certificate digest.
4. **Policy:** an unlocked or unverified phone can pair with the READ key only. It can chat, see status and use
   the inbox mirror, but **can never sign approvals or protected-core changes**. Re-check attestation on every
   app update and every 30 days (new key with a fresh challenge).

This follows standard attestation policy guidance: require a locked device and a verified state, and accept a
self-signed boot key only when it is your pinned key in a tightly scoped programme
([Warden Supreme technical notes](https://a-sit-plus.github.io/warden-supreme/technical/android)). Verify on the
lab phone that a relocked custom-key Pixel reports `SELF_SIGNED` plus your key hash before relying on it in
Stage 3.

**Rules carried over unchanged:**
- Voice, gestures and taps never approve money or big decisions. Only BiometricPrompt + SIGN key + the PC
  verifying it does.
- Anything public, irreversible, money, secrets or servers waits for the fingerprint.
- The approval prompt always shows the exact thing signed: amount, recipient and a short hash. Stage 3 draws it
  in a SystemUI layer that apps can't cover.
- Camera off and hot mic off by default, both visibly indicated (Android's privacy dots, plus Orion chips). Device
  Owner opts out of auto-granting sensor permissions at provisioning, so even Orion asks you.
- The safety core can't change itself (section 5).
- Privacy first: inbox kept on the phone (7 days); mirror off by default; no Google services needed; push via your
  own PC.

---

## 5. The phone reprograms itself, safely (the Orion layer on Android)

Same five steps as `self_program.py`: **change in a copy, then build and test, then ghost preview, then approve
with your fingerprint, then install with probation and automatic rollback.** There are three levels:

| Level | What changes | Built and tested where | Installed how | Rollback |
|---|---|---|---|---|
| **U: data and skills** (all options) | Rules, Grimoire phone macros, themes, shader parameters, tile layouts, inbox filters, prompts | On the phone or PC; schema checks and the macro dry-run | Hot-reload into a new **generation** folder in app storage (`layer/gens/<n>`, `current` pointer), mirroring `orion_layer.py` | Instant: point `current` back. Automatic if the app crashes twice within 2 minutes. |
| **A: app code** (Stage 1.5+) | Kotlin/UI/features of `com.orion.mobile` | PC: a git worktree `orion-self/<id>` of `apps/mobile`, Gradle build, unit + Robolectric tests, instrumentation tests on an emulator (headless) and on the phone over Wi-Fi debugging, screenshot diff | **Ghost preview:** the PC installs a side-by-side `com.orion.mobile.ghost` build (different package, no keys, no approvals, no inbox access) so you try it on the real phone. **Approve:** fingerprint signs the APK's SHA-256. **Install:** PackageInstaller with silent self-update (stock 12+), Device Owner or privileged install. | **Guardian** app (`com.orion.guardian`, tiny, protected): the new version must report healthy (opens, PC link up, keys present, self-tests pass) within 2 minutes, or the Guardian reinstalls the previous build. Android blocks downgrades, so the PC always keeps a "previous" build re-stamped with a higher `versionCode`. In Stage 3 use Android's RollbackManager (`setEnableRollback`) instead. |
| **S: system and kernel** (Stage 3 only) | The OS: Orion SystemUI, framework patches, SELinux, kernel config, init tuning, eBPF | Build box: fork tree at `orion-self/<id>`, `m` build, boot test on the lab phone (or Cuttlefish virtual device), CTS-lite smoke tests, Orion's own on-device test suite | **Ghost:** flash the lab phone first. **Approve:** fingerprint on the main phone signs the OTA hash. **Install:** signed A/B OTA from your OTA server via the Updater/update_engine. | A/B: the new slot is written while the old one keeps running. If it fails to boot, or isn't marked successful after several tries, the bootloader falls back to the old slot ([AOSP A/B](https://source.android.com/docs/core/ota/ab)). Your fork delays "mark successful" until the Orion health check passes. |

**Protected core on the phone (never self-changed, never automatic):**
- `Keys.kt`, the approval screen and the verdict signer, pairing and attestation code;
- the Guardian app, the installer/updater code, `privapp-permissions-orion.xml`, Orion SELinux policy, the boot
  key and OTA certificates in the image;
- the self-change gate and its levels; the Freeze path.

These paths are added to `apps/daemon/protected_core.json` (it can only add protection), so the PC's gate
refuses them in levels U/A/S. Changing them needs a boardroom decision signed by **both** the phone fingerprint
and the boardroom key. It is then installed by hand (USB/ADB for the APK, or a separately approved OTA), never by
the self-change flow.

**Key separation (important):** the APK release key and the OTA/AVB keys **must not sit where the self-changing
daemon can use them.** Put them in a separate "release signer" (protected core on the PC). It holds the keys
encrypted (ideally on a hardware token such as a YubiKey/HSM for the OTA key). It only signs an artifact whose
SHA-256 carries a valid phone-fingerprint approval packet. A compromised daemon then still can't ship an update
without your finger. Back up the keys offline twice: losing them in Stage 3 means unlocking and wiping to
re-key (avbroot warns the same).

**What the phone may change by itself without asking:** nothing in A or S. Level U changes marked "cosmetic"
(theme, layout) may follow the desktop's `ui_code` gate level if you raise it. The default is ask.

---

## 6. "Instantly connects with my computer"

**Design: one persistent, mutually authenticated link, plus a wake-up channel, plus fast rediscovery.**

| Piece | Recommendation | Why |
|---|---|---|
| Main link | Phone holds one long-lived **WebSocket (or HTTP/2 stream) over TLS** to the PC daemon, with the PC's key pinned (as today) and every request signed with READ/SIGN. It lives in a `connectedDevice` foreground service. | Sub-second push both ways (approvals appear instantly, agent status streams, inbox mirror). Replaces the 15-minute `PollJob`. |
| Wake-up push (when the socket is down) | **UnifiedPush with a self-hosted ntfy server on the PC** (or a small VPS when the PC sleeps). The ntfy Android app is the distributor; WebSocket mode delivers instantly ([etke.cc ntfy notes](https://etke.cc/help/extras/ntfy/)). | No Google FCM, so it works on GrapheneOS without Play. One shared connection for all UnifiedPush apps. The PC sends "approval waiting" and the phone reconnects. Payloads carry only an ID, never content. |
| Away from home | **Tailscale** (or **headscale** self-hosted on the PC/VPS; the Android client supports a custom control server, [Tailscale docs](https://www.tailscale.com/docs/how-to/set-up-custom-control-server?tab=android)). Device Owner sets it as **always-on VPN**. | The same PC address everywhere. Encrypted (WireGuard). Note Android allows one VPN at a time. Alternative: embed userspace Tailscale (`tsnet`) inside Orion so it doesn't use the VPN slot (more work). |
| Rediscovery at home | mDNS `_orion._tcp` (*built*) + a `ConnectivityManager` network callback ("home Wi-Fi is back, reconnect now") + **Companion Device Manager Bluetooth LE presence** (the PC advertises a BLE beacon; Android wakes Orion's service when the PC is in range) | Reconnects in about 1 second instead of waiting for a timer. **Android 17:** apps targeting SDK 37 need the runtime `ACCESS_LOCAL_NETWORK` permission for mDNS and LAN TCP/UDP ([Android docs](https://developer.android.com/privacy-and-security/local-network-permission)). Request it in pairing, before the targetSdk bump. |
| Files | LocalSend protocol v2 (*built* on the PC): HTTPS on 53317, multicast discovery 224.0.0.167 ([LocalSend protocol](https://github.com/localsend/protocol)). Next step: implement the LocalSend sender inside Orion so the separate app isn't needed. | Proven and fast; already understood by the PC side. |
| Clipboard, notifications, media, remote input | Borrow **KDE Connect's** ideas: mutual TLS with trust-on-first-use pinning, a short code compared on both screens, and plugins for notifications, clipboard, battery, media and remote input ([rust-connect case study](https://georgelarson.me/rust-connect)). Orion's pairing already does the TOFU + five words. | A clean plugin list for "phone and PC as one device". |
| PC controls the phone | Opt-in: accessibility "hands" (gestures, typing) streamed over the link; for full screen mirroring, **scrcpy** over wireless debugging from the PC | PC-side Orion can run phone macros; vision reads the phone's UI tree first (cheap and private), screenshots second. |
| Nearby / Wi-Fi Direct | Not needed day to day. Optional for a no-router "field mode" (Wi-Fi Aware / Direct) later. | Complexity without much gain at home. |

**What a privileged system app (Stage 3) adds to connectivity:** `persistent` process (never killed, no
battery-optimization games), clipboard sync in the background, its own VPN/network rules without user prompts,
reading SMS and calls natively, starting the hot word at boot, and the lock screen showing PC state. Device Owner
(Stage 1.5/2) gets most of the reliability: an always-on VPN, a battery exemption and no user-removable
restrictions. Only clipboard sync and the lock screen truly need Stage 3.

---

## 7. Recommendation by phone model (decision tree)

```
What phone is it?
|
+-- Google Pixel 8 / 8 Pro / 8a / 9 / 9 Pro / 9a / 10 / 10 Pro / 10a / Fold 9/10   (bought unlocked, not Verizon etc.)
|     Stage 1.5 now (Device Owner; needs a reset)  ->  Stage 2 GrapheneOS + Device Owner  ->  Stage 3 own build later.
|     Tip: do Stage 1.5's reset and Stage 2's install together: GrapheneOS's first setup is the
|     perfect moment to make Orion the Device Owner.
|
+-- Pixel 6a / 7 / 7 Pro / 7a / Fold (1st gen)
|     Works, but support ends 2027-2028. Fine for Stage 2 short-term, and an ideal **Stage 3 lab phone**.
|     For your daily approval phone, plan to move to a Pixel 9a / 10 / 10a.
|
+-- Pixel 6 / 6 Pro   -> support ended Oct 2026. Stage 1 / 1.5 only. Don't invest in Stage 2/3 on it.
|
+-- Pixel 11          -> Stage 1.5 now. Wait for GrapheneOS to confirm support before Stage 2/3
|                        (custom boot keys also need a firmware update on this model).
|
+-- Carrier-locked Pixel (OEM unlocking greyed out) -> Stage 1.5 only.
|
+-- Samsung Galaxy    -> Stage 1.5 only (One UI 8 removed bootloader unlocking; rooting trips Knox for good).
|                        Pluses: DeX desktop mode, Linux terminal on S26 / One UI 8.5. Device Owner works.
|
+-- OnePlus / Xiaomi / Nothing / Motorola / others
                      -> Stage 1.5. Unlocking and LineageOS are possible on many, but that means an unlocked
                         phone = READ-only pairing under the section 4 policy. A Motorola phone with GrapheneOS
                         is expected in 2027: a future Stage 2/3 target.
```

---

## 8. Staged roadmap (starting from what's built)

| Stage | Contents | Effort | Risk | Exit test |
|---|---|---|---|---|
| **1 (built)** | Launcher, assistant, inbox, tiles, fingerprint approvals, missions, pairing, Pocket Orion | done | none | — |
| **1.5 Orion Max APK** | (1) `OrionAdmin` DeviceAdminReceiver + Device Owner provisioning screen, policies (sensor-grant opt-out, always-on VPN, restrictions you choose, update policy) and a big "Remove Orion admin" button behind your fingerprint. (2) Key attestation at pairing + the READ-only policy for unlocked phones. (3) Persistent link service (`connectedDevice` FGS) + UnifiedPush client + ntfy on the PC + Tailscale/headscale + network/BLE wake-ups; retire `PollJob`. (4) Guardian app + silent self-update + level U and level A self-change (PC side: extend `self_program.py` with an `android` target: Gradle in the worktree, emulator tests, ghost package, APK hash approval, release signer). (5) Opt-in accessibility "hands" + volume-key hotkeys + phone macros in the Grimoire. (6) targetSdk 34 to 36 now, 37 with `ACCESS_LOCAL_NETWORK`. (7) Register a limited-distribution developer account for the signing key. (8) Pocket Orion hardening (change the `droid` password, iptables allow only 9121). | 2 to 4 weeks | Low. The Device Owner step wipes the phone if it has accounts: back up. | Approval arrives on the phone within 1 s of being queued, on home Wi-Fi and on mobile data; a self-change ships an APK and a bad one rolls back by itself; an unlocked test phone can't approve. |
| **2 Hardened phone** | Pixel 8+ on official GrapheneOS, relocked; Orion as Device Owner at first setup; sandboxed Play only if your banks need it; Pocket Orion in the terminal; PC pins the GrapheneOS boot key in attestation | 1 evening + 1 week of living with it | Low (the official installer is well tested). You lose Google Wallet tap-to-pay and some bank apps. | All Stage 1.5 tests pass on GrapheneOS; Auditor shows the device as genuine. |
| **3a Build box + plain own-signed build** | Linux box (Ubuntu LTS, 64 GB+ RAM, 1 to 2 TB NVMe, 16+ cores). Sync the GrapheneOS tree for the **lab** Pixel, generate **your** keys (offline backup), build, sign, flash with `avb_custom_key`, relock, check attestation shows `SELF_SIGNED` + your key; host your own update server; prove one OTA and one forced rollback | 1 to 2 weeks | Medium (lab phone only; a mistake means unlock and wipe, not a brick, as long as OEM unlocking stays on until it's proven) | Lab phone updates from your server; a deliberately broken OTA falls back to the old slot. |
| **3b Orion OS Mobile** | Orion + Guardian as privileged system apps with an allowlist; Orion SystemUI (lock screen, status chips); clipboard sync; RollbackManager; level S self-change (PC builds OTAs in a copy, the lab phone runs the ghost, the main phone's fingerprint approves); kernel/init tuning; automation script for each GrapheneOS release (fetch tag, rebase Orion patches, build, test on the lab phone, sign, publish) | 3 to 6 weeks, then about 1 to 2 days a month | Medium: upkeep, key custody | One month of daily use with on-time security updates (within 72 h of each GrapheneOS release). |
| **3c Move the daily phone** | Flash your build on the daily Pixel (wipes it), relock, re-pair (the PC pins your key) | 1 evening | Medium (wipe; back up) | — |
| Not planned | Root on the approval phone; Samsung custom ROMs | — | High | — |

---

## 9. How to put it on your phone

### 9a. Stage 1.5: Orion as Device Owner (recommended first step)

You need: the PC with Android Studio (or just "Android SDK Platform-Tools" for `adb`), a USB cable, and the new
Orion APK (Stage 1.5 build). The Device Owner part requires a phone with **no accounts**, which in practice means
right after a reset.

1. **Back up** photos, chats (WhatsApp/Signal backups), and 2-factor codes (export them from your authenticator
   app). Write down which banking apps you use.
2. **Factory reset** the phone (Settings, System, Reset options, Erase all data). On first start, go through
   setup **but skip adding a Google account** (or any account). Connect to Wi-Fi.
3. **Turn on Developer options:** Settings, About phone, tap "Build number" 7 times. Then Settings, System,
   Developer options, turn on **USB debugging**.
4. **Install Orion:** plug in the phone, accept the "Allow USB debugging?" prompt, then on the PC:
   `adb install Orion-Mobile.apk` (or double-click **Build Orion APK.cmd**, which builds and installs it).
5. **Make Orion the Device Owner:**
   `adb shell dpm set-device-owner com.orion.mobile/.OrionAdmin`
   It should answer "Success". If it says there are accounts on the device, an account was added: remove it in
   Settings, Accounts (or reset again).
6. **Optional one-time grants** (also work without Device Owner):
   `adb shell pm grant com.orion.mobile android.permission.WRITE_SECURE_SETTINGS`
7. **Open Orion.** Its setup screen now shows "Orion admin: on". Pair with the PC (QR code and five words, as
   before). The PC checks the attestation and shows "Phone keys: in the secure chip, phone locked, Android
   verified".
8. **Now add your Google account** and reinstall your apps as normal (Device Owner doesn't stop that). Open each
   banking app once to check it works.
9. In Orion's Phone setup, turn on the parts you want: Home, Assistant, Inbox, Tailscale (always-on VPN),
   "Orion hands" (only if you want macros; Android first asks you to allow restricted settings in App info).
10. Turn **USB debugging off** again (Orion can enforce this). Orion updates itself from now on, each update after
    your fingerprint.

**Undo:** Orion > Settings > "Remove Orion admin" (fingerprint) turns Device Owner off; then uninstall. A factory
reset always removes it too. Orion never blocks factory reset.

### 9b. Stage 2: official GrapheneOS + Orion

Follow the official web installer: <https://grapheneos.org/install/web> (it is the source of truth; summary in
`ORION_PHONE_GUIDE.md`). Steps: turn on OEM unlocking, boot to fastboot, unlock (wipes), flash, **relock**
(wipes), first boot. **Then, during first setup, add no accounts**, and do steps 3 to 7 of 9a to install Orion and
make it Device Owner. Install sandboxed Google Play from GrapheneOS's App Store only if your apps need it.
Linux terminal: Developer options, Linux development environment, then install Pocket Orion (existing guide). Keep
"OEM unlocking" off after setup (the last setup screen offers this).

### 9c. Stage 3: your own Orion OS Mobile build (lab Pixel first)

Plain words first, then the tools. **Follow GrapheneOS's own build guide (<https://grapheneos.org/build>) for
exact commands; they change between releases.** (Note: that page and the FAQ couldn't be fetched during this
research. Read them directly before starting.)

1. **Get a build box:** a Linux PC (Ubuntu LTS), 64 GB RAM or more, 1 to 2 TB fast SSD, 16+ CPU cores. Install
   the AOSP build packages and `repo`.
2. **Download the source** for the GrapheneOS release matching your lab phone (`repo init` on the GrapheneOS
   manifest at the release tag, then `repo sync`). About 250 GB of source plus 150 GB per build.
3. **Make your keys** (once, on an offline or encrypted disk): the release, platform, shared, media, networkstack
   and other APK-signing keys, the **AVB key** (verified boot) and the **OTA key**. GrapheneOS's guide includes
   scripts for this and for encrypting them with a passphrase. Make **two offline backups**. Losing them means
   unlocking and wiping to re-key; leaking them means someone else can sign your phone's OS.
4. **Add Orion:** put `com.orion.mobile` and `com.orion.guardian` into the tree as prebuilt (or source-built)
   privileged apps, with `privapp-permissions-orion.xml`, default-role configuration (home, assistant, SMS if
   wanted), and later the SystemUI changes. Point the system Updater at **your** update server URL (the build guide
   explains where).
5. **Build** for your device (`lunch <device>-cur-user` style target, then the build command; first build several
   hours), then **sign** the target files with your keys and produce the factory images and OTA zip using the
   guide's release script.
6. **Flash the lab phone:** boot to fastboot, `fastboot flashing unlock` (wipes), flash the factory images, then
   install your key: `fastboot flash avb_custom_key avb_pkmd.bin` (the public part of your AVB key), then
   `fastboot flashing lock` (wipes). On boot the phone shows a yellow notice with your key's fingerprint. That's
   expected and shows it's verifying **your** OS. Compare the fingerprint with your key's hash.
7. **Check it:** pair with the PC. The PC must see `deviceLocked=true`, `SELF_SIGNED`, and `verifiedBootKey` =
   your key hash. Test an OTA from your server, and a deliberately broken OTA (it must fall back).
8. **Keep OEM unlocking on until everything is proven** (it's your escape hatch on the lab phone), then turn it off.
9. **Automate releases:** a script on the build box watches for GrapheneOS release tags, rebases the Orion patches,
   builds, runs tests on the lab phone, and asks your fingerprint. The release signer then signs and publishes the
   OTA. Your daily phone updates itself the same way the desktop does.
10. **Move the daily phone** (9c steps 6 to 7 on it) only after a month of clean updates on the lab phone.

---

## 10. Feature mapping: every Orion feature on the phone

Columns: **1** = built APK; **1.5** = Max APK (Device Owner); **2** = GrapheneOS + Device Owner (same as 1.5
unless noted); **3** = Orion OS Mobile; **Root** = for comparison only (not recommended).

| Orion feature | 1 (built) | 1.5 / 2 | 3 (Orion OS) | Root (comparison) |
|---|---|---|---|---|
| Living black hole HUD | Launcher + live wallpaper | Same | + **lock screen**, status bar, boot animation | Lock screen via LSPosed hooks (fragile) |
| Chat with Orion | PC, or Pocket Orion when the PC is off | + streaming over the persistent link | Same | Same |
| Voice: hold-to-talk, assistant button | Built | Same | Same | Same |
| Wake word ("Orion") | Software hot word in an FGS (opt-in, green dot) | Same; can't auto-start at boot (Android 15 rule) | Platform hotword path, can start at boot, lower battery, still visibly indicated | Same as 1.5 |
| TTS replies | Built | Same | Same | Same |
| Unified inbox (Discord, Telegram, WhatsApp, Messenger, Signal, IG, SMS) | Notification listener; send on tap | + SMS via default SMS role; digest + VIP satellites on home | + native SMS/call log, inbox as the notification shade | Can read app databases (privacy risk) |
| Reply drafts in your style | PC drafts | Same + Pocket Orion offline | Same | Same |
| Calls | Screening role | + default dialer role (optional) | + live captions to Orion, call notes (on-device) | Same as 3, hacky |
| Approvals (fingerprint) | SIGN key in StrongBox | + **attestation verified by the PC**; instant push | + trusted approval layer in SystemUI | **Untrustworthy** (READ-only by policy) |
| Freeze / kill switch | Tile + fingerprint unfreeze | + freeze also suspends Orion's own powers (hands, VPN rules) via Device Owner | + freeze at system level | Same |
| Missions / agent swarm | Built | + live robot updates via push | Same | Same |
| Grimoire macros / voice-triggered scripts | Run on the PC from the phone | + **phone macros** (accessibility hands, opt-in); PC can run phone macros | + macros using system APIs directly (no accessibility needed) | Via `su` shell |
| Hotkeys | QS tiles | + volume-key combos (accessibility), PC-to-phone hotkeys | + hardware key remaps in the framework | Key remap modules |
| Watch and learn / Skill recorder | PC only | Phone skill recorder from the UI tree (skips password fields, like `capture_privacy.py`) | + system-level recording with the same privacy rules in protected core | Same, no guarantees |
| Vision: screen understanding | — | Accessibility tree + `takeScreenshot` (opt-in, chip visible) | + system screenshot API, no accessibility | Any |
| Vision: camera presence / gestures | — | Off by default; opt-in foreground camera only while the Orion screen is open | Same, plus system camera-in-use indicator in Orion's bar | Same |
| PC access (control the PC from the phone) | Missions, agents, hot mic tile | + remote desktop over Tailscale, desktop_access routes | Same | Same |
| Phone access from the PC | — | Accessibility hands + scrcpy (wireless debugging) | + native remote input and screen sharing service | Same |
| Clipboard alchemy / sync | Share sheet | Phone to PC on share; PC to phone push | **Background clipboard sync** (privileged permission) | Same |
| Files to/from PC | LocalSend app | LocalSend built into Orion | Same | Same |
| Video Scout | Tile + share | Same | Same | Same |
| Knowledge vault / memory | Pocket Orion vault | Same | Same | Same |
| Battle Stations | — | "Phone station": Device Owner suspends distracting apps, sets Do Not Disturb, opens project apps | Same + system focus modes | Same |
| Health (focus arc, hydration, wind-down) | — | Home widgets, Device Owner app suspension for wind-down | + lock screen arc | Same |
| Life logistics / runway | — | Read-only glass card from the PC | Same | Same |
| Creative asset pills | Share to PC | Same | Same | Same |
| Self-programming | Desktop only | **Level U + level A** (APK, Guardian rollback) | + **level S** (signed A/B OTA, kernel/system) | Unchecked runtime changes |
| Protected core | Desktop | Phone paths added; Guardian; release signer | + boot key, privapp allowlist, SELinux in the core | None |
| Auto-update / self-running | Manual APK | Silent signed self-updates | Signed OTAs from your PC | Manual |
| Privacy dashboard | PC | + Device Owner security/network logs from the phone | + eBPF per-app network accounting | Possible |
| Desktop mode | Built | Same | + Orion desktop as the system desktop shell | Same |
| Pocket Orion (PC off) | Built | + hardened setup | + could ship as a built-in VM image | Same |

---

## 11. Safe vs risky (plain list)

**Safe (reversible, nothing unlocked):** Stage 1 and 1.5; Device Owner (removable; factory reset always works);
one-time ADB grants; Shizuku; the Linux terminal; Tailscale/ntfy.

**Wipes the phone (back up first) but not dangerous:** setting Device Owner on a used phone; unlocking and
relocking a Pixel; installing GrapheneOS; flashing your own build.

**Loses things:**
- **Banking apps / Google Wallet:** fine on stock (1.5). On GrapheneOS and your own build, **Google Wallet
  tap-to-pay doesn't work** and banks that demand Google certification fail. Many banks do work with sandboxed
  Play. **Check your exact banks before Stage 2** (GrapheneOS keeps a community list of banking app
  compatibility). Your own build also loses apps that allow-list GrapheneOS's official keys.
- **Root:** fails DEVICE/STRONG integrity, Knox fuse on Samsung, and breaks Orion's approval trust.

**Can brick, or effectively brick:**
- Turning "OEM unlocking" off on a phone whose boot images don't match the locked key (rooted-and-locked setups;
  avbroot's warning).
- Losing your Stage 3 keys (not a hardware brick, but you must unlock and wipe to re-key).
- Interrupting a flash. Use a direct USB port and a good cable, and follow the installer exactly.

**Breaks the fingerprint trust:** root, unlocked bootloaders, sideloaded "Orion" builds not signed by your release
signer, and keeping release keys where the self-changing daemon can use them.

---

## 12. Open questions for the owner

1. **Which phone (model, and bought unlocked or from a carrier)?** This decides the branch in section 7.
2. **Which banking/payment apps must keep working?** Is Google Wallet tap-to-pay a must? (If yes: stay stock with
   Stage 1.5, or pay with a watch or card.)
3. Is there a Linux machine with 64 GB+ RAM for Stage 3, or should we budget one (or a cloud build VM; keys must
   stay off it)?
4. OK to buy a second, older Pixel (7/7a) as the Stage 3 lab phone?
5. Should the phone be allowed to apply "cosmetic" level-U changes without asking (the default is ask)?

---

## Sources (accessed 2026-11-07; publication dates where known)

- GrapheneOS Pixel 10 stable builds, Jan 2026: <https://piunikaweb.com/2026/01/21/grapheneos-finally-rolls-out-stable-builds-for-pixel-10-series/>
- GrapheneOS device support end dates, 2026-09-19: <https://swappa.com/blog/grapheneos-guide-which-pixel-to-buy/>
- GrapheneOS Android 17 port, June 2026: <https://discuss.grapheneos.org/d/36469-grapheneos-has-been-ported-to-android-17-and-official-releases-are-coming-soon>
- Pixel 11 and GrapheneOS, 2026-09-04: <https://www.androidheadlines.com/2026/09/the-pixel-11-series-might-actually-end-up-with-grapheneos-support.html>
- Motorola and GrapheneOS, 2026-03-01 and 2026-08-24: <https://9to5google.com/2026/03/01/motorola-confirms-grapheneos-partnership-for-a-future-smartphone-porting-features/>, <https://9to5google.com/2026/08/24/motorola-razr-fold-ultra-grapheneos-support-coming/>
- AOSP releases twice a year, 2026-01-07: <https://piunikaweb.com/2026/01/07/android-aosp-source-code-q2-and-q4-pixel-monthly-security-patches-unchanged/>
- Android 17 stable for Pixels, 2026-06-16/17: <https://www.gsmarena.com/android_17_stable_update_is_rolling_out_to_pixel_phones-news-73308.php>; features: <https://techcabal.com/2026/06/16/android-17-features>
- Android developer verification timeline (2026): <https://www.androidauthority.com/android-sideloading-changes-timeline-3679204/>; Google help: <https://support.google.com/android-developer-console/answer/16561738>; 24-hour flow, March 2026: <https://thehackernews.com/2026/03/google-adds-24-hour-wait-for-unverified.html>
- Root frameworks, Play Integrity status, verified 2026-10-14: <https://awesome-android-root.pages.dev/rooting-guides/root-framework-comparison>
- Play Integrity hardware-backed verdicts (Dec 2024 / May 2025): <https://www.androidauthority.com/google-play-integrity-hardware-attestation-3561592/>
- Samsung One UI 8 removes bootloader unlock, July 2025: <https://www.sammobile.com/news/say-goodbye-to-your-custom-roms-as-one-ui-8-kills-bootloader-unlock/>
- Samsung Linux terminal (S26 / One UI 8.5), 2026: <https://www.androidauthority.com/samsung-phones-linux-terminal-one-ui-8-5-3652503/>
- LineageOS 23, Oct 2025: <https://www.androidcentral.com/apps-software/android-os/lineageos-23-brings-android-16-to-over-100-devices-despite-googles-holdups>
- Attestation policy (RootOfTrust, SELF_SIGNED, pinned keys): <https://a-sit-plus.github.io/warden-supreme/technical/android>
- avbroot (custom AVB key, relock, warnings): <https://www.github.com/chenxiaolong/avbroot>
- AOSP build requirements: <https://source.android.com/docs/setup/requirements>
- AOSP A/B updates and rollback: <https://source.android.com/docs/core/ota/ab>
- Privileged permission allowlist: <https://source.android.com/docs/core/permissions/perms-allowlist>
- Device Owner runtime/sensor permission grants: <https://bayton.org/android/android-enterprise-faq/managing-runtime-permissions/>
- Silent self-update conditions (Android 12+): <https://gitlab.com/fdroid/fdroidclient/-/issues/2380>
- Shizuku: <https://github.com/RikkaApps/Shizuku>
- Android 13/15 restricted settings for sideloaded apps, 2024: <https://androidauthority.com/android-15-restricted-settings-sideloading-3481098>
- Android 15 foreground service type changes: <https://developer.android.com/about/versions/15/changes/foreground-service-types>
- Android 17 Advanced Protection accessibility lock, 2026-10-02: <https://thehackernews.com/2026/10/android-17-advanced-protection-locks.html>
- Linux terminal / AVF field guide, retested 2026-05-26: <https://cdn.jsdelivr.net/gh/ferrumclaudepilgrim/claude-code-android@main/docs/avf-guide.md>
- Local network permission (Android 17): <https://developer.android.com/privacy-and-security/local-network-permission>
- Companion Device Manager: <https://developer.android.com/guide/topics/connectivity/companion-device-pairing>
- ntfy / UnifiedPush: <https://etke.cc/help/extras/ntfy/>
- Tailscale custom control server (headscale): <https://www.tailscale.com/docs/how-to/set-up-custom-control-server?tab=android>
- LocalSend protocol: <https://github.com/localsend/protocol>
- KDE Connect protocol notes (rust-connect): <https://georgelarson.me/rust-connect>

**Couldn't be fetched during this research (read them directly before Stage 2/3):** <https://grapheneos.org/faq>,
<https://grapheneos.org/build> (permission request timed out), the source.android.com verified-boot device-state
page (robots-blocked), and the developer.android.com FGS background-start restrictions page (robots-blocked).
Claims about GrapheneOS build steps, banking compatibility and Device Owner support on GrapheneOS come from
secondary sources and general knowledge and are marked as things to verify on the lab phone.
