# OrionOS architecture

OrionOS is Ubuntu 24.04 with Orion as the shell. You never see a stock desktop: you see the Orion HUD, Orion's own
themed apps, and the apps you choose, all driven by voice, keyboard or the phone. This document describes the target
system and how we get there from v0.1 (two VirtualBox VMs, see `README.md`).

## Decisions (2026-10-14)

| Question | Decision |
|---|---|
| Where OrionOS runs | **Both**: a VirtualBox VM on Windows, and bare metal from a USB SSD or its own drive partition |
| Compositor | **Sway** (SwayFX for rounded corners and shadows; plain Sway as fallback) |
| VPN behind the killswitch | **Your own WireGuard server** (`orion-killswitch enable <server-ip>`) |
| Phone approvals | **One tap + fingerprint**, money included, once the phone is paired as a signer (section 8) |
| iOS | Not now. Android only |

## 1. Layers

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ YOU            voice · keyboard/mouse · Orion Mobile (phone)                 │
├──────────────────────────────────────────────────────────────────────────────┤
│ SHELL          Orion HUD (Tauri, workspace 1) · Guide Mode step cards        │
│                Orion Files · Orion Settings · Comms · Queue · Life · Agents  │
├──────────────────────────────────────────────────────────────────────────────┤
│ COMPOSITOR     Sway (Wayland) · Orion theme · notifications (mako → HUD)     │
├──────────────────────────────────────────────────────────────────────────────┤
│ ORION DAEMON   orchestrator · voice (Whisper/Kokoro) · vision (grim+OCR)     │
│ (systemd user) desktop control (Sway IPC, wtype) · comms hub · boardroom     │
│                guide engine · service supervisor · update manager            │
├──────────────────────────────────────────────────────────────────────────────┤
│ SERVICES       WoW dev server (Podman) · treasury watcher (read-only)        │
│ (systemd)      Discord/Telegram bridges · Ollama · Tailscale · backups       │
├──────────────────────────────────────────────────────────────────────────────┤
│ SYSTEM         Ubuntu 24.04 · ufw/nftables · unattended-upgrades · btrfs     │
└──────────────────────────────────────────────────────────────────────────────┘
     ▲ signed verdicts only                         ▲ paired devices only
 orion-boardroom (separate VM / later: separate device)    Orion Mobile (APK)
```

**Rules that hold at every layer**
- The lab proposes, the boardroom signs. Nothing that spends money, merges to `main`, posts publicly or changes
  firewall/secrets runs without a boardroom signature (see `apps/daemon/boardroom.py`).
- No wallet private keys, seed phrases or exchange logins on any Orion machine. The treasury is **watch-only**.
- Account sign-ups, identity checks, logins and payments are always done by you; Guide Mode walks you through them.
- Every action an agent takes is logged with who, what, why.

## 2. Guide Mode (the "tell me what to click" system)

| Part | What it does | Built from |
|---|---|---|
| Idea engine | Reads the task board, git activity, Comms and the treasury; proposes **one** next job with a reason | orchestrator + memory_core |
| Plan | Breaks the job into 3-15 steps, each tagged **Orion does it** or **You do it** | orchestrator (structured output) |
| Step card | Floating, always-on-top Orion window: one instruction, a big Done/Skip/Help, read aloud by Kokoro | new `orion-guide` Tauri window (Sway rule: floating, sticky) |
| Pointer | Draws a glowing ring around the exact button to click | grim screenshot → OCR/vision locates the label → transparent overlay window |
| Watcher | Advances when the screen shows the expected result ("Upload complete") | capture_engine pHash + OCR |
| Hands | Runs the "Orion does it" steps: open apps, type, run builds, move files | `os_automation` on the Sway adapter |
| Brakes | Any step marked risky becomes a moon/boardroom decision; Esc or "Orion, stop" halts everything | approval queue, kill switch |

Steps that are always **You do it**: creating accounts, captchas, identity checks, typing passwords, 2FA, anything
involving payment details.

## 3. Always-on operations

| Job | How | You hear about it |
|---|---|---|
| Keep services running | `orion-supervisor`: a systemd target per service (WoW server, bridges, Ollama), restart on failure, health checks every 60 s | Notification + phone push after 3 failed restarts |
| OS updates | unattended-upgrades for security patches nightly; btrfs snapshot before each run | Weekly summary card |
| Orion updates | Signed bundle (minisign, same key as the Windows updater) downloaded to staging, tests run, then a **boardroom decision** to apply; automatic rollback if the daemon fails to start | Approval moon |
| Backups | Nightly encrypted restic backup of `~/.orion`, `~/projects`, WoW DB to an external drive or your own storage | Failure only |
| Approvals | Every pending decision → HUD moon + desktop notification + phone push (via the paired APK) | Immediately |

## 4. Look and feel: the Orion theme

One design language everywhere, taken from the HUD (`theme/colors.ts`, `index.css` emboss tokens):

| Token | Value | Used for |
|---|---|---|
| Void | `#05070E` | Wallpaper, terminal, window backgrounds |
| Panel | `#0B0F1E` at 80 % | Cards, notifications, step cards |
| Accent | `#7C5CFF` → `#00E5FF` gradient | Focus rings, active window border, buttons |
| Text | `#D8DEFF` / quiet `#8888AA` | Everywhere |
| Corners | 14 px windows, 10 px cards, pill buttons | Sway gaps + GTK/Qt theme |
| Type | Nunito (UI), JetBrains Mono (code) | All apps |

Delivered as: a GTK 3/4 theme + Qt (Kvantum) theme generated from those tokens, Papirus icons recoloured, matching
Sway/foot/mako/wofi configs, Plymouth boot splash with the black hole, and greetd greeter in the same style. Window
rounding and soft shadows come from **SwayFX**, a drop-in Sway fork that reads the same config and adds rounded
corners, blur and shadows. It isn't in Ubuntu 24.04's archive, so we build it once and ship it in the bundle. Plain
Sway stays the fallback (square corners, everything else identical).

**Orion's own apps** (Tauri + React, same components as the HUD):
- **Orion Files**: home, Projects, Vault, Captures, Downloads; drag a file onto the black hole to process it.
- **Orion Settings**: Wi-Fi, display, sound, voice, pairing (boardroom, phone), killswitch, updates.
- **Orion Terminal**: foot with the theme. Standard apps (Firefox, VS Code) are themed, not replaced.

## 5. Voice control

Always-listening wake word ("Orion") on the CPU with openWakeWord → Whisper for the command → `commands.py`
deterministic router first, LLM second. Voice can open/close/switch apps and workspaces, dictate, run Grimoire
commands, read and answer Comms ("reply: on my way" stages a draft; "send it" sends), and approve **low-risk**
moons. It can never approve boardroom decisions; those need the boardroom passphrase.

## 6. Workloads on the lab

| Workload | Runs as | Notes |
|---|---|---|
| OpenWoW dev server (AzerothCore/TrinityCore build + MySQL) | Podman pod, `orion-agent` user, 4 GB RAM cap | Rebuilt from a git worktree per task; ports bound to localhost/Tailscale only |
| Treasury watcher | Python service, read-only | Watches public addresses and Ko-fi webhooks; feeds the paper/real ledger; holds no keys |
| Discord + Telegram + Messenger | Existing bridges (`discord_bridge`, `telegram_*`, extension) | Unified Comms tab; replies only from you or an approved draft |
| Local models | Ollama on the Windows host now; on the lab when it has a GPU | |

## 7. Running OrionOS: VM and bare metal

OrionOS is one image that runs three ways. All three use the same provisioning scripts, so they behave the same.

| Way | How | Your Windows drive |
|---|---|---|
| **VM** (now) | VirtualBox on Windows (`Create OrionOS VMs.cmd`) | Untouched |
| **USB SSD** (first bare-metal step) | OrionOS installed onto an external USB 3 SSD (256 GB+). Pick it in the laptop's boot menu (F12 on most Lenovos); without the SSD plugged in, Windows boots as normal | Untouched |
| **Drive partition** | OrionOS on a partition of the internal drive (or a second internal drive). GRUB menu offers OrionOS or Windows | Shrunk once, from Windows Disk Management |

**How the bare-metal image is made:** an Ubuntu 24.04 autoinstall ISO whose `late-commands` run the same
`os/lab/provision.sh`. Write it to a USB stick with Rufus, boot it, pick the target (USB SSD or partition), and
it installs and provisions itself. It's the same flow the VMs use, just on real hardware.

**Bare-metal differences:** the real GPU is used (no software rendering, full-speed black hole). NVIDIA cards
need NVIDIA's own driver: the USB image's NVIDIA flavour (`os/image/build.sh --flavour nvidia`) ships it
(`nvidia-driver-580` with Ubuntu's ready-built, signed kernel module) and boots with `nvidia-drm.modeset=1`; the
standard image doesn't, but `orion-install-disk` installs it (`ubuntu-drivers install`, else the 580 packages, which
needs internet) and adds the kernel option when it finds an NVIDIA card. `orion-session` starts Sway with
`--unsupported-gpu` whenever the `nvidia` module is loaded (on Intel/AMD + NVIDIA laptops the built-in card keeps
drawing the screen). The baremetal provisioning path does not install NVIDIA drivers by itself. Sleep, Wi-Fi,
sound and mic are checked by `orion-check-hardware` (Settings > This PC's hardware).

**Where the boardroom goes on bare metal:** the phone (section 8). With the phone as the signer, the lab can
boot alone from the USB SSD and still have a separate device holding the approval key. The boardroom VM remains
available as a second, optional signer.

**Before touching the internal drive** (partition way only): full Windows backup, BitLocker recovery key written
down, Windows Fast Startup turned off. The USB SSD way needs none of this, which is why it comes first.

| Stage | Gate to move on |
|---|---|
| A. VMs | Both VMs boot, pair, and run the loop for a week |
| B. USB SSD | Laptop boots OrionOS from the SSD; GPU, Wi-Fi, sound, mic and sleep all work |
| C. Phone signer | Phone approves boardroom decisions; the boardroom VM becomes optional |
| D. Partition (optional) | You want OrionOS on the internal drive for speed |

## 8. Orion Mobile link (Android)

The APK (Tauri 2 Android + a Kotlin plugin) pairs with the lab like the boardroom: `orion-ctl pair-phone` shows a
one-time code as a QR, the phone makes a key in its **hardware keystore** (StrongBox where available), you confirm
the fingerprint on the lab, and from then on every request is signed and goes over Tailscale.

**Approving is one tap.** A notification says what's waiting ("Pay $25 bounty to contributor-05 (simulated)"):
- **Low-risk items** (draft replies, docs merges at level 0-1): tap **Approve**.
- **Boardroom items** (money, merges to `main`, public posts, firewall): tap **Approve**, then touch the
  fingerprint sensor. The keystore only signs after a fresh fingerprint, so the key can't be used by an app or by
  someone holding your unlocked phone without your finger.
- The phone signs the hash of exactly what it showed you, the same rule as the boardroom console.

The phone also gets Comms (read and reply), push-to-talk voice, Guide Mode step cards and service status. A lost
phone is revoked with `orion-ctl unpair-phone`. iOS is not planned for now.

## 9. Build order

1. Theme pack (GTK/Qt/icons/Sway/foot/mako/wofi/Plymouth) + Orion Settings + Orion Files
2. Guide Mode v1: plan + step cards + "You do it / Orion does it", no pointer ring yet
3. Supervisor + update manager + approval notifications
4. Wake-word voice control
5. WoW dev server pod + treasury watcher (read-only) wired to the boardroom
6. Phone pairing + signer in the daemon (B0), then the Android APK with one-tap approvals
7. Bare-metal autoinstall ISO → USB SSD install
8. Guide Mode pointer ring + watcher
9. SwayFX build for rounded windows; drive-partition install guide
