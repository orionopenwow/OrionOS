# OrionOS after 1.0 — the "living Orion" feature pass

**Status: plan only. Nothing here starts until OrionOS 1.0 has shipped** (`ORIONOS_V1_PLAN.md`). Stardust stays
parked separately (`STARDUST_PLAN.md`).

The phases below put easy wins first. The theme is that Orion learns from you, shows its work as a living thing, and can rebuild itself:
1. Self-learning: teach Orion by doing a task once.
2. A login that turns into the black hole.
3. Black hole 2.0: a real 3D, gravity-bending core.
4. The agent swarm: little robots that are your coding agents.
5. Mission Control: Claude Code, Codex, Antigravity and Gemini in one place.
6. Webcam presence and hand gestures.
7. Live self-reprogramming while you use the desktop.
8. A game engineering pipeline for Benilla + VMaNGOS: one prompt to a tested, playable zone.
9. Video Scout: send Orion short videos and get transcripts, virality metrics and summaries, to find the best WoW
   addons and projects.
10. The Orion Toolkit: 25 tools that make your coding agents safer and easier to trust, plus dev, security and
    life helpers (`ORION_TOOLKIT_PLAN.md`).

Before or alongside this pass: `ORIONOS_COMPLETE_PLAN.md` (OS basics, Craft suite, self-running updates and
repair).

---

## The phases at a glance (easy wins first)

Effort: **S** = days, **M** = 1–3 weeks, **L** = a month or more. ⭐ = optional extra from the feature brainstorm.
Each phase is useful on its own. Later phases build on earlier ones, and the riskiest work comes last.

### Phase 1 — Quick wins (reuse what's already built)
| Feature | Effort | Builds on | Details |
|---|---|---|---|
| Agent swarm robots: one robot per running agent, with status bubbles | S | Coding-agent pet, `agent_watch.py` | §4 |
| Starry login screen that swirls into the black hole | M | HUD shaders, greetd | §2 |
| Compute meter: tokens and cost per model, live in the HUD, with "cheaper prompt" tips | S | Telemetry | ⭐ (the "gas meter", in dollars, not tokens) |
| Local model kept warm for offline use, indexing and quick completions | S | `ollama_manager.py` | ⭐ |
| MCP bus v1: Grimoire, runner, git, cargo, MySQL, Blender as tools any agent can call | M | Grimoire, runner | ⭐ §8.3 |
| Crash auto-healer: a crash or Rust panic → trace → reproducing test → **proposed** fix in the Queue | M | Self-healing terminal (`runner.py`) | ⭐ (never patches by itself) |
| Skill Recorder, browser first | M | Extension, `cdp_bridge.py` | §1 |
| **Video Scout:** send Orion a TikTok, YouTube or Instagram link → transcript, on-screen text, virality metrics, keyword hits and an AI summary | M | Voice stack (Whisper), OCR, Ollama, knowledge vault | §10 |

### Phase 2 — Orion learns from you
| Feature | Effort | Details |
|---|---|---|
| Skill Recorder for desktop apps (accessibility tree, not pixels) | M | §1 |
| Skill cards, variables, preview, replay with approval, self-healing selectors | M | §1 |
| Pattern spotting ("you did this 4 times, make it a skill?") | S | §1 |
| Nightly review: proposes new skills, rules and shortcuts as Queue cards | M | §1 |
| Import/export Playwright and Python scripts | S | §1 |

### Phase 3 — Fleet: your agents as a team
| Feature | Effort | Details |
|---|---|---|
| Mission Control: send Claude Code, Codex, Gemini CLI (and Antigravity if it allows) out on missions | L | §5 |
| Model router by strength: Claude as architect, a second model (e.g. Codex) as implementer/auditor | M | §8.1 ⭐ |
| Two-model review: each model checks the other's work before anything is written to the main checkout | M | ⭐ (adversarial self-correction) |
| Speculative pre-execution: candidate patches run and tested in throwaway sandboxes first | M | ⭐ |
| Swarm spawner: 5–50 parallel sub-agents, with concurrency and cost caps | M | ⭐ |
| Race mode: several agents, same task, you pick the best | S | §5 |

### Phase 4 — Event Horizon: the look
| Feature | Effort | Details |
|---|---|---|
| Black hole 2.0: 3D ray-marched core, lensing, textured disk, quality levels | L | §3 |
| Depth: flat screen edges curving into the well | M | §3 |
| Voice-reactive, mood colours, agent-busy spin | S | §3 |
| Gravity mode: warps the real desktop (needs Hyprland or Wayfire) | L | §3, optional |

### Phase 5 — Presence: webcam and gestures (opt-in)
| Feature | Effort | Details |
|---|---|---|
| Presence: lock when away, greet on return, real screen time | M | §6 |
| Hand gestures: pointer, swipe desktops, draw-a-rectangle new desktop, palm kill switch | L | §6 |

### Phase 6 — Forge World: the Benilla game pipeline
| Feature | Effort | Details |
|---|---|---|
| Spec memory: `benilla-re` + `MAP.md` as a vector + graph memory | M | §8.2 |
| Game MCP tools and the build → binary-diff → bot-playtest → two-model sign-off loop | L | §8.3–8.4 |
| **Worldbuilding toolkit** ⭐ (original content only): | L | §9 |
| · text-to-ADT terrain with erosion; automatic 4-layer splatmap painter | | |
| · procedural WMO architect (rooms, portals, collision, lighting); low-poly M2 props with rigging | | |
| · road carver (walkable grades); incremental vmap/mmap rebuilds | | |
| · quest-loop planner; creature ecology and leash-radius balancing | | |
| · lore and dialogue writer with landmark directions; skybox, fog and light composer | | |
| Voice-to-World: speak in-game ("flatten this plateau, add a gnoll camp") and watch the chunk reload | M | ⭐ |
| Zero-restart hot-swapping of tiles, models and DBC rows in Benilla | M | ⭐ |
| Endianness / struct-padding validator for generated Rust | S | ⭐ |
| Deterministic replay testing against **your own** recorded server sessions | M | ⭐ |
| Clean-room spec discipline: specs and implementation kept apart, with sources logged | S | ⭐ |
| Closed alpha: one AI-built zone, up to 50 testers, you approve every deploy | — | §8.7 |

### Phase 7 — Mod network and community (after the legal check in §8.8)
| Feature | Effort | Details |
|---|---|---|
| Content-addressed, signed mod storage and the Benilla manifest syncer | M | §8.5 |
| Sandboxed Lua/Wasm runtime for untrusted addons | M | ⭐ |
| Mod dependency resolver (conflicting spell, creature and area IDs) | M | ⭐ |
| World branching and visual merging of zone forks | L | ⭐ |
| Headless bot playtest swarm (tank/healer/DPS bots, sped up) | L | ⭐ §8.4 |
| Player heatmaps and telemetry → content proposals | M | §8.6 |
| Opt-in AI NPC brains with lore-bounded memory | M | ⭐ |
| Ephemeral test shards: spin up a VMaNGOS + Benilla shard with a modpack, on your own hardware | M | ⭐ (no tokens) |
| Time-machine world rollback per commit | M | ⭐ |
| Trailer and patch-note director: a camera agent records a flythrough and drafts notes; you approve publishing | M | ⭐ |
| "OrionOS Dev Edition" image: Rust, Bevy, VMaNGOS and agents preconfigured | M | ⭐ (the "Genesis Fork SDK") |

### Phase 8 — Living Orion
| Feature | Effort | Details |
|---|---|---|
| Live self-reprogramming: worktree → tests → ghost preview → approve → hot-swap → auto-rollback, with a protected core | L | §7 |

Phase 8 comes last because it depends on the recorder, Mission Control, the MCP tools and a mature test suite.

### Toolkit track — runs alongside the phases
The 25 tools in `ORION_TOOLKIT_PLAN.md` (summary in §11). Each step is mostly small (S) and slots in next to a phase:
| Step | Tools | Alongside | Why then |
|---|---|---|---|
| 1 | Secret Shield (18), Collision Guard (2) | Before Phase 1 | The repo is public, and several agents already share it |
| 2 | Proof Gate (3), Baton (1), One Rulebook (6) | Phase 1 | Make the agents you already use trustworthy and swappable |
| 3 | Backup with restore drills (16), DB Time Machine (12) | Phase 2 | Undo for everything before agents get more power |
| 4 | Package Reality Check (7), Injection Firewall (8), Key Proxy (9) | Before Phase 3 (Mission Control) | Close the agent-specific holes before running fleets |
| 5 | Runbook (10), Release Butler (11), Raid Night Autopilot (24) | Phase 3 | Daily-life wins |
| then | The rest (4, 5, 13–15, 17, 19–23, 25) | As needed | |

### Not adopted, or parked
| Idea | Why |
|---|---|
| All `$DUST` features: the OS wallet, royalty streaming, bounty marketplace, item minting, proof-of-playtest pay, GPU "mining", DAO voting, economy sentinel tied to tokens | Parked with Stardust (`STARDUST_PLAN.md`, appendix B) |
| Decompiling `WoW.exe` (Ghidra/IDA bridge), memory-watching the original client, sniffing official-server traffic | Breaks Blizzard's terms and risks the clean-room position. Benilla's specs must come from clean-room sources |
| Converting retail/WotLK Blizzard models and maps down to 1.12 for distribution | Redistributes Blizzard assets; at most a local, personal-use tool, never in the mod network |
| One-click scaffolder to recompile other classic games (Morrowind, Diablo II, Warcraft III, RuneScape) | Each is someone else's IP; reconsider per game, with legal advice |
| Unknown-archive auto-inferrer, DX9 shader decompiler | Useful only for proprietary assets; revisit if a legitimate original-content use appears |
| Fully autonomous patching and deploying ("zero-touch", 100% autonomy) | Public changes always need your approval (Orion's core rule) |

---

# Feature details

## 1. Self-learning toolkit

### What already exists
| Tool | File | What it learns | Limit today |
|---|---|---|---|
| Watch & learn (macro recorder) | `macro_recorder.py` | Clicks, typing, hotkeys, window switches between "watch" and "stop" | Records **screen coordinates**, so replays break if a window moves; Windows only (pynput) |
| Grimoire harvest | `harvester.py`, `grimoire.py` | Your most-used shell commands become named macros | Shell only |
| Standing rules | `memory_core.py` (D7 speed 1) | "From now on…" instructions | Text rules only |
| Reply style | `alignment_drafter.py` | How you type, from your sent messages | Comms only |
| Approval learning | `boardroom.py` autonomy learner | Which task types you trust | Per task type, not per step |
| Fine-tune export | D7 speed 2 | Approved/edited pairs as a dataset | Manual export |
| Self-evolve | `self_evolve.py` | Changes its own code on request | Off on OrionOS; no live preview |
| Browser control | `cdp_bridge.py`, extension | Reads and drives Chrome tabs | No recorder |

### New: Skill Recorder ("record a skill")
A light recorder that writes down **what** you clicked rather than pixels, so replays still work after windows
move or pages change.

**Recording**
- Start with "Orion, watch this", a hotkey, or the red record button. A small "recording" pill stays on screen
  the whole time.
- **Desktop apps:** each click is saved with the app, window title, and the element's accessibility identity
  (role + name, e.g. button "Export" in dialog "Save as"). This comes from the AT-SPI tree on Linux and UI
  Automation on Windows. The screen position is kept only as a backup.
- **Browser (the deep browser recorder):** the Orion extension records each step as a DOM action, using several
  selectors per element: the role and label, the visible text, `data-testid`, a CSS path, and coordinates as the
  last resort. It also records URL changes, form fields, file uploads, downloads and waits ("until the page has
  finished loading", "until 'Upload complete' appears").
- **Keystrokes:** typed text is saved as text per field, not as raw key presses. Shortcuts are saved as shortcuts.
- **Never recorded:**
  - password fields, credit-card fields and 2FA codes (the browser marks these fields);
  - windows of password managers, banks or login pages (the existing `SENSITIVE_TITLE` rule, extended).

  Recording pauses automatically and says so.

**After recording:** Orion turns the raw events into a readable **skill card**, for example:
> 1. Open Firefox → itch.io dashboard
> 2. Click **Upload new build**
> 3. Choose file `{build_zip}`
> 4. Type `{version}` in **Version**
> 5. Click **Save** *(asks you first: submits a form)*

- Values that change become **variables** (`{build_zip}`, `{version}`). Orion guesses which ones; you confirm.
- You name it and give it a voice trigger ("Orion, upload the build").
- It's saved in the Grimoire as a new kind of entry, `skill`, inside your encrypted vault.

**Replaying with your approval**
- **Preview:** Orion shows the steps and highlights each target on screen before doing anything.
- **Run step by step, or run all.** Any step that submits a form, sends, buys, deletes or posts **always stops for
  your approval**, every time. That follows the autonomy rules for irreversible actions.
- **Self-healing:** if a button can't be found by any selector, Orion tries a visual search (OCR or vision). If
  that's not certain either, it stops and asks you to point at it. The fix is saved into the skill.
- **"Load anything":** skills can start from a file, a URL, an app or a project, and can chain other skills.
  They can be exported as a Playwright script (browser) or a Python script, and Playwright scripts can be
  imported as skills.

**Learning on its own (opt-in)**
- **Pattern spotting:** Orion notices when you repeat the same sequence ("You've exported and uploaded a build 4
  times this week. Want me to make that a skill?"). It works from the light event log only; no screen video is
  kept.
- **Nightly review ("dreaming"):** once a day Orion looks back over the day's events, approvals and edits. It
  proposes new skills, rules and shortcuts as cards in the Queue. Nothing changes until you accept.
- **Corrections stick:** when you edit a replayed step or reject a draft, the fix is stored with that skill or
  rule.

### Build notes
- `skill_recorder.py` (daemon): event model, accessibility capture (pyatspi on Linux, uiautomation on Windows),
  evdev/pynput fallbacks.
- `recorder.js` in the extension: DOM event capture plus a selector generator (approach borrowed from
  Playwright's codegen).
- Replay engine: Playwright for the browser, plus `os_automation` with the accessibility tree for apps.
- Skill format: versioned JSON in the vault, signed by the vault key so a skill can't be altered behind your back.

---

## 2. Login: stars into the black hole

**Boot sequence**
1. **Loading screen (Plymouth):** a black screen, the Orion mark and a soft starfield. It shows only as long as
   the boot takes.
2. **Login screen, instantly:** pure black with live, slowly drifting stars (depth parallax that responds to the
   mouse). It shows the time, your picture and a single passphrase field. No taskbar, no clutter.
3. **Enter:**
   - The field, the clock and the stars get pulled into a spiral.
   - They stretch and fall into a point, and the accretion disk blooms out of it: the black hole is born.
   - The HUD fades in around it (about 1.5–2 seconds).
4. **Reduced-motion setting:** a simple fade instead.

**One passphrase:** on the USB stick, the login passphrase also unlocks the encrypted vault, using
`pam_mount`/systemd-homed instead of a separate unlock prompt at boot. The password is checked before any
animation starts.

**Build notes:** a custom greetd greeter. It's a small Tauri/WebGL page running full-screen in the `cage`
kiosk compositor, sharing the HUD's star and black-hole shaders so the hand-off from greeter to HUD is seamless.

---

## 3. Black hole 2.0: a real 3D core

### The look
- **A real 3D model:** a ray-marched black hole shader with:
  - **gravitational lensing** (the starfield and disk bend around the hole);
  - a textured, animated **accretion disk** with Doppler brightening on one side;
  - a photon ring;
  - a dark event horizon.

  Built in three.js/WebGL2 (WebGPU when available), replacing today's layered 2D images.
- **Depth:** the edges of the screen stay flat. Toward the centre, space visibly curves and falls inward, so the
  screen feels like the rim of a gravity well.
- **Alive:**
  - It reacts to your voice while you talk.
  - Its colour changes with the mode: violet idle, cyan focus, amber Sanctuary, warm at sunset, and the skins
    from XP.
  - It spins faster when agents are busy.
- **Quality levels:** Ultra (full ray-march), High (pre-baked lensing), and Low/no GPU (today's 2D core).

### Warping the real desktop (optional "Gravity mode")
The black hole can bend the **actual windows** around it: windows near the core stretch and lens; dragging a
window close makes it fall in and minimise.
- This has to happen inside the **compositor**, because an overlay can't bend other apps' pixels.
- **Hyprland** supports full-screen shaders and custom effects; **Wayfire** supports plugins in the same way.
  **SwayFX doesn't.**
- **Decision needed before 1.0's compositor is locked:** if Gravity mode matters, pick Hyprland (or Wayfire) for
  1.0 instead of SwayFX, or plan a compositor switch in this pass.
- Gravity mode is off by default (it costs GPU), and off automatically on battery or with reduced motion.

### Talk to it
Click or look at the core (with the webcam, section 6) and speak. The disk ripples as Orion answers.

---

## 4. The agent swarm (robots around the core)
Each running agent becomes a small robot orbiting the black hole, building on today's coding-agent pet and
`agent_watch.py`, which already detects Claude, Codex, Antigravity, Gemini and Cursor.
- **One robot per agent or mission**, with a colour per tool and a name ("Claude · Orion repo").
- **Status bubbles:** what it's doing right now, in plain words ("Running the tests", "Waiting for your OK").
- **Click a robot:** see its live log, the files it changed, the cost so far, and Pause / Stop / Open.
- **Finished work** flies down to the Queue as a card (a pull request to review).
- **Stuck or failed:** the robot blinks amber and says why.

## 5. Mission Control: all your coding agents in one place
One window (also a HUD tab) to **send agents out to build things**:
- **New mission:** describe the project or feature, pick the repo, pick an agent (Claude Code, Codex, Antigravity,
  Gemini CLI) or let Orion pick, and set a budget and time limit.
- **Each mission runs in its own git worktree inside a sandbox container**, the existing rootless Podman
  sandboxes with the agent user. Agents can't touch your main checkout or each other's work.
- **How each tool is driven** (all using your own subscriptions or API keys, through each tool's official
  headless mode):
  - **Claude Code:** headless mode (`claude -p`) or the Claude Agent SDK.
  - **Codex:** `codex exec`.
  - **Gemini CLI:** non-interactive mode.
  - **Antigravity:** through its command-line or API surface if it offers one. Otherwise watch-only (the robot
    shows its activity) and Orion uses Guide Mode to drive it with you.
- **One board:** columns for queued, running, needs you, review and done. Live logs, diffs and cost per mission.
- **Approval rules:**
  - Agents open pull requests; merging to `main` needs your signature.
  - Missions can't spend money, post publicly or change secrets.
  - Several agents can tackle the same task, and you pick the best result ("race mode").
- **Templates:** "New website", "New game prototype", "Fix this bug", "Add a feature to Orion".

## 6. Webcam: presence and hand gestures
**Off by default.** One tap on the camera icon in the HUD (or a hotkey) turns it on or off, silently: no sound,
no pop-up.
- Processing is 100% local (MediaPipe hand and face landmarks).
- Frames are never saved or sent anywhere.
- A visible camera indicator stays on in the HUD, with a switch in the privacy dashboard.

**Presence**
- Locks when you walk away; wakes and greets you when you sit down.
- Pauses media when you look away (optional).
- Counts real screen time for the focus arc.

**Hand gestures**
| Gesture | Does |
|---|---|
| Point + pinch | Move the pointer, click |
| Open palm, hold | Pause Orion / stop all agents (a gesture kill switch) |
| Swipe left / right | Switch desktop |
| Draw a rectangle in the air | Create a new desktop |
| Grab and throw | Move a window to another desktop, or "into the black hole" to minimise it |
| Two-hand spread / pinch | Zoom the HUD, open or close the panel |
| Thumbs up | Approve the current *small* card (never boardroom decisions, never money) |

Gestures work only while a hand is held up in "gesture zone" for half a second, to avoid accidents. There's a
calibration screen and an off switch, and a CPU budget so it doesn't slow games.

## 7. Orion reprograms itself, live
Ask out loud while you work ("Orion, make the robots bigger and add a gesture to mute") and Orion changes its own
code with Claude Code, without stopping your session. This builds on today's Talk-to-Develop (`self_evolve.py`).

How it stays safe:
1. **Sandboxed change:** the edit happens in a separate git worktree of Orion, never in the running copy. The
   OrionOS system image stays read-only. Self-made changes live in an **Orion layer** in your vault.
2. **Build and test:** the build runs, along with Orion's test suite and a quick visual check (screenshots of the
   HUD compared before and after).
3. **Preview:** a "ghost" copy of the HUD shows the change side by side, or in a corner, with the reason and the
   diff.
4. **You approve:** the change hot-swaps in (UI changes reload in place; daemon changes restart in about a
   second).
5. **Rollback:** "Orion, undo that", or automatic if Orion fails to start or a health check fails within 2
   minutes.
6. **Protected core:** some code can't be changed this way without your phone or boardroom signature, and never
   automatically:
   - the approval system and the signer;
   - the firewall;
   - the recorder's privacy rules;
   - the self-modification gate itself.
7. Self-made changes can be shared upstream as a pull request to the Orion repo, if you choose.

How it's built (Phase 8): `apps/daemon/self_program.py` runs the flow, `apps/daemon/orion_layer.py` keeps the swap
history, the Orion layer and the start-up guard, and `apps/daemon/protected_core.json` + `protected_core.py` hold the
protected list. On a source checkout the change is tested in a git worktree (branch `orion-self/<id>`) and written
into the checkout when you approve. On the OrionOS image the layer lives in `~/.orion/orion-layer/`: each approved
change is a new full copy of the daemon (`gens/<n>/`), `current` says which one runs, and the daemon re-runs itself
from it at start; undo points `current` back. On the image only the brain can change itself this way; screen
changes need the source checkout, since the image's HUD has its screens built in. The ghost preview is
`apps/desktop/src/components/selfprog/GhostPreview.tsx`. Protected-core changes become a boardroom decision that
only your phone's fingerprint key or the boardroom key can sign; levels are read like `orion-self-gate` (the admin
file wins).

## 8. Game engineering pipeline (Benilla + VMaNGOS)

An Orion pipeline that builds **playable, tested custom zones** for your Benilla client (a Rust 1.12.1 client)
and a VMaNGOS staging server, from a single prompt. It reuses Mission Control (section 5), the sandboxes and the
signer. The money and token parts of the original idea (compute escrow, royalty splitter, in-game asset
minting, burns) moved to the parked Stardust plan, appendix B.

### 8.1 Orchestrator: route work by model strength
| Role | Model | Owns |
|---|---|---|
| **Lead Architect & Creative Director** | Claude (Opus / Fable) | Zone lore, quest flow, repo conventions, architecture plans, aesthetic and lore review |
| **High-Speed Implementer & Systems Auditor** | A second coding agent (Codex, or a local model for routine work) | Rust binary serializers, 3D mesh/collision math, deep bug and reliability passes |

- Routing rules live in a versioned `routing.json`: task type → model, with a fallback model and a cost cap per
  mission.
- Any agent Mission Control supports can fill either role; the router just records which one is best at what.

### 8.2 Ground-truth memory
- Ingest `benilla-re` (the reverse-engineering specs) and `docs/MAP.md` into a persistent **vector + graph
  memory**: every structure, field and byte offset of 1.12.1 **ADT, WDT, WMO, M2 and DBC**, linked to the code
  that reads and writes it.
- Both models query it before writing format code, and every generated parser cites the spec section it
  implements.
- Re-index automatically when the spec repo changes.

### 8.3 MCP tool layer
Expose these as MCP tools so agents can run them, read the errors and fix their own work. Every tool runs inside
the mission's sandbox container.
- The Rust ADT/DBC compiler.
- `cargo check` / `cargo test`.
- A binary diff against stock files.
- Read-only and staging-write access to the VMaNGOS SQL database.
- The headless client launcher.

Tools that can touch anything outside staging (the production DB, public servers, releases) are not exposed to
agents. Those go through approval.

### 8.4 The build-and-verify loop (one prompt → a playable zone)
1. **Reverse-engineering and synthesis:** an agent reads the target structure from `benilla-re`. The implementer agent writes the
   Rust parser/serializer and the procedural meshes (M2/WMO/ADT chunks).
2. **Compatibility check:** `cargo check`, the tests, and a binary diff/round-trip against stock 1.12.1 (build 5875)
   structures. Any mismatch fails the build.
3. **Headless bot playtesting:**
   - After every build, Orion starts a headless Benilla client and the **staging** VMaNGOS server.
   - AI bots log in and walk the new tiles along the painted paths.
   - They check vmaps/mmaps line of sight and pathing, and run quest pickup/turn-in loops.
   - They flag Z-axis clipping, falling through the world, and pulls that chain aggro into deaths.
   - Each run produces a report with screenshots and coordinates.
4. **Two-model sign-off:** The auditor agent runs a reliability pass on the zone and SQL migrations; Claude reviews cohesion
   and lore.
5. **You promote it.** Promotion to anything players can load is a signed approval (phone or boardroom). Under
   the autonomy rules, publishing stays at "always asks". The original "zero-touch / 100% autonomous" stage is
   not adopted.

### 8.5 Mod network (distribution)
- **Content-addressed delta patches:** custom `.adt/.wmo/.m2`, DBC patches and Lua 1.12 addons
  (`benilla-config/AddOns/`), indexed by hash. Start with a plain CDN or object storage plus a signed manifest.
  IPFS/Arweave are optional later and aren't needed to start.
- **Benilla manifest syncer:** on start-up the client fetches the signed manifest, downloads approved patches and
  mounts them as an overlay before login. Every file is verified against its hash and the manifest signature.
- **Security gate:** every submitted or AI-generated patch is scanned in an isolated container. That means Lua
  sandbox and static checks, fuzzing the binary chunks against the parsers (to catch memory-corruption
  payloads), and a size/structure sanity check.
- **No Blizzard assets in the network:** only original content and data patches, as Benilla intends.

### 8.6 Day-2 loop (with you in it)
- **Telemetry:** from the staging and live servers, find where players die, which quests they abandon, and where
  high-level players run out of content.
- **Proposals:** Claude drafts the next content patch or balance fix; the implementer builds it; the bots test it.
- **You approve** each deploy. A weekly "patch day" card bundles the week's verified changes.

### 8.7 Staged launch (revised)
| Stage | Target | Autonomy | Gate |
|---|---|---|---|
| **Closed alpha ("Genesis Sandbox")** | You + up to 50 testers, one AI-built zone on Benilla + VMaNGOS | AI builds terrain and quests; you approve every merge and deploy | No client crashes over 48 h; pathfinding verified under load |
| **Open beta, mod network live** | Public launcher, registry live | Bots auto-verify community/AI mods and **queue** them; you approve publishing | 10+ external mods/zones hot-loaded cleanly; security gate catches a planted test exploit |
| **Steady state** | Continuous content proposals from telemetry | Proposing and testing are automatic; deploys need your approval | — |

### 8.8 Legal check before any public stage
Benilla and VMaNGOS target World of Warcraft 1.12.1. Distributing content for another company's game is legally
sensitive, and private-server projects that **made money** have been sued. Before any public stage:
- keep the mod network free;
- distribute no Blizzard files;
- get legal advice on the public launcher and the mod registry.

**Nothing in this section is combined with Stardust or any paid feature** (see Stardust plan, appendix B).

---

## 9. Worldbuilding toolkit (Phase 6 details)

Each tool works on **original content only**, writes into the mission's sandbox, and is checked by the
bot-playtest loop before you see it.

| Tool | What it does |
|---|---|
| Terrain synthesizer | A zone brief becomes a WDT grid and ADT heightmaps (MCVT) with hydraulic erosion |
| Splatmap painter | Paints MCLY/MCAL alpha layers from slope, height and moisture, within the 4-textures-per-chunk limit |
| WMO architect | Multi-room inns, dungeons and forts with valid portals, collision and interior lighting groups |
| M2 prop maker | Stylised low-poly props, weapons and creatures, with bones, `.skin` and `.blp` textures (hardest; start with static props) |
| Road carver | A* paths between quest hubs at walkable grades (< 30°), flattened and edge-blended |
| Navmesh rebuild | Incremental vmap/mmap rebuilds when something moves |
| Quest-loop planner | Lays out hubs, objectives and breadcrumbs as loops without dead-end backtracking |
| Ecology balancer | Spawn density, aggro radii, patrols and respawn timers per subzone |
| Lore and dialogue writer | Quest and gossip text with landmark-based directions, checked against zone lore |
| Atmosphere composer | Fog, water tint, sky gradients, Light.dbc and zone music per subzone |

## 10. Video Scout (Phase 1 details)

**What it's for:** send Orion TikTok, YouTube (including Shorts) and Instagram Reels links, and it listens,
transcribes and summarises them. The first use is scanning WoW videos to find the best addons and the newest
projects, using what's said, what's shown on screen, and how fast each video is spreading.

**Built almost entirely from open-source parts, run locally.** About 300–400 lines of Orion glue, no rewrites.

| Step | Open-source tool | What it gives |
|---|---|---|
| Fetch | **yt-dlp** (supports TikTok, YouTube and Instagram) | Audio-only download, plus a metadata JSON: views, likes, comments, reposts, upload date, creator follower count (where available), hashtags, description, duration |
| Captions shortcut | yt-dlp `--write-subs --write-auto-subs` | YouTube's own captions when they exist, so no transcription is needed |
| Transcribe | **faster-whisper** (the same one as the 1.0 voice stack), or whisper.cpp on CPU | Text with timestamps; runs on the PC, offline |
| On-screen text | ffmpeg (a frame every 1–2 s) → **Tesseract** (already in Orion) | Addon names and links shown as captions or overlays, which TikToks often use instead of saying them |
| Comments (optional) | yt-dlp `--write-comments` where the site supports it | "What's the addon called?" replies, which often name it |
| Match keywords | **RapidFuzz** against your keyword list and an addon catalogue | Fuzzy hits ("pfquest", "pfQuest", "PF quest") with timestamps |
| Summarise | **Ollama** (local) by default, or Claude through Orion's normal router | A summary plus structured JSON: addons, projects, links, claims, a "worth checking" verdict |
| Store and search | Orion's knowledge vault + SQLite | Transcripts become searchable memory; metrics are kept over time |

**How you send videos**
- Paste a link in Chat, or drop it on the black hole.
- Share from your phone straight to Orion Mobile (the Android share sheet).
- Send a link to Orion's Telegram bot.
- **Batch:** a file of links, a YouTube channel, playlist or search (`ytsearch`), or a creator's TikTok page.
- **While you browse:** the Orion extension can quietly collect video links from TikTok pages you're already
  scrolling, and offer to scout them. It never scrolls or searches TikTok by itself.

**What comes back:** a Video Scout card per video:
- A 3–5 bullet summary and the addons/projects mentioned. Each one links to the moment it's mentioned and to its
  download page if found.
- **Virality:**
  - views per day since upload;
  - engagement rate ((likes + comments + shares) ÷ views);
  - views compared with the creator's usual video and follower count;
  - **velocity:** Orion re-checks saved videos after 24 h and 72 h to see how fast they're still growing.
- Keyword hits, highlighted in the transcript.

A **leaderboard** in the Projects tab ("Top WoW addons this week") ranks addons by how many videos mention them,
weighted by each video's virality. It shows what's rising, what's new, and which creators keep finding good ones.
Results can be exported as Markdown/CSV or sent to a project's `IDEAS.md` (for example OpenWoW or Benilla).

**WoW setup out of the box**
- A starter keyword list: addon, weakaura, UI, pfUI, questie, classic, vanilla, 1.12, turtle, private server, macro,
  "link in bio", github, curseforge, wago.
- An addon catalogue built from public addon lists (GitHub repos with a WoW `.toc` file, plus Wago and CurseForge
  names where their public pages allow), refreshed weekly, so fuzzy matching knows real addon names.
- An editable prompt template: "From this transcript and on-screen text, list every WoW addon or project, what it
  does, which game version, and whether the creator recommends it."

**Rules**
- **Read-only:** Video Scout never posts, likes, follows or comments.
- **Media isn't kept:** downloaded audio and frames are deleted after processing. Only the transcript, metadata and
  summary are kept. Nothing is re-uploaded or shared.
- **Gentle on the sites:** rate-limited (a few videos a minute), cached, and it respects login walls. For Instagram
  or age-gated videos, yt-dlp can use your own browser's cookies, on your PC only. If a platform blocks it, the
  card says so.
- Downloading may go against a platform's terms even for personal research. Keep it personal and low-volume. For
  large-scale TikTok research, TikTok's official Research API (for approved researchers) is the clean route.
- Batch runs and the nightly re-checks are scheduled jobs; you can pause them any time.

**Build steps** (when this phase starts):
1. `video_scout.py` in the daemon: a yt-dlp wrapper, faster-whisper, frame OCR, RapidFuzz matching, an LLM
   summary, and SQLite tables `videos`, `video_metrics` and `mentions`.
2. A `scout <url>` deterministic command plus link detection in Chat/Telegram/drops.
3. The Scout card, the leaderboard UI, and the phone share target.
4. Scheduled re-checks for velocity and the weekly catalogue refresh.

This is independent of the rest of OrionOS, so it could be pulled forward into the Windows desktop app at any
time.

## 11. The Orion Toolkit (track details)

Full plan: `ORION_TOOLKIT_PLAN.md`. Every tool works by itself, by one phrase, or by one tap, keeps your approval
rules, reports in plain words, and is also an MCP tool on the Phase 1 bus.
- **A. AI coding:** Baton (hand work between agents), Collision Guard, Proof Gate, AI Blame, Context Pack, One
  Rulebook, Package Reality Check, Injection Firewall, Key Proxy.
- **B. Dev and ops:** Project Runbook (just + mise), Release Butler, DB Time Machine, Perf Sentinel, Log Lens, Clean
  Box, Backup with restore drills, Dependency Autopilot.
- **C. Security (your own machines only):** Secret Shield, Supply-Chain Scan, Exposure Map, Host Health, Tripwires,
  Share Scrubber.
- **D. Life:** Raid Night Autopilot, Promise Keeper (never messages anyone for you).

**Where it meets the rest of this plan:**
- Proof Gate is the check behind Phase 3's two-model review and race mode; Baton and Collision Guard run inside
  Mission Control (§5), and Collision Guard draws the red line between robots (§4).
- Context Pack feeds the compute meter's savings (Phase 1).
- DB Time Machine and Clean Box are the base for Phase 7's world rollback and test shards.
- Key Proxy and Injection Firewall must exist before Phase 8 (self-reprogramming).
- Release Butler wraps the existing image workflow and minisign signing.

## Risks

| Risk | What we do |
|---|---|
| A recorder looks like a keylogger | Visible indicator, start/stop only, password and payment fields never captured, recordings encrypted, privacy dashboard |
| Replays clicking the wrong thing | Accessibility identity first, several selectors, preview, approval for any irreversible step, stop when unsure |
| Always-on camera trust | Local-only, no frames stored, visible indicator, hardware LED, off by default |
| GPU cost of the 3D core and Gravity mode | Quality levels, battery-aware, 2D fallback |
| Agent tools changing their headless interfaces or terms | Adapters per tool, pinned versions, users' own accounts and keys only |
| Self-modification breaking Orion | Worktree + tests + ghost preview + automatic rollback + protected core |
| Legal exposure of a WoW-based mod network | No Blizzard assets, nothing paid, legal advice before any public stage |
| Video downloads blocked or against a platform's terms | Personal, low-volume use; delete media after processing; official APIs where offered; the card reports blocks plainly |
| Malicious community mods | Hash-verified signed manifest, container scanning and fuzzing, approval to publish |

## Decisions

| Question | Decision |
|---|---|
| Webcam | **Off by default**; one tap turns it on or off silently. The small camera indicator stays visible while it's on. |
| Skills | **Private for now.** No public skill library. |
| Astra | **Dropped.** The router pairs Claude with Codex or another supported agent. |
| Boot screen | **Keep it clean:** the art update's boot sequence, no hardware-check text. |
| Compositor / Gravity mode | **Open.** Keep Sway for 1.0; put compositor calls behind an adapter so Hyprland can be tried later; decide at Phase 4. |
