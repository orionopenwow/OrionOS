# Orion Toolkit: 25 tools for AI coding, development, security and life (plan, not started)

Written 2026-10-16. Plan only. Nothing is built, and nothing in the repo has changed. It's meant to come after (or
alongside) the Art Update, once the other conversation's repo work is finished.

**Where it sits:** this is a track inside the after-1.0 pass (`ORIONOS_AFTER_1_0_PLAN.md`, "Toolkit track" and
§11), with its first tools pulled forward next to Phase 1.

**What it's built around**, based on how you work:
- You run several AI coding agents at once: Claude Code, Codex, Gemini CLI, Antigravity and Cursor.
- You keep a public GitHub repo (Orion).
- Your Rust game projects are OpenWoW, Benilla and the VMaNGOS staging server, plus MySQL and Blender.
- Your machines are a Windows PC, VirtualBox VMs, a laptop and the USB stick.
- Approvals happen on your phone.
- You want plain words and as much automation as possible.

It also leaves out everything already built or planned (Phase 1 tools, Mission Control, race mode, two-model
review and so on). These 25 tools fill the gaps between those.

**Labels:**
- **Adopt** means a proven open-source tool, wired into Orion so it's one phrase or zero effort.
- **Build** means Orion glue around existing parts.
- **New** means I don't know of a tool that does this today; it would be Orion's own.

**Effort:** S = days, M = 1–3 weeks, L = a month or more.

## The rules every tool follows

- **Three ways to use it, simplest first:**
  1. It happens by itself.
  2. One phrase: "Orion, …".
  3. One tap on the phone or a button in the HUD.

  Settings exist, but nothing needs them to start.
- **Your approval rules hold.** Anything public, irreversible, involving money or secrets, or touching the
  production server waits for your fingerprint. Agents never get more power through these tools.
- **Each tool reports in plain words:** what it found, what it did, and what it wants you to decide.
- **Every tool is an MCP tool too** (on the Phase 1 MCP bus), so your coding agents can use it, within the same
  rules.

---

## A. AI coding: making your agents safer, cheaper and easier to trust

### 1. Baton: hand work from one agent to another (New, M)
When an agent stops (out of usage, stuck, or you want a second opinion), Orion writes a hand-off: the goal,
decisions made, files changed, what's left, failing tests and the exact next step. Another agent, from any
vendor, picks it up and continues without you re-explaining.
- **Use:** "Orion, hand this to Codex." It also offers this automatically when an agent hits its limit.
- **Built from:** agent_watch logs, git diff, the test runner, and the Mission Control adapters.

### 2. Collision Guard: stop agents (and you) stepping on each other (New, S)
This is exactly the situation tonight, with another conversation in the same repo.
- Orion keeps a live map of who is editing which files and branches: each agent, each conversation, and you.
- When two would touch the same file, it warns before it happens. It suggests a separate worktree, and an order
  for merging.
- **Use:** automatic, plus a red line between the robots that are about to collide. "Orion, is it safe to start?"
  gives a yes, or the list of conflicts.
- **Built from:** agent_watch, git worktrees, file watchers.

### 3. Proof Gate: "done" means proven (New, S)
When an agent says it's finished, Orion checks independently before its robot turns green. That means the build,
the tests, a screenshot for UI work, and a check that the files it claimed to change really changed.
- If anything is off, the robot stays amber with the reason ("Said tests pass; 2 fail").
- **Use:** automatic for every mission. "Orion, prove it" on any agent's claim.
- **Built from:** runner, test detection from the crash healer, and headless Chromium screenshots.

### 4. AI Blame: who wrote this line, and why (New, M)
A flight recorder for agent work. Every change is linked to the agent, the request, the conversation, the cost and
the tests that ran.
- **Use:** point at a line in VS Code, or ask "Orion, why did this change?". You get "Codex, Tuesday, while fixing
  the loot crash; your request was …; it cost $0.40; tests passed."
- **Built from:** git notes, agent_watch, the compute meter, and a VS Code companion command.

### 5. Context Pack: the smallest brief that does the job (Build, M)
Before an agent starts, Orion assembles just what it needs, within a token budget:
- the relevant files;
- the project's rules;
- recent errors;
- the decisions from your plan docs.

It learns which files agents actually open, so later packs get smaller.
- **Use:** automatic for missions. "Orion, brief Claude on the vault bug" puts the pack in your clipboard.
- **Built from:** a repo map (the approach popularised by aider and repomix), the knowledge vault, and the compute
  meter (which shows the savings).

### 6. One Rulebook: the same instructions for every agent (Build, S)
You write your project rules once. Orion keeps CLAUDE.md, AGENTS.md (Codex and others), GEMINI.md and Cursor's
rules file in sync, and flags rules an agent keeps breaking.
- **Use:** "Orion, add a rule: never push without asking." It updates all of them.
- **Built from:** templates plus a small sync job, and Proof Gate's findings.

### 7. Package Reality Check: catch made-up and look-alike packages (New, S)
AI models sometimes suggest packages that don't exist. Attackers register those names, or near-misses of popular
ones.
- Orion checks every new dependency an agent adds. It looks at whether the package exists, how old it is, how many
  downloads it has, who publishes it, and whether the name is one letter off a popular package.
- Suspicious ones are blocked until you approve them.
- **Use:** automatic on install commands and lockfile changes (pip, npm, cargo).
- **Built from:** the registries' public APIs, and osv-scanner data.

### 8. Injection Firewall: agents don't take orders from web pages (New, M)
Anything an agent reads from outside (web pages, GitHub issues, READMEs, emails, video transcripts) is marked
"untrusted".
- If an agent then tries a risky tool call that matches instructions found in that text, Orion stops it and shows
  you the source. An example is "send the .env file to this address".
- **Use:** automatic. Blocked attempts show up as cards with the quoted text.
- **Built from:** the MCP bus (every tool call passes through it), and taint labels on content.

### 9. Key Proxy: agents never hold your real keys (New, M)
- Agents get placeholders. When a request leaves the machine, Orion's local proxy swaps in the real key, for that
  mission only, for the allowed sites only, and within a spending cap.
- A leaked agent log or transcript then contains nothing usable.
- **Use:** automatic once a key is added in Settings. "Orion, which agents used my OpenAI key today?"
- **Built from:** sops/age for storage and a small local HTTPS proxy. The idea is similar to `op run` in
  1Password's CLI, but scoped per mission.

---

## B. Development and operations: less typing, fewer surprises

### 10. Project Runbook: one command per project (Adopt, S)
- Orion learns how each project starts, tests, builds and deploys. It writes that into a `justfile`, with tool
  versions pinned by `mise`.
- "Run OpenWoW" then means the same thing on the PC, the laptop, a VM and inside agent sandboxes.
- **Use:** "Orion, run OpenWoW", "Orion, test Benilla", or a project tile on the phone.
- **Built from:** just, mise, and Grimoire's harvest of your most-used commands.

### 11. Release Butler: one phrase to a signed release (Adopt, M)
"Orion, release Orion 1.1" runs the whole release, and the actual publish waits for your fingerprint:
- writes the changelog from commits (git-cliff);
- bumps versions;
- runs the full tests;
- builds the OrionOS image;
- signs it (minisign);
- drafts the GitHub release and the patch notes.
- **Built from:** git-cliff, minisign, gh, and the existing image workflow.

### 12. DB Time Machine: undo for MySQL (New, M)
- Before any migration, agent write or bulk change to the staging database, Orion takes a snapshot. Afterwards it
  shows exactly which rows changed, in plain words.
- One tap rolls back. It also works per table ("put the loot tables back to last night").
- **Use:** automatic around MCP database writes and migrations. "Orion, what changed in the world DB today?"
- **Built from:** consistent mysqldump/xtrabackup snapshots and a row-diff. Dolt (a versioned SQL database) is an
  option for staging copies.

### 13. Perf Sentinel: notice when things get slower (Build, S)
- Benchmarks run on every commit, and the trend lines are kept. Examples are OpenWoW's server tick time, Benilla's
  load time, Orion's start-up and the image boot time.
- A slowdown of more than 10% becomes a card naming the commit that caused it.
- **Use:** automatic. "Orion, is the server faster than last week?"
- **Built from:** hyperfine, Rust's criterion benchmarks, and the runner.

### 14. Log Lens: ask your logs a question (Build, M)
- **Use:** "Why did the world server restart at 3 am?" Orion searches the system journal, app logs, crash-healer
  records and the server's own logs on every machine. It answers with a short timeline and the key lines.
- **Built from:** journald, lnav-style parsing, Log Lens indexes in the knowledge vault, and a local model for
  the summary.

### 15. Clean Box: a fresh machine in seconds (Adopt, S)
- **Use:** "Orion, give me a clean box for testing the Benilla build." You get a throwaway Ubuntu container with
  the project's tools, your editor attached, and nothing of yours inside unless you share a folder.
- Deleted when you're done.
- **Built from:** distrobox or Podman, plus the Runbook's tool versions.

### 16. Backup with restore drills (Adopt + New, S)
- Encrypted, versioned backups of projects, the vault, the databases and Blender files. They go to a USB drive
  and optionally a cloud bucket.
- **The new part:** once a month Orion actually restores a random sample into a Clean Box and checks it opens. You
  learn that backups work before you need them.
- **Use:** automatic. "Orion, when was the server DB last backed up, and does it restore?"
- **Built from:** restic, Clean Box, and DB Time Machine snapshots.

### 17. Dependency Autopilot: updates that test themselves (Adopt, M)
- Weekly, Orion proposes dependency updates for each repo and runs the tests in a sandbox.
- It brings you only the ones that pass, as one card: "9 updates ready, all tests pass, 1 needs a code change".
- Security fixes come immediately.
- **Use:** automatic. Approve all from the phone.
- **Built from:** Renovate (self-hosted CLI), the sandboxes, and Proof Gate.

---

## C. Security: defending your own machines, repos and accounts

### 18. Secret Shield: no keys in the public repo (Adopt, S)
Your Orion repo is public, so this comes first.
- Every commit, from you or an agent, is scanned before it's made. The whole git history is scanned once.
- Secrets are blocked with the exact line. If one ever leaked, Orion gives a step-by-step guide to rotate it.
- GitHub's own push protection for public repos is turned on as a second net.
- **Use:** automatic. "Orion, is anything secret in my repos?"
- **Built from:** gitleaks (pre-commit), trufflehog (history), and GitHub secret scanning.

### 19. Supply-Chain Scan: known holes in what you depend on (Adopt, S)
- **Scanned daily:** the Rust crates, Python and npm packages, and the OrionOS image itself.
- **Each finding is explained in plain words:** what's affected, whether your code actually uses it, and the
  fix.
- **Use:** automatic, as a weekly summary card. "Orion, is the USB image safe to ship?"
- **Built from:** osv-scanner, cargo-audit, pip-audit, npm audit, and syft + grype for the image.

### 20. Exposure Map: what can the outside reach? (Build, S)
- Each machine (PC, laptop, VMs, server) gets a live map of what's listening on which ports, and who can reach it:
  local only, your LAN, Tailscale, or the internet.
- Firewall and Tailscale rules are checked against it.
- A new open port, or a service that suddenly faces the internet, becomes a card.
- **Use:** automatic. "Orion, what's open on the server?"
- **Built from:** `ss`, ufw/Windows Firewall rules, the Tailscale ACL, and a polite scan of your own LAN only.

### 21. Host Health: a weekly security check-up (Adopt, S)
- **Each machine gets a score and the three most useful fixes,** in plain words.
- **Optional extras:**
  - **Brute-force protection:** blocks repeated password guessing against the server's login and SSH.
  - **File watch:** reports changes to important system files.
- **Use:** automatic every Sunday. "Orion, how healthy is the laptop?"
- **Built from:** Lynis, AIDE, CrowdSec or fail2ban, and the existing OrionOS hardening script.

### 22. Tripwires: know if someone is inside (Adopt, S)
- **Bait:** fake API keys, fake database logins and a fake "passwords" file are planted in places an intruder or a
  rogue script would look. Examples are an old repo branch, the VM desktops and the server's home folder.
- **Alarm:** if anything ever uses one, your phone gets an urgent alert saying where it came from.
- **Use:** "Orion, set tripwires on the server." After that it's silent unless triggered.
- **Built from:** the Canarytokens approach, self-hosted on your own server, so nothing depends on a third party.

### 23. Share Scrubber: share without leaking (Build, S)
- Before a screenshot, log, crash report or code snippet leaves your machine (Discord, GitHub issues, or a
  message to an AI), Orion blurs or removes secrets. That covers keys, tokens, emails, home paths, IP addresses
  and server names.
- **Use:** automatic for Orion's own sharing. A "Share clean" button and hotkey for anything you copy.
- **Built from:** the existing secret filter, OCR for screenshots, and the guide-card blur code.

---

## D. Life and community

### 24. Raid Night Autopilot (New, M)
Before raid night, Orion does the checklist so you don't have to:
- **Server:** checks it's up and healthy, and that last night's backup restores.
- **Patch:** if one is staged, it's tested and ready to apply.
- **Announcement:** drafts the Discord post. You approve it, and it says it was written with Orion's help.

During the raid:
- It watches for crashes and lag spikes. Restarting the server follows your approval rules.
- It keeps a short log for the post-raid notes.

- **Use:** set once ("Raid night is Fridays at 7"). Then one phone tap on "All good, post it".
- **Built from:** the wow agent, crash healer, Backup drills, Perf Sentinel and the comms drafter.

### 25. Promise Keeper: never forget what you said you'd do (New, M)
- Orion notices commitments in your own messages across Discord, Messenger, Telegram and texts. Examples are
  "I'll send you the build tomorrow" and "I'll fix that bug this weekend".
- It turns each one into a quiet task with the date and a link back to the message, and nudges you once before
  it's due.
- It never messages anyone for you.
- **Use:** automatic, as a "Promises" list in the morning brief. "Orion, what did I promise this week?"
- **Built from:** the unified messages, a local model to spot commitments, and quick commands.

---

## Where to start (when building resumes)

| Order | Tools | Why first |
|---|---|---|
| 1 | Secret Shield (18), Collision Guard (2) | Public repo safety, and tonight's two-conversations problem |
| 2 | Proof Gate (3), Baton (1), One Rulebook (6) | Make every agent you already use more trustworthy and interchangeable |
| 3 | Backup with restore drills (16), DB Time Machine (12) | Undo for everything before agents get more power |
| 4 | Package Reality Check (7), Injection Firewall (8), Key Proxy (9) | The three agent-specific security holes most setups ignore |
| 5 | Runbook (10), Release Butler (11), Raid Night Autopilot (24) | The daily-life wins |
| then | The rest | As needed |

All 25 can be split across parallel agents in the same way as Phase 1. Collision Guard, built first, then
protects the build itself.

## Left out on purpose

- Anything that hides identity or routes money anonymously.
- Anything that probes other people's systems. All scanning is limited to your own machines and networks.
- Anything that posts publicly without your approval.

All of these conflict with Orion's core rules.