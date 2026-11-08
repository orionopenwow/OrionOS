# Orion — next pass plan (V3)

> **Current focus:** OrionOS 1.0, bootable from a USB drive. See [`ORIONOS_V1_PLAN.md`](ORIONOS_V1_PLAN.md).
> Money, treasury and Stardust work is parked until it ships.

Status: **planned, not started.** Captured 2026-10-14. Nothing in this document has been built yet.

The V3 pass turns Orion from a personal desktop companion into the operator of the OpenWoW project. A small
team of AI sub-agents runs day-to-day work. Anything risky goes to the owner for a one-click decision from an
isolated machine. Money comes in through a transparent, non-custodial treasury.

---

## 1. AI CEO and the multi-agent hierarchy

**Goal:** Orion runs OpenWoW day to day as its lead maintainer. It delegates work to specialised sub-agents
and reports to the owner.

| Agent | Owns | Can do without asking | Always needs sign-off |
|---|---|---|---|
| **CEO (Orion)** | Priorities, delegation, weekly report, public voice | Assign tasks, write status updates, draft announcements | Public announcements, roadmap changes, anything touching money |
| **CTO** | Code | Open PRs from a sandboxed worktree, run CI builds, review contributor PRs | Merging to `main`, releases, dependency or licence changes |
| **CFO** | Treasury maths | Reconcile incoming payments, update the Priority Sheet, forecast runway | Every payout, bucket-rule changes |
| **Community** | Discord | Triage (the V2 prompt detector), run polls, answer FAQs from the vault | Moderation actions (bans, deletions), anything posted as the owner |
| **DevOps** | Infrastructure | Health checks, log triage, scale-up proposals with cost estimates | Spending money, destructive operations, firewall or secret changes |

**Build pieces**
- `agents/` package in the daemon: one class per role sharing a task board (LanceDB table `agent_tasks`).
  Each role has its own system prompt, tool allow-list and budget (tokens per day, money cap = 0 unless approved).
- CEO loop: a daily stand-up that reads the task board, the git overview, Discord triage stats and the treasury,
  then writes a short brief to the Queue and the Life tab.
- CTO sandbox: git worktrees per task, builds in a container or a separate Windows user, and PRs opened with a
  bot token whose permissions stop at opening PRs. Reuses `self_evolve.py`'s patch engine and the cross-repo
  transplant code.
- DevOps: VPS provider API behind an adapter. Read-only metrics first; scaling is always a *proposal* card with
  a cost estimate.
- UI: an **Agents** tab showing each agent's current task, today's actions, cost so far, and a pause switch per agent.

**Design rules**
- **Disclosure:** the agents speak publicly as *Orion, OpenWoW's AI maintainer*, never as
  the owner and never as an unnamed human. Community trust depends on people knowing when they're talking to an AI,
  and Discord and GitHub both have rules about automated accounts.
- Every agent action is logged with who did it, what it did, why, and a link (an audit trail viewable in the
  Agents tab).
- Global kill switch: one hotkey or tray item pauses every agent at once.

---

## 2. VM Shadow Boardroom and the autonomy learner

**Goal:** high-stakes decisions (merges, releases, payouts, infra spend) wait for the owner on an isolated,
VPN-locked virtual machine. They appear as orbiting "moons" around the core for one-click sign-off. Orion learns
from each decision until low-risk routine tasks can run on their own.

**Build pieces**
- **Boardroom VM:** a separate Windows or Linux VM reachable only over WireGuard/Tailscale. It runs a minimal
  Orion client in "boardroom mode" with no general browsing and no other apps. Approvals there are signed with
  a key that never leaves the VM.
- **Decision transport:** the main daemon pushes decision packets (summary, diff or payout details, risk score,
  agent's recommendation) to the VM. The VM returns signed approve, edit or reject verdicts, and the daemon
  verifies the signature before acting.
- **Moons UI:** each pending decision orbits the singularity. Size shows the stakes and colour shows the domain
  (code, money, infra, community). Click to expand, approve, edit or reject. This reuses the VIP-satellite
  component.
- **Autonomy learner:** every decision is stored in LanceDB `boardroom_decisions` (context, agent proposal,
  your verdict, edits). A task *type* graduates up an autonomy ladder only after a streak of identical approvals:

  | Level | Behaviour | Example promotion rule |
  |---|---|---|
  | 0. Ask | Always asks | default |
  | 1. Suggest + auto-queue | Pre-fills the decision, still asks | 10 approvals, 0 edits |
  | 2. Act + notify | Does it, tells you, can be undone | 30 approvals, 0 rejections, task is reversible |
  | 3. Silent | Does it, logs only | Reversible, low-impact tasks only, after 100 approvals |

  Any rejection or edit drops the task type back a level. The owner can pin any task type to "always ask".

**Hard limits (not learnable)**
- **Money never graduates past level 1.** Every payout, refund or transfer needs a fresh human signature, however
  routine it looks. The same goes for merges to `main` of code touching auth, payments or secrets, deleting data,
  and anything irreversible.
- Promotions themselves need a one-time sign-off ("Let CTO auto-merge docs-only PRs?").

---

## 3. Transparent waterfall treasury and the `$MAGIC` economy

> **Update:** `$MAGIC` is replaced by **Stardust (`$DUST`)**, the fuel that pays for Orion's AI, game modding
> (Forge) and in-game stores. The treasury rules below still apply. See section 9 and
> [`STARDUST_PLAN.md`](STARDUST_PLAN.md).

**Goal:** donations and support arrive through three rails and are split automatically into five public
buckets, live-streamed as a Priority Sheet. Supporters earn cross-game Orion's Magic perks.

**Intake (non-custodial; Orion never holds keys)**
| Rail | How Orion sees it | Keys |
|---|---|---|
| Solana / USDC | Watches a public receiving address through an RPC or indexer | Owner's wallet; Orion is watch-only |
| Bitcoin | Watch-only `xPub` derives fresh receive addresses; balance via a public API | Private keys never on any Orion machine |
| Ko-fi (fiat) | Ko-fi webhook → daemon → ledger | Ko-fi account stays the owner's |

**Waterfall Priority Sheet (5 buckets)**
1. Hosting — fill to next month's server bill first
2. API fuel — model and API costs for the agents
3. Worker bounties — paid tasks for contributors
4. Server upgrades — saved toward the next tier
5. Good causes — overflow share to a chosen charity

- The CFO agent fills buckets in order up to each cap, then overflows. Caps and order live in a versioned
  `treasury_rules.json` that changes only through a boardroom decision.
- The ledger is append-only (SQLite plus a daily signed snapshot), and every entry links to its on-chain
  transaction or Ko-fi receipt.
- **Live sheet:** a public page (on the Orion GitHub site) and an OBS browser source for streams, showing each
  bucket's progress, recent supporters (opt-in names only) and spending with receipts.
- Payouts (bounties, hosting bills) are **proposed** by the CFO and executed by the owner from their own wallet after
  boardroom sign-off. Orion prepares the transaction details; it never signs or sends.

**`$MAGIC` perks**
- **Start off-chain:** `$MAGIC` is a points balance in the ledger, tied to Discord or GitHub identities, earned
  by donating, contributing (merged PRs, bug reports, events) and community help.
- Perks: cosmetic in-game titles and mounts, Discord roles, early access to realms, and a vote weight on the
  roadmap polls the Community agent runs.
- Cross-game: a small read-only API (`/magic/{player}`) that other OpenWoW realms or games can query.

**Decisions needed before building this section** (talk to a lawyer before launch):
- **Token or points?** Turning `$MAGIC` into an on-chain token that people buy, or that grants "governance rights"
  over a fund, can make it a regulated security or money-transmission product in many places. Keeping it as
  non-transferable, non-purchasable perk points avoids most of that. Recommendation: points first, and revisit a
  token only with legal advice.
- **Accepting donations for OpenWoW:** OpenWoW builds on World of Warcraft. Taking money (or selling perks)
  for a project built on another company's game and IP raises legal risk, and private-server operators have been
  sued over monetisation. Get advice on how donations and perks can be framed (for example, funding open-source
  tooling and hosting rather than access to the game).
- **Tax and records:** crypto and fiat donations may count as income. The ledger should export for an
  accountant from day one.
- **Good-causes bucket:** pick a registered charity and publish the receipts.

---

## 4. Coding-agent pet: make it actually detect agents

**Problem:** the pet never wakes on the owner's PC. It watches `~/.claude/projects/` for Claude Code session logs,
but that folder doesn't exist on that machine. Their coding agents run elsewhere:
- **Claude in the desktop app:** these sessions run in Anthropic's cloud, so they leave no local logs.
- **Antigravity, Cursor, Codex and Gemini:** each has its own folder in the home directory.

The watcher works in tests; it is just looking in the wrong place.

**Build pieces**
- **Multi-agent sources:** extend `agent_watch.py` to also watch:
  - Codex CLI (`~/.codex/sessions/**.jsonl`)
  - Gemini CLI (`~/.gemini/tmp/**`)
  - Cursor and Antigravity agent logs (locate their files on the PC first)
  - Claude Code, if it's installed later

  Each source becomes an adapter returning `(working?, status, project)`. The pet shows the agent's name
  ("Claude is editing App.tsx", "Codex is running tests").
- **Desktop-app and cloud sessions:** these leave no local files, so add a lightweight signal:
  - Window and process activity of the Claude desktop app, Antigravity and Cursor (focused window title plus
    CPU spikes of the agent process), shown as a softer "probably working".
  - A tiny local endpoint (`POST /agent/ping`) that any agent or script can call with a status. Orion's own
    sessions can call it through the desktop bridge while they work.
- **File-change fallback:** if a watched workspace sees a burst of file writes it didn't make itself
  (git-tracked files changing every few seconds), the pet treats that as an agent at work and names the
  project.
- **Debug view:** add a "What I'm watching" line to the pet's click card (sources found, last signal time),
  so a silent pet can be diagnosed without logs.
- **Test on the real PC:** start an agent in each tool and confirm the pet wakes within 5 seconds and naps
  within 30 seconds of it stopping.

---

## 5. Life OS and bio-rhythm engine

Partly built: the 90-minute focus arc, break check-ins, breathwork vortex, hydration and the 21:30 wind-down
already exist. This section makes the clock actively shape the day.

- **Sanctuary Mode after each 90-minute lap:** when the captured star reaches the event horizon, the vortex
  slows and turns deep amber. Orion's voice guides a 5-minute recovery chosen from the conditioning deck
  (for example box breathing + 20 push-ups or squats).
  - The next focus block "unlocks" when the routine is done.
  - This is a soft gate: a visible "skip this once" always works, and skips are logged so the roulette can adapt.
    Orion should never lock you out of your own computer.
- **Sun & Moon shifting:** compute local solar elevation from a saved latitude/longitude (entered once, no
  network needed). At sunset:
  - Strip blue from the HUD with a warm filter.
  - Shift the disk to warm violet/gold.
  - Lower TTS volume by 15%.
  - Hold non-critical coding alerts until morning. VIP messages and anything marked urgent still come through.

  This replaces the fixed bedtime-minus-90 rule with real dusk, and the bedtime rule stays as a fallback.
- **Logistics and travel radar:**
  - Drop a voice note, receipt photo or screenshot on the viewport. Orion reads it locally (OCR, plus Whisper
    for audio) and extracts the amount, place and date.
  - Expenses go into the personal SQLite ledger (shared with the Runway tab) after a confirm card.
  - Trips, supply runs and meal prep become calendar blocks once a calendar is connected.
  - Weather and wind windows come from a forecast API for saved places.

## 6. Multi-venture "holding company" portfolio

Extends the single-project setup so one Orion runs every venture: OpenWoW, Kiln, Jiablo, Kubi, MetaWiki and
community or ecological initiatives.

- **Constellation Switcher:** zooming out from the core (or opening Projects) shows a star map with one solar
  system per venture. Clicking a star switches the active context, including git repos, Discord channels,
  treasury buckets, rules and agents. Data model: `ventures.json` in the preload kit, with each venture owning
  its own workspaces, channels and buckets.
- **Cross-pollination agent:** a local code indexer (tree-sitter ASTs plus embeddings) across every venture's
  repos. When code in one project matches an open issue or TODO in another ("this water shader from
  Project A solves an open issue in Project B"), it proposes a port. The port goes through the existing
  cross-repo transplant and approval queue.
- **Clip factory:** connect to OBS over its WebSocket API with the replay buffer on.
  - Triggers: activity spikes (a big donation, a boss kill, a chat burst, a merged milestone PR).
  - Each trigger saves the last 60 seconds, adds captions with Whisper, and crops to vertical.
  - The result is queued as a purple approval moon. Nothing is posted to YouTube Shorts or X without approval.
- **Chief-of-staff triage:**
  - Comms grows to cover GitHub notifications and email (through a connector) next to the chat platforms.
  - The local model sorts by urgency and drafts in the owner's tone.
  - Bulk clear: drag moons into the black hole to clear them. That only ever means *mark read / ignore / archive*,
    never send.

## 7. A tactile, game-like desktop

- **Event-horizon drag and drop:** dropped files spiral into the disk with a "spaghettification" stretch shader,
  then pop back out processed.
  - **Images or 3D models:** background removed, compressed to `.webp`, or converted to `.glb`, using the
    existing asset pills.
  - **Code or error logs:** a cyan flare, then a refactor patch or bug report in the Queue.
  - **PDFs and articles:** indexed into memory, then a 20-second spoken briefing.
- **Living ecosystem, a Tamagotchi meets a Dyson sphere:**
  - **Momentum:** focus blocks done, commits shipped and healthy servers make the disk burn cyan-emerald, and a
    micro Dyson swarm slowly builds itself around the core.
  - **Entropy:** skipped sleep or piled-up critical bugs bring red asteroid debris into orbit. Clearing the
    queue destroys them, a tower-defence loop for bug fixing.
  - This is driven by a single "momentum score" computed from pacing, git, the Queue and server health.
- **Quest log and Architect XP:** focus sessions, workouts and merged PRs grant XP (and personal `$MAGIC`
  points). Levels unlock singularity skins built on the galactic-mode system: Supernova Gold, Blood Moon
  Eclipse, Emerald Nebula, Arcane Runic.
- **Ambient soundscape synth:**
  - A procedural Web Audio layer next to the Kokoro voice: subtle gamma-focus drones, rain or campfire textures.
  - Its tempo and filter follow the vortex's spin speed.
  - Off by default, with a volume slider, and it ducks automatically while Orion speaks.

---

## 8. Cosmic voices, and your own voice

Today Orion speaks through the PC's built-in voices (`pyttsx3`), and the browser demo uses the browser's voice.
The plan: a small set of designed **cosmic voices**, plus a voice cloned from **your own recordings**, all
running on your PC.

### 8.1 Voice engine
- Swap the speech layer for a local neural engine behind one `VoiceEngine` interface:
  `speak(text, voice_id) -> audio`, streamed sentence by sentence so Orion starts talking in under half a second.
- Keep `pyttsx3` as the fallback when no model is installed.
- Candidates to evaluate (check each licence before shipping, since several cloning models are non-commercial):
  - **Kokoro** — small, fast and good quality for the built-in voices.
  - **Piper** — very light, runs on any CPU.
  - For cloning: **Chatterbox**, **OpenVoice v2**, **F5-TTS** and **XTTS-v2**.
- GPU when available, CPU otherwise. Models download on first use into `~/.orion/voices/models`, and the
  installer stays small.

### 8.2 Cosmic voice presets
Each preset is a base voice plus a light, real-time effects chain, applied after synthesis so it works with
any engine:

| Preset | Character | Effects |
|---|---|---|
| **Nebula** (default) | warm, close, calm | soft plate reverb, gentle high shelf |
| **Event Horizon** | low, slow, gravitational | −2 semitones, long dark reverb, sub-harmonic layer |
| **Starlight** | bright, airy, quick | shimmer reverb (octave-up tail), light chorus |
| **Pulsar** | crisp, rhythmic, focused | short room, subtle tremolo synced to speech rate |
| **Aurora** | dreamy, wide, night mode | stereo widening, slow chorus, low-pass that closes at sunset |
| **Void Whisper** | intimate, almost a whisper | breath layer, de-essed, very low reverb (for late nights) |

- Presets live in `~/.orion/voices/presets/*.json` (base voice, pitch, rate and an effects list), so new ones are
  just files.
- **Mode-aware:** the voice can follow the core's mode. Focus uses a crisper preset, Sanctuary and wind-down use
  softer and slower ones, and the existing 15%-quieter-after-sunset rule stays.
- **Voice picker:** a Life → Voice card with a preview button per preset and sliders for warmth, space and speed.
  The demo gets the same picker, using the browser's voices with Web Audio effects.

### 8.3 Your own voice ("Voice Forge")
A guided flow in Life → Voice → **Make my voice**:
1. **Consent and ownership:** you read a short consent line out loud ("This is my voice, and I allow Orion to
   speak with it on this PC"). This recording is kept with the voice, and Orion only builds a voice from the
   person who recorded the consent line.
2. **Record:** about 3–5 minutes of guided reading (Orion shows sentences chosen to cover the sounds of English),
   with live level and noise checks. Bad takes can be redone.
3. **Clean up:** trim silences, reduce noise, normalise loudness.
4. **Build:** zero-shot cloning for a first version in under a minute. Optionally, a longer local fine-tune
   overnight for higher quality.
5. **Preview and tune:** compare it against your raw recording, then pick a cosmic effects preset on top
   (or none).

Safety rails (non-negotiable):
- The voice model and recordings stay on your PC in `~/.orion/voices/mine/`. They are never uploaded, never
  sent to the agents' lab VM, and are git-ignored.
- **Your voice is never used to impersonate you to other people.** Orion will not use it in calls, voice notes
  or messages sent on your behalf. It is for Orion talking *to you* only. Anything audio that leaves the PC
  uses a clearly labelled Orion voice and discloses that it's AI.
- Only voices with a matching consent recording can be built, which blocks cloning someone else from a clip.
- A one-click **Delete my voice** wipes the model and the recordings.

### 8.4 Build order
1. `VoiceEngine` interface with sentence streaming; Kokoro or Piper for the built-in voices.
2. The effects chain and the six presets; the Life → Voice picker; mode-aware switching.
3. The demo gets the same picker with browser voices and Web Audio effects.
4. Voice Forge: consent, recording, clean-up and zero-shot clone.
5. Optional overnight fine-tune for a higher-quality personal voice.

## 9. Stardust (`$DUST`): the fuel for OrionOS

> **Parked until OrionOS 1.0 ships.** Current focus: [`ORIONOS_V1_PLAN.md`](ORIONOS_V1_PLAN.md), a public
> OrionOS that boots from a USB drive.

Stardust pays for Orion's Claude brain (no API keys or subscriptions), for **Forge** (a hotkey bar over a game
that writes, installs and reloads mods, or makes new games), and for in-game stores through a `window.orion`
SDK. The full working plan is in [`STARDUST_PLAN.md`](STARDUST_PLAN.md). The short version:

- **Fits the boardroom:** the lab asks to spend, the boardroom signs. Spending stays at autonomy level 1. To keep
  gaming smooth, you sign one **Fuel Tank** per session (for example 100 DUST for 2 hours) instead of one
  approval per prompt.
- **A Compute Gatekeeper server** holds the API keys, checks the signed tank receipt and meters each call.
- **No private keys on any Orion machine**, boardroom included.
- **Phases:**
  - **0:** off-chain prepaid **credits** plus the Forge MVP (WoW 3.3.5a addons on OpenWoW).
  - **1:** remix tree, royalties, Store SDK.
  - **2:** optional testnet token with no value.
  - **3:** a real token only after a legal opinion and a contract audit.

## Carry-over backlog from V2 and the Feature Bible

- [ ] Link GitHub, then run **Publish to GitHub.cmd**, turn on Pages, and run the first `npm run release`
      (fills in the updater URL).
- [ ] Install optional packages in the packaged exe's own Python, not just dev mode (macro recorder, PDFs,
      background removal, music widget).
- [ ] Connect an AI model (Ollama or an API key) so chat, devlogs and style-matched replies use a real model.
- [ ] Deepen the remaining sound cues (task finished, alerts, break reminder) to match the new synth rise.
- [ ] Windows-only checks: clipboard alchemy, macro recorder, pause detection, music widget and quoted-path
      scripts on a real machine.

## Built so far (V3 personal pass)

These shipped in the first V3 pass. No money, no public posting.

- [x] **Pet detection (section 4):** watches Claude, Codex, Antigravity, Gemini and Cursor session folders,
      plus `POST /agent/ping` and a "probably working" fallback from file bursts in your workspaces. When
      napping, the pet says which agents it is watching.
- [x] **Sanctuary Mode:** when a break is due the core turns amber and slow, and a five-minute routine card
      unlocks the next focus block. "Skip this once" is allowed and logged.
- [x] **Sun & Moon:** set your location in Life → Sun & Moon (or Detect). After the real sunset the HUD warms,
      Orion's voice plays 15% softer and non-critical work alerts are held until morning.
- [x] **Event horizon:** drop a file on the core. Images, audio and video go to the asset pipeline; code and
      logs go to the chat for a diagnosis; documents go to the knowledge vault and Orion briefs you on them.
- [x] **Momentum, entropy and XP:** a Dyson swarm grows with focus time, commits and water; red asteroids
      stand for piled-up approvals, failed runs and late nights (click one to deal with it). XP levels unlock
      core skins (Life → Architect).
- [x] **Constellation Switcher:** Projects → Constellation. Each workspace is a star; recent commits make it
      brighter and uncommitted work gives it an amber halo. Click a star to make it the active project.
- [x] **Soundscape:** Life → Soundscape. Cosmic drone, rain or campfire, generated live. It quiets while Orion
      talks and follows the core's mode.
- [x] **Lab machine scripts:** `scripts/vm/` sets up and checks `orion-lab` and `orion-boardroom` (Appendix A).
- [x] **Guest demo:** the GitHub page now has "Try it in your browser" (`docs/app/`): the real HUD on sample
      data, with water, ideas, rules, focus, breaks and wind-down working locally in the visitor's browser.

- [x] **Phone layout:** the HUD switches to a mobile layout automatically on phones and narrow windows (`?view=mobile`
      or `?view=desktop` forces one). The core shrinks, the widgets fold into a row of chips, the panel becomes a
      full-height sheet, and Comms becomes one column.
- [x] **Guided tour (demo):** Orion talks the visitor through her features (browser voice plus captions) and opens
      each menu as she describes it. It can be paused, skipped and muted, and is offered on first visit or via
      `app/?tour=1`.

Still to do from sections 5–7: chief-of-staff triage, clip factory, cross-pollination indexer, logistics radar.
Next up after that: section 8 (cosmic voices and your own voice).

## Suggested build order for V3

0. **Machines:** set up `orion-lab` and `orion-boardroom` following Appendix A, and pass its checklist.
1. **Foundation:** agent framework, task board, audit log, kill switch and the Agents tab. No public actions yet.
2. **Boardroom:** VM client, signed decision transport, moons UI and the decision log (autonomy level 0 only).
3. **Community + CTO agents** at level 0, both proposing only.
4. **Autonomy learner** promotion rules, starting with reversible, non-public, non-money tasks.
5. **Treasury, read-only:** the three watch-only rails, the ledger and the public Priority Sheet.
6. **CFO agent:** bucket waterfall and payout *proposals*.
7. *(Parked until OrionOS 1.0 ships)* Stardust Phase 0, see `STARDUST_PLAN.md`. Steps 5–6 (treasury, CFO) are parked with it.
8. **DevOps agent:** metrics, then scaling proposals.

The pet fix (section 4) is small and independent of the rest, so it can ship first or alongside step 1.

Sections 5–7 are personal and mostly UI and daemon work with no money or public posting, so they can run
in parallel with the agent work. A sensible order (1–5 are built, see above):
1. ~~Sanctuary Mode and Sun & Moon~~
2. ~~Drag-and-drop event horizon~~
3. ~~Momentum, entropy and XP~~
4. ~~Constellation Switcher~~
5. ~~Soundscape~~
6. Chief-of-staff triage
7. Clip factory
8. Cross-pollination indexer
9. Logistics radar

---

## Appendix A — Setting up the Linux AI machines for the experiment

V3 uses **two separate Linux virtual machines** on the Windows PC. They must stay separate: if the agents and the
sign-off key lived on the same machine, an agent could approve its own work.

| VM | Name | Job | Holds |
|---|---|---|---|
| 1 | `orion-lab` | Runs the sub-agents' risky work: CTO builds and tests, DevOps scripts, untrusted contributor code | Bot tokens with minimal permissions. No wallets, no sign-off key. |
| 2 | `orion-boardroom` | The VPN-locked sign-off station where the moons are approved or rejected | The **boardroom signing key**, and nothing else of value |

Wallet private keys live on **neither** VM (they stay on a hardware wallet or your phone). Orion only ever
watches addresses.

> **Scripts:** `scripts/vm/lab-setup.sh`, `boardroom-setup.sh` and the two `verify-*.sh` checklists automate
> A.3–A.6 and A.8. See `scripts/vm/README.md`.

### A.1 What you need
- Windows 10/11 with virtualization enabled in the BIOS/UEFI (Intel VT-x or AMD-V). Check in Task Manager → Performance → CPU →
  "Virtualization: Enabled".
- **Hypervisor:**
  - **Windows Pro/Enterprise:** use Hyper-V (`Turn Windows features on or off` → Hyper-V).
  - **Windows Home:** use VirtualBox (free).
  - Don't use WSL for this: WSL shares too much with Windows to count as isolated.
- **Ubuntu Server 24.04 LTS ISO** from ubuntu.com (no desktop, smaller attack surface).
- A free **Tailscale** account (private VPN mesh), or your own WireGuard server.
- **Disk space:** about 100 GB free for the lab and 25 GB for the boardroom.

### A.2 Create the VMs
| Setting | `orion-lab` | `orion-boardroom` |
|---|---|---|
| CPUs | 4 (6+ if building the WoW core) | 2 |
| RAM | 12–16 GB | 4 GB |
| Disk | 100 GB, dynamically sized | 25 GB |
| Network | NAT / Default Switch | NAT / Default Switch |
| Clipboard & shared folders | **Off** | **Off** |

Install Ubuntu Server on each:
- Pick a minimal install.
- Create the admin user `orion-admin` with a long passphrase.
- Tick **Install OpenSSH server**.
- Skip all extra snaps.

**Take a snapshot named `clean-install`** right after the first boot.

### A.3 Base hardening (run on both VMs)
```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y unattended-upgrades ufw fail2ban curl git ca-certificates
sudo dpkg-reconfigure -plow unattended-upgrades        # automatic security patches

# firewall: nothing comes in except over the private VPN (added in A.4)
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw enable

# SSH: keys only, no root, no passwords
mkdir -p ~/.ssh && chmod 700 ~/.ssh                     # paste your PC's public key into ~/.ssh/authorized_keys
sudo sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/; s/^#\?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```
On Windows, make the key with `ssh-keygen -t ed25519` in PowerShell, then copy `~\.ssh\id_ed25519.pub` across.

### A.4 Private VPN (Tailscale)
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh --hostname orion-lab          # use orion-boardroom on the other VM
sudo ufw allow in on tailscale0                        # only VPN traffic may reach the VM
```
Install Tailscale on the Windows PC (and optionally your phone) with the same account. Then, in the Tailscale
admin console:
- **Disable key expiry** for the two VMs.
- Add an **ACL** so that:
  - the PC can reach both VMs,
  - `orion-lab` can only reach the PC's Orion daemon port (9120),
  - `orion-boardroom` can only reach the PC's daemon,
  - `orion-lab` can **never** reach `orion-boardroom`.

Example ACL:
```json
{
  "acls": [
    { "action": "accept", "src": ["tag:owner-pc"], "dst": ["tag:lab:*", "tag:boardroom:*"] },
    { "action": "accept", "src": ["tag:lab"], "dst": ["tag:owner-pc:9120"] },
    { "action": "accept", "src": ["tag:boardroom"], "dst": ["tag:owner-pc:9120"] }
  ],
  "tagOwners": { "tag:owner-pc": ["autogroup:admin"], "tag:lab": ["autogroup:admin"], "tag:boardroom": ["autogroup:admin"] }
}
```

### A.5 `orion-lab`: the agent sandbox
```bash
# a non-admin account the agents run as — no sudo
sudo adduser --disabled-password --gecos "" orion-agent

# toolchains
sudo apt install -y python3.12-venv python3-pip build-essential cmake
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs

# rootless Docker: every agent task runs in a throwaway container
sudo apt install -y uidmap dbus-user-session docker.io
sudo -iu orion-agent dockerd-rootless-setuptool.sh install
```
- **Per-task sandbox:** each CTO or DevOps task runs as
  `docker run --rm --network=<task-net> --cpus=2 --memory=4g --read-only -v task:/work`.
  It runs from a fresh git worktree, and the container is deleted when the task ends.
- **Egress allowlist:** agent containers may only reach GitHub, the package registries (npm, PyPI, crates.io),
  the model API you choose, and the PC's daemon over Tailscale. Enforce this with a Docker network plus
  `ufw`/nftables rules, or a small egress proxy such as Squid with a domain allowlist.
- **Tokens:**
  - A GitHub fine-grained token scoped to the OpenWoW repos with *Contents: read*, *Pull requests: write* and no
    admin rights. Agents can open PRs but never merge.
  - Keep it in `/home/orion-agent/.config/orion/secrets.env` with `chmod 600`.
- **Models:**
  - Run Ollama on the Windows PC (it has the GPU) and let the lab reach it over Tailscale.
  - Or install Ollama in the lab for CPU-only small models: `curl -fsSL https://ollama.com/install.sh | sh`.
- **Orion lab worker:** `git clone` the Orion repo as `orion-agent`, `python3 -m venv .venv`, install
  requirements, then run the V3 `lab_worker` service (to be built in step 1). It connects *out* to the PC
  daemon and takes tasks from the task board.
- Snapshot it: `lab-ready`.

### A.6 `orion-boardroom`: the sign-off station
```bash
sudo apt install -y python3.12-venv minisign
# boardroom signing key — generated here, never copied anywhere
mkdir -p ~/.orion-boardroom && chmod 700 ~/.orion-boardroom
minisign -G -p ~/.orion-boardroom/boardroom.pub -s ~/.orion-boardroom/boardroom.key
```
- Copy **only `boardroom.pub`** to the PC (into Orion's settings). The daemon accepts approvals only when they
  carry a valid signature from this key.
- Install the V3 boardroom client (to be built in step 2). It shows pending decisions, and each approval is
  signed locally with the key, which asks for its passphrase every time.
- Lock it down further:
  - No browser and no other apps.
  - Automatic screen lock after 5 minutes.
  - Outgoing traffic for your user only over Tailscale, so it can talk only to the PC's daemon. (A plain
    `ufw deny out` would also cut Tailscale's own connection; `scripts/vm/boardroom-setup.sh` uses a per-user
    rule instead.)
- Back up `boardroom.key` to an encrypted USB stick kept offline. If you lose it, you generate a new key pair
  and re-register the public key.
- Snapshot it: `boardroom-ready`.

### A.7 Daily use and recovery
- Start both VMs with the PC (Hyper-V: set *Automatic Start Action*; VirtualBox: use headless start from a
  scheduled task).
- To reach the boardroom, open its console window in the hypervisor, or connect over Tailscale SSH from your phone.
- **If an agent misbehaves:** use Orion's kill switch, then roll `orion-lab` back to the `lab-ready` snapshot.
  Nothing in the lab is precious, since all real work lives in git.
- Monthly: patch both VMs, rotate the GitHub token, test restoring `boardroom.key` from the USB.

### A.8 Checklist before turning on any autonomy
- [ ] From `orion-lab`, `ping orion-boardroom` **fails**.
- [ ] From `orion-lab`, `curl https://example.com` **fails** (allowlist works) and `git ls-remote` on the
      OpenWoW repo **works**.
- [ ] The GitHub token can open a PR but **cannot merge** (test on a scratch branch).
- [ ] An approval forged without the boardroom key is **rejected** by the daemon.
- [ ] No wallet private key, seed phrase or exchange login exists on either VM (`grep -ri "seed\\|mnemonic" ~` is empty).
- [ ] Snapshots `clean-install`, `lab-ready` and `boardroom-ready` exist and restore.
