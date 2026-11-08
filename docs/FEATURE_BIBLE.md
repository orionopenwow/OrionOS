# Orion Feature Bible — what's built and how to use it

This covers the Feature Bible and Day-One Preload Kit. Most of it works by voice, by typing in Chat, or from the
**Projects / Tools / Life** tabs. Deterministic commands run first, before the LLM, so they're instant and free.
Anything Orion doesn't recognise goes to the normal assistant.

## Day-One Preload Kit (`~/.orion/preload/`)

On first start Orion creates five files from your seed bundle. Edit any of them and Orion reloads within 30 s, with no restart.

| File | What it drives |
|---|---|
| `identity_and_rules.md` | Each bullet becomes a **core rule** that's injected into every prompt and reply draft |
| `workspace_manifest.json` | Projects (path, `start_cmd`, ports), plus custom **Battle Stations** |
| `grimoire_scripts.json` | Named commands with voice triggers (`kill_stuck_ports`, `git_quick_checkpoint`, …) |
| `comms_routing.json` | VIP contacts, muted/low-priority threads, digest times, `discord.devlog_channel_id` |
| `conditioning_deck.json` | Movement/breathwork routines, focus length (90), warn mark (60), hydration goal, bedtime |

**Harvest** (Projects tab, or say *"harvest my projects"*) does three things:
- Scans your project folders and adds any new workspaces.
- Turns your most-used PowerShell/bash history into Grimoire macros. History lines that look like secrets are skipped, and harvested macros always ask before running.
- Indexes READMEs and docs into memory.

To choose which folders it scans, set `HARVEST_ROOTS` (separated by `;`).

## D1 · Living Grimoire

- **Run by voice:** *"clear stuck ports"*, *"run the script that converts GLB to M2"*. Exact triggers run instantly; anything else goes through a semantic search. Ambiguous matches don't run.
- **Script vault:** drop `.bat .ps1 .py .js .sh .user.js` files into `~/.orion/grimoire/`. A script whose *contents* include destructive commands (`rd /s`, `Remove-Item -Recurse`, `git push --force`…) goes to the **Queue** for approval.
- **Watch & learn:** say *"watch what I do next"* … *"stop watching"*. A save card asks for a name and voice trigger. Recording happens only between start and stop, skips password/login windows, and stops itself after 10 minutes (it keeps what it recorded). Needs `pynput` (`npm run setup:daemon:full`).
- **Clipboard alchemy** (Windows): copy JSON, a stack trace, SQL or code and a pill offers *→ TypeScript*, *Diagnose*, *Explain*, *Save snippet*. Clipboard text stays in memory only, unless you click an action.
- **Self-healing terminal:** every run streams into **Tools → Run console**. On failure Orion diagnoses it (port in use, missing package, missing script…) and offers **Fix & Restart**. Destructive fixes are never suggested, and a destructive command can't be re-run with one click.
- **Battle Stations:** *"switch to OpenWoW mode"* opens the IDE (`ORION_IDE`, default `code`), starts the dev servers, closes the listed apps and loads that project's memories and git status. Destructive commands in a station are skipped. Weak or ambiguous names fall through to the assistant.

## D2 · Comms

- **VIP satellites:** unread messages from VIP contacts orbit the core. Click one to open the thread.
- **Mute** a group (bell icon in Comms) and it stops pinging you. A three-bullet **digest** arrives at the `digest_times` (12:00 / 18:00), or on demand with the **Digest** button.
- **Voice-to-reply:** *"Tell Alex on Telegram we're recording at 6"*. Orion reads it back and shows a card. Nothing is sent until you say *"send it"* or click **Send it**.

## D3 · Deep work

- **Multi-repo radar** (Projects tab): branch, uncommitted files and the last commit for every workspace.
- **Devlog compiler:** *"write the devlog"*, or it runs automatically after a 45-min-plus session with 3 or more commits. Orion drafts a community post plus a technical changelog and puts it in the **Queue**. On approval it posts to `discord.devlog_channel_id`.
- **Cross-project harvester:** *"grab the reconnect hook from orion hub and adapt it for openwow"*. Orion reads the source repo (read-only), writes a patch for the target repo and puts it in the Queue.

## D4 · Creative

- **Idea catcher:** *"log a realm idea: low-gravity CTF"* appends to that project's `IDEAS.md`.
- **Asset pills:** drop an image, audio/video file or `.glb` on the window to get **Remove Background** (`rembg`), **Crop to Circular Discord Icon**, **Compress for Web**, **Normalize Loudness** (`ffmpeg`) and **Convert GLB → M2**. The M2 conversion is your Grimoire command with `{input}`/`{output}`. Results go to `~/.orion/assets/out`.
- **Podcast producer:**
  - *"start podcast mode"* starts the topic timer.
  - While you talk, silent fact cards appear from your docs.
  - Say *"mark that"* to save a highlight; *"next topic: …"* starts a new topic.
  - *"stop podcast mode"* saves markdown notes.

## D5 · Health

- **Focus arc in three bands:** cyan 0–60 min, purple 60–90, amber when a break is due.
- **Pause-aware:** the check-in waits for a natural pause (12 s with no input, or a window switch) and never interrupts mid-sentence. After 20 min it shows up regardless.
- **Conditioning roulette:** each check-in suggests the routine category you've done least this week.
- **Breathwork routines** open a full-screen vortex paced 4-4-4-4.
- **Hydration:** *"I drank a glass of water"*, or use the **+ Glass** button.
- **Wind-down:** 90 min before bedtime the HUD warms and dims, and Orion tells you once.

## D6 · Life logistics

- **Runway / Exp bar** (Life tab): balances, monthly bills and supporter revenue give you runway in months and progress on your milestones. An Open Collective balance is pulled from the public API if you set your slug. Everything else is just numbers you type, stored locally.
- **Offline knowledge vault:** drop manuals, wikis and PDFs into `~/.orion/vault/` (PDFs need `pypdf`). They're indexed every 10 min, or when you click **Sync**.

## D7 · Two-speed training

- **Speed 1 (instant):** *"from now on keep announcements casual"*, or add a rule in **Life → Standing rules**. Learned rules are stored in LanceDB `user_rules`; the most relevant five, plus every core rule, are injected into each prompt and reply draft. Questions and everyday sentences ("remember to call mom") are *not* saved as rules.
- **Speed 2 (consolidation):** Consolidate Memories exports your approved and edited pairs as a fine-tune `.jsonl`. This part is unchanged from V2.

## Safety rails added in this pass

- Browser extensions can't call any HTTP endpoint except `GET /health`. Previously any installed extension could approve queue items over HTTP.
- On Windows, commands run through `create_subprocess_shell`, so quoted paths (`python "C:\My Scripts\x.py"`) work.
- The secret filter catches `sk-ant-…`, `sk-proj-…`, `glpat-`, `AIza…`, URL credentials, `-u user:pass` and high-entropy tokens.
- Vault scanning, doc harvesting, PDF extraction, file indexing and uploads run off the event loop. Uploads are capped at 300 MB even when the request has no content length.

## Optional extras

`npm run setup:daemon:full` installs:
- `pynput` for the macro recorder.
- `pypdf` for PDFs in the vault.
- `rembg` for background removal.

The audio/video pills need `ffmpeg` on PATH.
