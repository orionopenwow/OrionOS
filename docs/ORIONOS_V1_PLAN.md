# OrionOS 1.0 — full feature plan (ship from a USB drive)

**Goal:** a public release people can put on a USB stick, boot on almost any PC, and be greeted by Orion. It
should look and feel finished. Nothing needs installing to try it, your data stays encrypted on the stick, and
installing to the PC is one button.

**Base (decided 2026-10-15): Ubuntu 24.04.** It keeps the existing provision scripts, Ubuntu's signed
shim and kernel, and NVIDIA support. The image is built with a scripted casper/squashfs pipeline in CI.
Fedora Atomic/bootc was considered (it gives image CI, atomic updates and NVIDIA images for free), but it was
turned down so the tested scripts can be kept.

**Build less, reuse more:** wherever a maintained open-source project already does the job, Orion uses it and
adds only the Orion look and glue. Each table below has a **Reuse** column; "—" means it's Orion's own work.

**After 1.0:** the next feature pass (Skill Recorder, the stars-to-black-hole login, the 3D black hole, the agent
swarm, Mission Control, webcam gestures, live self-reprogramming, and the Benilla game pipeline) is planned in
`ORIONOS_AFTER_1_0_PLAN.md`.

**What's parked:** Stardust and all crypto/treasury work (`STARDUST_PLAN.md`, V3 section 3) wait until 1.0 has
shipped. In 1.0, AI models are local (Ollama) or use your own API key.

**Starting point:** OrionOS v0.1, two VirtualBox VMs (`os/README.md`). The lab boots into the HUD on Sway, with:
- the boardroom and the autonomy learner;
- phone pairing (APK) and lab updates;
- the theme, Files and a Settings menu;
- the setup agent: it installs dev tools, Firefox, VS Code and Discord and clones your repos, each after one
  signed approval.

The Orion desktop app has the Feature Bible, V3 personal features, phone layout and the guest demo.

### Where it stands (2026-10-15)

| | Built | Partial | New |
|---|---|---|---|
| Whole plan (78 items) | 28 | 19 | 31 |
| 1.0 "must ship" items (67) | 26 | 18 | 23 |

"Built" mostly means built in the Windows desktop app; on OrionOS some of it still needs testing on real
hardware (mouse clicks, GPU rendering, media controls). The app store and coding-agent installs moved from New to
Partial because the setup agent already installs apps after one approval. With the reuse below, the remaining
new and unfinished 1.0 work comes down to roughly 8 pieces that are Orion's own work:
- Guide Mode;
- the first-run flow;
- the privacy dashboard;
- the Settings app front end;
- the app store front end;
- cosmic voice tuning;
- the website and video;
- the outside security review.

---

> **Completing the OS:** `ORIONOS_COMPLETE_PLAN.md` covers the everyday basics still missing (browser, file
> viewers, clock, Settings, app store, snapshots…), the optional Craft creative suite, and Orion keeping itself
> healthy and up to date within permission levels you set.

## UI reference video (the target look and feel for 1.0)

> **Art update (decided 2026-10-16):** the detailed visual plan now lives in
> [`ORION_ART_UPDATE_PLAN.md`](ORION_ART_UPDATE_PLAN.md). It covers the living black-hole shader, the 6-second boot
> sequence, frosted-glass windows (SwayFX), the logo, the fonts (Unbounded / Figtree / Martian Mono) and the
> "Event Horizon" terminal theme. Where it differs from the notes below, the art update wins. Its render is
> `07-art-update-windows.png`.

Saved in `docs/assets/ui-reference/`: the 10-second clip `orion-ui-reference.mp4`, a `contact-sheet.jpg` and four
key frames. It's AI-generated, so the text in it is gibberish. Use it for **look, layout and motion**, not for
wording.

**What it shows**
1. **Idle desktop (`01-idle-desktop.jpg`):**
   - Deep-space wallpaper with a soft Milky Way band and scattered stars.
   - The violet black hole sits in the centre: a dark core, a glowing swirl, and a thin, tilted, Saturn-like
     accretion ring in lavender.
   - Nothing else on screen except two small glass elements: the dock and the chat widget.
2. **Dock, bottom-left:** five small square glass buttons with soft edges:
   - windows/apps;
   - terminal;
   - links;
   - a globe (browser / Orion);
   - a grid (widgets).

   Later in the clip it slides to the **bottom centre** and shows a thumbnail of a minimised window.
3. **Chat widget, bottom-right:**
   - **Collapsed:** a small glass card with a chat icon and an unread badge, a message field and a mic button.
   - **While typing:** the mic becomes a round violet send button.
   - **Expanded upward:** a tall chat panel with Orion's glowing orb logo at the top. Your messages are violet
     bubbles on the right; Orion's replies are dark grey bubbles on the left, with the orb as its avatar. A
     timestamp sits under each reply, and "Type a message" plus send is at the bottom.
4. **Windows (`02-window-and-chat.jpg`):** dark, **translucent glass** windows with rounded corners, tabs and
   macOS-style traffic-light buttons.
   - The black hole and stars stay visible **through** the window.
   - The example is a file manager (sidebar, light-blue folder icons) next to a code pane with teal/cyan code,
     which Orion writes live while you chat.
5. **Gravity minimise (`03-window-falling-in.jpg` → `04-minimized-to-dock.jpg`):**
   - Stars start streaking toward the core, like warp speed.
   - The window's edges ripple and bend.
   - The window falls into the black hole and disappears, then reappears as a thumbnail in the dock. A file
     dropped earlier sits as an icon on the desktop.

**How it maps to the plan**
| In the video | Where it lives |
|---|---|
| Space wallpaper + centred black hole with a tilted ring | HUD core today; the 3D version is Black hole 2.0 (after-1.0 plan, §3). For 1.0, restyle today's core toward this look: a thinner, tilted ring, lavender tones, a calmer swirl |
| Glass dock (bottom-left → centre) | **New for 1.0:** an Orion dock replacing the bare launcher (part of B. Shell). It holds pinned apps and minimised windows |
| Collapsible chat card → expanding chat panel | **Restyle for 1.0:** today's Earth button + panel becomes this bottom-right card that grows upward (keep the tabs inside the expanded panel) |
| Translucent glass windows showing the black hole through them | Compositor blur/transparency (SwayFX now; Hyprland if switched) + the GTK/Qt glass theme |
| Orion typing code into an open editor while you chat | Mission Control / coding-agent work shown live (after-1.0 Phase 3); for 1.0, Orion's chat can open the file it's editing |
| Window warps and falls into the black hole to minimise | **Gravity minimise** animation. A simple version (window shrinks and spirals into the core, then lands in the dock) is a 1.0 polish item. The true warping of the window is Gravity mode (after-1.0, needs a compositor with shaders) |
| Star streaks toward the core during the effect | Part of the core's animation states (shared with the login swirl) |

**Rule for designers and agents:** when a 1.0 screen's look is unclear, match this video first, then the existing
HUD theme tokens (`os/ARCHITECTURE.md` §4).

### Boot sequence references

Two more stills, also AI-generated (so the text in them is garbled): `05-boot-hardware-check.png` and
`06-boot-logo.png`.

1. **Hardware check — dropped (decision: keep the boot clean).** Kept here as a reference only. The art update's
   boot sequence shows no check text; a failed check shows a plain message instead.
   - A dark navy panel with a thin lavender border.
   - The **ORION** wordmark at the top, with a constellation (connected glowing star points) drawn over the
     letters.
   - Under it, a short boot checklist in a monospace font, in teal and lavender lines: checking system, memory,
     peripherals, configuration, kernel.
   - **In OrionOS:** show the real early-boot steps (memory, storage, graphics, network, vault found, …) in the
     Plymouth theme, a line at a time, for the first second or two. Then move to the logo screen. The text comes
     from real checks (the first-boot hardware check), never invented. It's hidden after the first boot unless
     something fails.
2. **Boot logo:**
   - Deep space with a soft Milky Way band.
   - The violet black hole with its tilted ring in the centre.
   - **ORION OS** in a spaced, rounded, light-lavender typeface, with a thin circular spinner under it.
   - **Today's Plymouth theme** (`os/image/files/usr/share/plymouth/themes/orion/`) already has the turning
     black hole and the title. To match this still, it needs:
     - the Milky Way background image;
     - the spaced "ORION OS" wordmark instead of plain bold "OrionOS";
     - the ring spinner.
   - The vault passphrase prompt stays on this screen, under the spinner.

Then the starry login (Phase 1, already built) swirls into the HUD's black hole, so boot → login → desktop is one
continuous sequence.

## 1. How 1.0 boots: the USB model

**The system is an image, your stuff is a vault.**

```
USB stick (32 GB minimum, 64 GB+ recommended, USB 3)
├── EFI partition        bootloader (Ubuntu's signed shim + GRUB) → works with Secure Boot on
├── System A (read-only) OrionOS image (casper squashfs, ~4 GB)
├── System B (read-only) next update downloads here; switch on reboot, roll back if it fails
└── OrionVault           your home folder, Orion's memory, settings, models (LUKS-encrypted, fills the rest)
```

A/B on Ubuntu:
- **The slots:** two casper directories. GRUB picks one with `live-media-path`, and a boot counter in the GRUB
  environment handles rollback. This is the same pattern RAUC's GRUB backend uses.
- **The vault:** a small initramfs hook unlocks the LUKS partition, with the passphrase prompt shown by the Orion
  Plymouth theme.

| Mode | What happens | Saved? |
|---|---|---|
| **Try Orion** | Boots the image, runs the tour and the demo data | Nothing, wiped at shutdown |
| **Portable** (default after first run) | Unlock your vault with a passphrase at boot; everything you do is saved on the stick | Yes, encrypted |
| **Install to this PC** | One button in Orion Settings: alongside Windows (dual boot) or replace it; copies the image and your vault | Yes |
| **Run on Windows** | `Start OrionOS.cmd` (creates and starts the VMs), kept for people who won't reboot | Yes |

Why this layout:
- A read-only system can't be broken by an app or an agent.
- Updates are a whole-image swap with automatic rollback.
- Losing the stick doesn't leak your data.
- Fewer writes means a longer-lived USB stick.

**Making the stick:**
- **Orion USB Maker** for Windows and macOS: a fork of Raspberry Pi Imager (open licence; it already does
  download → check → write, with a custom image list). Orion adds the signature check and vault creation, plus a
  `.cmd` version.
- Plain `.iso` and `.img` downloads for Rufus, balenaEtcher and Ventoy users ("Try" mode only on Ventoy).
- Two image flavours, as Pop!_OS does: **Standard** (Intel/AMD graphics) and **NVIDIA** (with the proprietary
  driver).

## 2. Full feature set

Status: **Built** works today · **Partial** exists but needs finishing for 1.0 · **New** not started.
Priority: **1.0** must ship · **1.x** soon after.

### A. Boot, system and hardware
| Feature | Status | Priority | Reuse | Notes |
|---|---|---|---|---|
| Image build pipeline (Ubuntu 24.04 → squashfs → hybrid ISO/IMG) in GitHub Actions | New | 1.0 | Pop!_OS's open ISO build scripts (Ubuntu + casper, Standard and NVIDIA) as the template | Feeds the existing provision scripts; reproducible, signed releases |
| Secure Boot | New | 1.0 | Ubuntu's signed shim and kernel | No custom kernel in 1.0 |
| A/B system slots, update with rollback | New | 1.0 | GRUB env boot counting (RAUC's GRUB pattern); RAUC itself for installed systems | Replaces `orion-update` for USB/installed systems |
| Encrypted vault (LUKS2) + unlock screen in the Orion style | New | 1.0 | cryptsetup + Plymouth password prompt | Passphrase; optional FIDO2 key unlock in 1.x |
| Boot splash (black hole), greeter, lock screen | Partial | 1.0 | Plymouth theme; swaylock-effects for the lock screen | Planned in ARCHITECTURE §4 |
| Boot to HUD in < 30 s from USB 3 | New | 1.0 | casper `toram` | `toram` on machines with 16 GB+ RAM |
| Hardware check on first boot (Wi-Fi, audio, mic, camera, GPU, sleep) with a plain report | New | 1.0 | hw-probe / inxi, with an Orion report on top | Also shareable to help fix support |
| Wi-Fi/Bluetooth/audio (PipeWire)/printers | Partial | 1.0 | NetworkManager, BlueZ, PipeWire, CUPS (all via D-Bus) | Needs Orion-style settings screens |
| Sleep/resume, battery, brightness | New | 1.0 | power-profiles-daemon, brightnessctl, swayidle | Laptops |
| Installer (dual boot or whole disk) | New | 1.0 | Calamares with an Orion branding folder | |
| Write-wear reduction (zram, logs in RAM, noatime) | New | 1.0 | zram-generator, log2ram | |
| Offline mode: everything core works with no internet | Partial | 1.0 | Ollama + local voice (section C) | |

### B. The shell and the look
| Feature | Status | Priority | Reuse | Notes |
|---|---|---|---|---|
| HUD as the desktop (black hole, galactic modes, widgets, pet, panel) | Built | 1.0 | — | Needs GPU path tested on real hardware |
| Compositor with rounded corners, blur, shadows | Partial | 1.0 | SwayFX (takes today's Sway config as is) | Plain Sway today |
| Theme across GTK/Qt apps, icons, cursors, sounds | Partial | 1.0 | Kvantum/qt6ct, Papirus, Bibata cursors, freedesktop sounds | GTK + wofi done |
| App launcher, workspaces, window switcher | Built | 1.0 | wofi | Restyle as an Orion launcher overlay |
| Notifications into the HUD (mako → Alerts widget) | Partial | 1.0 | mako / freedesktop notification D-Bus | |
| Phone layout auto-detect (touch laptops, small screens) | Built | 1.0 | — | From the desktop app |
| Accessibility: screen reader, high contrast, reduced motion, text size, full keyboard use | Partial | 1.0 | Orca + AT-SPI | Reduced motion exists |
| Languages (English first, translation-ready strings) | New | 1.x | i18next | |

### C. Orion's brain and voice
| Feature | Status | Priority | Reuse | Notes |
|---|---|---|---|---|
| Chat, memory, standing rules, Grimoire commands | Built | 1.0 | — | |
| Model picker on first run: local (Ollama, auto-sized to the PC) or your own API key | Partial | 1.0 | Ollama | Must run on the OS itself, not the Windows host |
| Wake word "Orion" + speech-to-text | New | 1.0 | openWakeWord + faster-whisper, run as Wyoming services (Home Assistant's voice stack) | ARCHITECTURE §5 |
| Neural voice with sentence streaming | New | 1.0 | Piper / Kokoro (also as Wyoming services) | Replaces pyttsx3 |
| Cosmic voice presets (Nebula, Event Horizon, Starlight…) | New | 1.0 | Kokoro voice blending + Spotify's Pedalboard effects | V3 §8.2; the "wow" moment |
| Your own voice (Voice Forge, consent-gated, local only) | New | 1.x | | V3 §8.3 |
| Self-evolve / Talk-to-Develop | Built (off on OrionOS) | 1.x | — | Needs a safe path with a read-only system |

### D. Seeing and doing
| Feature | Status | Priority | Reuse | Notes |
|---|---|---|---|---|
| Screen reading (grim + OCR), focused window, idle | Built | 1.0 | grim, Tesseract | |
| Keyboard and mouse control (Sway IPC, wtype) | Partial | 1.0 | Sway IPC, wtype, ydotool | Mouse clicks untested on real hardware |
| **Guide Mode**: one next job, step cards, "Orion does it / You do it" | New | 1.0 | — | The headline feature |
| Guide Mode pointer ring + "step done" watcher | New | 1.0 | AT-SPI accessibility tree for exact button names and positions (OCR as backup); gtk-layer-shell overlay | |
| Macro recorder (watch & learn) on Wayland | Partial | 1.x | python-evdev to record, ydotool to replay | Built for Windows (pynput) |
| Clipboard alchemy | Built | 1.0 | wl-clipboard | |
| Drop on the black hole (assets, code, documents) | Built | 1.0 | — | |

### E. Apps
| Feature | Status | Priority | Reuse | Notes |
|---|---|---|---|---|
| Orion Files | Partial | 1.0 | Thunar (themed) | An Orion-native file view with "send to Orion" in 1.x |
| Orion Settings (Wi-Fi, display, sound, voice, model, pairing, vault, updates, privacy) | Partial | 1.0 | NetworkManager/BlueZ/PipeWire D-Bus; wdisplays | A wofi menu today → a real Tauri settings app |
| Orion Terminal | Built | 1.0 | foot | |
| App store (Flathub with an Orion front end) | Partial | 1.0 | Flatpak + Flathub/AppStream; Bazaar as a model | The setup agent's approve-then-install flow is the start |
| Browser with the Orion extension preinstalled | Partial | 1.0 | Firefox (Mozilla APT) | Extension exists |
| Music widget + media keys | Built | 1.0 | playerctl (MPRIS) | Needs the Linux backend wired in |
| Games: Steam (Proton) via Flathub, controller support | New | 1.x | Flathub Steam | No crypto; just games working well |
| Windows apps (Bottles/Wine) | New | 1.x | Bottles | |

### F. Work
| Feature | Status | Priority | Reuse | Notes |
|---|---|---|---|---|
| Projects, Constellation, Battle Stations, multi-repo radar, devlog | Built | 1.0 | — | |
| Coding-agent pet (Claude, Codex, Cursor, Gemini…) | Built | 1.0 | — | |
| Runs + self-healing terminal | Built | 1.0 | — | |
| Coding agents installable from the app store (Claude Code, VS Code, Cursor) | Partial | 1.0 | Setup-agent recipes | VS Code recipe exists |
| Agent sandboxes (rootless Podman, separate user, no network by default) | Built | 1.0 | Podman | |
| Real V3 agents (CTO/Community), replacing the stand-ins | New | 1.x | — | Proposing only, through the signer |

### G. Life
| Feature | Status | Priority | Reuse | Notes |
|---|---|---|---|---|
| Focus arc, pause-aware check-ins, Sanctuary | Built | 1.0 | — | |
| Sun & Moon (sunset shift), hydration, wind-down | Built | 1.0 | GeoClue (opt-in location) | |
| XP, momentum, skins, soundscape | Built | 1.0 | — | |
| Knowledge vault, idea catcher, podcast producer | Built | 1.0 | — | |
| Runway (manual numbers only) | Built | 1.0 | — | No crypto rails in 1.0 |

### H. Comms
| Feature | Status | Priority | Reuse | Notes |
|---|---|---|---|---|
| Unified inbox (Telegram, Discord, Messenger via extension) | Built | 1.0 | — | Each needs a guided sign-in in Guide Mode |
| Style-matched reply suggestions, digest, VIPs, voice replies | Built | 1.0 | — | |
| Phone texts/calls | New | 1.x | — | Through Orion Mobile |

### I. Approvals and the signer
On a USB stick there's no second VM for the boardroom, so in 1.0 **the signer is your phone**:

| Feature | Status | Priority | Reuse | Notes |
|---|---|---|---|---|
| Orion Mobile as the signer (hardware key, fingerprint) | Partial | 1.0 | Existing phone signing in `phones.py` | Make it the default signer |
| FIDO2 security key (YubiKey) as a signer | New | 1.x | libfido2 / python-fido2 | For people without Android |
| Boardroom VM or second device (advanced) | Built | 1.x | — | Kept for power users and V3 experiments |
| Autonomy learner, approval queue, audit log, kill switch | Built | 1.0 | — | |
| Small approvals in the HUD, big ones on the signer | Built | 1.0 | — | |

### J. Phone (Orion Mobile)
| Feature | Status | Priority | Reuse | Notes |
|---|---|---|---|---|
| Pairing, approvals with fingerprint, notifications | Built | 1.0 | — | Notifications: ntfy / UnifiedPush instead of 15-minute polling |
| Chat, Comms, push-to-talk voice, Guide cards on the phone | New | 1.0 | Tauri 2 Android build of the HUD's React code | ARCHITECTURE §8 |
| iPhone app | New | 1.x | Tauri 2 iOS build | |
| Pairing without Tailscale (local network + relay) | New | 1.0 | iroh (peer-to-peer with relays) or self-hosted headscale | The public won't all have Tailscale |

### K. Privacy and safety
| Feature | Status | Priority | Reuse | Notes |
|---|---|---|---|---|
| No telemetry; opt-in crash reports with a preview | New | 1.0 | GlitchTip (self-hosted, Sentry-compatible) | Say it plainly on the site |
| Privacy dashboard: what Orion can see, hear and touch, with switches | New | 1.0 | xdg-desktop-portal permission store | Screen, mic, camera, comms, location |
| Firewall, agent isolation, killswitch | Built | 1.0 | ufw, nftables | |
| Secret filter, approval rules, "never types passwords" | Built | 1.0 | — | |
| Signed images and updates | Partial | 1.0 | minisign | Key exists for the Windows updater |
| Independent security review before launch | New | 1.0 | OpenSSF Scorecard as a first pass | |

### L. First run and the public launch
| Feature | Status | Priority | Reuse | Notes |
|---|---|---|---|---|
| First-run flow: language → Wi-Fi → "meet Orion" voice tour → vault passphrase → model choice → pair phone (optional) | New | 1.0 | The demo's Tour | Orion talks and opens each menu |
| Browser demo + tour | Built | 1.0 | — | Already on the GitHub page |
| Website: download page, USB guide, video, FAQ, privacy page | Partial | 1.0 | Starlight or Docusaurus | Landing page exists |
| Docs: user guide, troubleshooting, hardware list | Partial | 1.0 | Same site | `os/GUIDE.md`, `os/README.md` |
| Launch video and 60-second "boot from USB to talking with Orion" clip | New | 1.0 | — | The thing that impresses people |
| Licence, third-party notices, trademark check on the name | New | 1.0 | REUSE tool, cargo-about, license-checker | |
| Community: Discord, issue templates, hardware reports | New | 1.0 | GitHub issue forms; hw-probe reports | |

## 3. Milestones

Each milestone ends at a gate. Time estimates assume focused part-time work with Claude doing most of the code.

| # | Milestone | Main work | Gate |
|---|---|---|---|
| **M0** | v0.1 proven | `Start OrionOS.cmd`, pair, approve the workspace setup, test mouse, a week of daily use | Both VMs boot, pair and run a week without a reinstall |
| **M1** | Bare metal | Install the lab image on a spare drive or old laptop; GPU HUD, Wi-Fi, sound, sleep | Orion runs on real hardware for a day with GPU rendering |
| **M2** | The image | CI pipeline from the Pop!_OS template; Secure Boot; "Try Orion" mode | A stick made from CI boots to the HUD on 3 different PCs |
| **M3** | Portable | Encrypted vault, Plymouth unlock, A/B slots, USB Maker (Imager fork), first-run flow | Unplug, boot on another PC, everything is still there; an update rolls back cleanly |
| **M4** | Orion's voice | Wyoming voice stack, cosmic presets, local model auto-setup | "Orion, …" works hands-free offline on an 8 GB laptop |
| **M5** | Guide Mode + apps | Guide Mode (AT-SPI + pointer ring), Settings app, Flathub store front end, phone as signer | A new user installs Discord and signs in with Guide Mode alone |
| **M6** | Public beta | Hardware report tool, accessibility pass, docs, security review, 50 beta testers | 90% of testers boot to Orion with no help; no critical security findings open |
| **M7** | **1.0 launch** | Website, video, signed release, NVIDIA + Standard images | Ship |

M4 and M5 can overlap with M2/M3 since they're mostly app-level work.

## 4. Hardware to test on (minimum matrix for beta)

- Intel laptop with integrated graphics (most common)
- AMD laptop or desktop (Ryzen + Radeon)
- NVIDIA desktop (needs the NVIDIA image)
- An older 8 GB machine (the low end we promise)
- Your own PC
- Two or three USB sticks: a cheap USB 3 stick, a fast one (Samsung/SanDisk Extreme class), a USB SSD

**Minimum spec we'd promise:** 64-bit PC from about 2015 onward, 8 GB RAM, USB 3 port. For local AI models, 16 GB
of RAM or a GPU with 6 GB+ is recommended; otherwise use an API key.

## 5. Risks

| Risk | What we do |
|---|---|
| NVIDIA drivers on a live stick | Separate NVIDIA image; fall back to software rendering with a clear notice |
| Secure Boot refusing to boot | Stay on Ubuntu's signed shim/kernel; document turning it off as a last resort |
| Slow or worn-out USB sticks | Read-only system, `toram` mode, write-light settings, recommend fast sticks |
| Wi-Fi chips without free firmware | Hardware check tells people which adapter works; ship all redistributable firmware |
| Local AI too heavy for the PC | Auto-pick a model by RAM/GPU; easy switch to an API key |
| People losing the vault passphrase | Recovery key at setup (print or save to phone), plain warning that we can't recover it |
| Public trust (an AI that sees your screen) | Privacy dashboard, no telemetry, open source, signed images, independent review |
| Accessibility on Sway is weaker than on GNOME | Test Orca + AT-SPI early (it also powers Guide Mode); fix gaps in the HUD itself |
| Upstream projects change or stall | Pin versions in the image build; prefer projects with several maintainers |

## 6. Decisions

1. **Base and build tool: decided, Ubuntu 24.04** with a scripted casper/squashfs pipeline in CI, using
   Pop!_OS's ISO build as the template.
2. **Compositor:** SwayFX (recommended: closest to today's Sway) or Hyprland (flashier, more effects, more churn).
3. **Signer for 1.0:** your phone by default (recommended), with the boardroom VM as an advanced option.
4. **Installer:** Calamares (recommended: fast to theme) or a custom Orion installer screen (more polish, more work).
5. **Release name and version:** "OrionOS 1.0" and a codename for the beta.
