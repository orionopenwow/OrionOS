# OrionOS: complete and self-running (plan, not started)

Written 2026-10-16. Plan only; nothing is built yet. It follows the Art Update and sits alongside the Toolkit plan.
Building waits for your go. The Art Update and Toolkit steps 1–2 are merged into the repo (2026-10-16), so
the repo is free; Collision Guard now exists to keep parallel agents apart.

**Where it sits:** saved as `docs/ORIONOS_COMPLETE_PLAN.md`. It completes OrionOS 1.x (`ORIONOS_V1_PLAN.md`) and
runs before or alongside the after-1.0 pass (`ORIONOS_AFTER_1_0_PLAN.md`); Part 3 builds on the Toolkit
(Clean Box, AI Blame, Host Health, Share Scrubber).

**Goal:**
1. OrionOS does everything people expect from an operating system.
2. It adds the Craft creative suite.
3. Over time it keeps itself healthy and up to date on its own, within limits you set.

---

## Part 1. The basics everyone expects

**Checked in `os/lab/provision.sh` on 2026-10-16.** OrionOS ships with:
- Sway, the foot terminal, Thunar, wofi, mako, grim, PipeWire, Podman, Tesseract, ufw and unattended-upgrades.
- On real hardware: NetworkManager, Bluetooth and fwupd.

It does **not** ship a web browser or any app for viewing PDFs, images, video or documents. The clock is set to
UTC.

| Gap | Fix | Notes |
|---|---|---|
| **Web browser** | Firefox from Mozilla's own APT repo (packages.mozilla.org), not the snap. Chromium as an option. | Most urgent gap. Themed to match. Default search and homepage are your choice. |
| **Open everyday files** | PDF, images, video/music, text, calculator; see Part 2 for which app does what | Every file type opens with something on day one. |
| **App store** | Flatpak with Flathub, shown in an Orion "Get apps" window | One-click installs. Updates are tested, then rolled back if broken (Part 3). |
| **Clock / time zone** | Set automatically from location at first start (with a manual override), using network time | Today it's stuck on UTC. |
| **Screenshots** | Print Screen = whole screen, Shift+Print = pick an area, with a quick markup tool. Saved to Pictures/Screenshots and copied. | grim + slurp + swappy. Optional Share Scrubber blur (Toolkit #23). |
| **Clipboard history** | Super+V shows the last 50 copies. Passwords are skipped. | cliphist |
| **Night light** | A warmer screen after sunset, automatic from location | wlsunset or gammastep |
| **Several monitors** | Plug in and it remembers each setup. A simple arrange screen. | kanshi + wdisplays |
| **Printing and scanning** | Printers are found automatically on the network. A scan app. | CUPS (driverless IPP), Simple Scan |
| **Office documents** | Opening and editing Word, Excel and PowerPoint files | LibreOffice now; the Craft apps when mature (Part 2) |
| **Undo for the whole system** | Snapshots before every update, plus a nightly one. Roll back from the boot menu. | Installed systems: btrfs + snapper. USB stick: already covered by A/B updates for the system; vault snapshots added. |
| **Settings window** | A proper Orion Settings app in the glass style: Wi-Fi, Bluetooth, sound, displays, power, users, privacy, updates, Orion's permission levels | Replaces the text menu |
| **Accessibility** | Screen reader, zoom, on-screen keyboard, high contrast, bigger text, reduced motion | Orca (Sway support to be tested), wvkbd, HUD zoom and contrast modes |
| **Typing other languages** | Input methods for other languages and emoji | fcitx5 |
| **File sharing** | Send files between OrionOS, Windows and your phone, on the same network | LocalSend (works on Windows, Android and Linux) |
| **More than one person** | A guest account that wipes itself at log-out, and easy extra users | A Settings page |
| **Laptop power** | Sleep, hibernate, lid close and low-battery warnings, tested on your laptop | Plus battery health in Host Health (Toolkit #21) |
| **Real-hardware checks** | Install to the laptop, Wi-Fi, NVIDIA, sleep, phone app built and paired | Carried over from the 1.0 status list |

---

## Part 2. The Craft creative suite (storytold / ArtCraft)

**What it is:** the "Crafting Apps" are free creative apps written in Rust by the ArtCraft team, published under
the **storytold** organisation on GitHub (https://github.com/storytold).
- Each one imitates a well-known commercial tool.
- They are dual-licensed MIT / Apache-2.0.
- They ship Linux packages (AppImage, Flatpak, .deb), and many come with a command-line tool and an MCP server so
  AI agents can drive them.

PhotoCraft has the most stars in the organisation; sources: its README, aiweekly.co and jacar.es, checked
2026-10-16.

| App | Like | Status (as of Oct 2026) | Agent hooks |
|---|---|---|---|
| **PhotoCraft** | Photoshop: layers, masks, PSD files | Early alpha | CLI with hundreds of commands; MCP server (`photocraft-cli mcp`); local control channel |
| **PdfCraft** | Reading, editing, merging and redacting PDFs | Early alpha | CLI; MCP with about 160 tools |
| **LightCraft** | Photo library and RAW development | In development | — |
| **VectorCraft** | Vector illustration | In development | — |
| **FilmCraft** | Video editing, colour and sound | In development | — |
| **EffectCraft** | Motion graphics and effects | In development | — |
| **DesignCraft** | Page layout | In development | — |
| **FigCraft** | Interface design and prototyping | In development | — |
| **WordCraft** | Word processor (Word) | In development | — |
| **GridCraft / DeckCraft / SoundCraft / CADCraft** | Spreadsheets, slides, audio, CAD | In development | — |

### How Orion uses it

- **"Orion Studio" in Get apps.** One tap installs the suite as Flatpaks, or AppImages with their own updater.
  Installs are checked for signatures, and updates go through the test-then-rollback flow (Part 3).
- **Opened by default where it's already good:**
  - PDFs open in PdfCraft. A simple, mature viewer stays as backup.
  - Images open in PhotoCraft for editing, with a fast image viewer for just looking.
- **Mature apps stay the safe default for daily work** while the Craft apps are in alpha: LibreOffice for office
  files, mpv for video, and a simple viewer for PDFs. Each file type gets a "Try with Craft" option, and Orion
  switches the default when a Craft app becomes stable.
- **Agents can use them.** The Craft MCP servers join Orion's MCP bus. That makes requests like these possible:
  - "Orion, sharpen these screenshots and make a 1200 px version for the website."
  - "Merge these PDFs and redact my address."
  - "Make the patch-notes banner."

  Agents use the same approval rules as everything else. Writing over your originals always asks; by default
  results go into a new file.
- **Look:** the apps keep their own design. Orion's window style (glass, rounded corners, shadows and belt dots
  from SwayFX) still frames them. Their names and logos are their trademarks, so Orion doesn't rebrand them.
- **Your projects:**
  - Video Scout thumbnails and Raid Night banners through PhotoCraft.
  - Patch-note PDFs through PdfCraft.
  - Trailer cuts through FilmCraft, for the Phase 7 "trailer director".

### Cautions

- Most of the suite is alpha or in development. The README says PhotoCraft is not yet a replacement for daily
  professional work.
- The apps describe themselves as clean-room reimplementations. Some people have publicly questioned how they
  were made, and nothing is proven either way. Orion offers them as optional installs from their official
  releases, and doesn't bundle them in the OrionOS image until that settles. This matches Orion's own clean-room
  rules for Benilla.

---

## Part 3. Orion keeps itself running and up to date

What already exists:
- signed system updates;
- two system copies with automatic rollback;
- the approval system (phone fingerprint);
- the crash fixer's tested proposals for your projects;
- the planned self-reprogramming with a protected core (after-1.0 plan §7).

What's missing are the eight parts below.

| # | Part | What it does |
|---|---|---|
| 1 | **Health monitor** | Keeps checking that the screen, AI, voice, phone link, network, disk space, memory, temperature and battery are fine. Today only "it started" is checked. Results feed the black hole: turbulence when something's wrong. |
| 2 | **Self-repair loop** | When a check fails: diagnose, then try a fix in a throwaway copy (Clean Box), then test it, then propose. Routine fixes (restart a stuck service, clear a full cache) can run automatically if you allow it. Anything bigger asks. If a fix makes things worse, it's undone. |
| 3 | **Update channels with a test run first** | Stable, Beta, and "My builds". Every new version boots first in a VM with the self-test suite. Only if that passes is it offered to your laptop, into the spare system slot, with automatic rollback. |
| 4 | **Permission levels for self-changes** | You choose how far Orion goes alone (table below). The protected core never changes itself. |
| 5 | **Everything stays current** | System packages, Flatpaks (including Craft), AI models, browser, drivers and firmware. Each kind is snapshotted first and rolled back if a health check fails afterwards. |
| 6 | **A readable change history** | Every change Orion made to itself: what, why, and the tests it passed. One tap undoes it. Linked to AI Blame (Toolkit #4). |
| 7 | **Kill switch** | One phone button, or "Orion, freeze", stops all self-changes, updates and agents until you unfreeze. |
| 8 | **Weekly self-review** | One card each Sunday covering what broke, what Orion fixed, what it updated, what it would like to change next, and the cost. You accept or dismiss each item. |

### Permission levels (default in bold)

| Kind of change | Level 1: always ask | Level 2: tell me after | Level 3: just do it |
|---|---|---|---|
| Security updates | | | **✓** (with snapshot and rollback) |
| App and Flatpak updates | | **✓** | |
| Restarting a stuck service, clearing caches | | | **✓** |
| New system version (A/B slot) | **✓** | | |
| AI model updates | | **✓** | |
| Orion changing its own code (UI) | **✓** | | |
| Orion changing its own daemon code | **✓** (fingerprint) | | |
| Protected core: approvals, signer, firewall, privacy rules, the self-change gate | **Always ask, fingerprint, never automatic, cannot be raised** | | |

The autonomy learner can suggest raising a level after a run of approvals ("you've approved 20 app updates in a
row, let me just tell you after?"). Raising always needs your OK.

**Self-test suite:** runs in VMs, Clean Boxes and on boot. It reuses the QEMU boot test, the daemon tests,
screenshot checks of the HUD, and a voice loopback test. Updates and self-changes must pass it first.

---

## Order of work

| Step | What | Why |
|---|---|---|
| 1 | Browser, file apps (PDF/image/video/text/calculator), clock, screenshots, clipboard, night light | Everyday basics people notice in the first minute |
| 2 | Settings window, Get apps (Flatpak), file sharing, printing, multi-monitor | A complete OS |
| 3 | Snapshots, health monitor, kill switch, change history | Safety net before anything automatic |
| 4 | Update channels with VM test run, self-repair loop, permission levels, weekly review | Self-running |
| 5 | Orion Studio (Craft suite) + Craft MCP tools on the bus | Creative power and agent access |
| 6 | Accessibility, input methods, guest account, laptop power tests | Everyone can use it |

Parallel agents can split steps 1, 2 and 5; step 4 depends on step 3. The Toolkit plan's Collision Guard comes
first so they don't clash.

## Sources

- storytold organisation: https://github.com/storytold
- PhotoCraft README: https://github.com/storytold/photocraft
- AI Weekly on PhotoCraft:
  https://aiweekly.co/alerts/photocraft-ships-rust-photoshop-clone-with-mcp-agent-server
- Jacar on the twelve Crafting Apps: https://jacar.es/artcraft-crafting-apps-rust-codigo-abierto/

## Notes from saving this plan into the repo

- **Browser vs the boardroom VM:** the boardroom VM check (`scripts/vm/verify-boardroom.sh`) fails if a web
  browser is installed. That rule is for the signing VM only; the OrionOS image gets Firefox. Keep the two apart.
- **Already in the repo after the Art Update:** the Orion Glass GTK theme, Orion icons, Thunar layout, themed
  wofi/mako/swaylock and the SwayFX window frame, so new apps (Firefox, viewers, Settings) inherit the look.
- **Self-change gate:** Part 3 levels match the protected core in the after-1.0 plan §7 and the approval rules;
  Proof Gate (built) is the test step for Orion's own code changes.
- **Craft suite:** optional installs only, never bundled in the image, until the clean-room questions settle.

