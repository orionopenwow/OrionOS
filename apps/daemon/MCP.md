# Orion tools for coding agents (MCP)

`orion-mcp` lets coding agents (Claude Code, Codex, Gemini CLI) use Orion's tools through the
Model Context Protocol. It talks over stdio: the agent starts it and sends requests on stdin.

| Tool | What it does | Risk |
| --- | --- | --- |
| `grimoire_list` | Lists your saved macros and scripts | read-only |
| `grimoire_run` | Runs a saved macro in the project folder | runs a command (macros marked "needs approval" ask you first) |
| `runner_start` | Starts one of the project's own commands in Orion's self-healing runner | runs a command (other commands ask you first) |
| `runner_status`, `runner_logs` | Status and output of those runs | read-only |
| `git_status`, `git_diff`, `git_log` | Look at the repo | read-only |
| `git_commit` | Stages the files you name, then asks you to approve the commit | needs approval |
| `cargo_check`, `cargo_test` | Rust checks and tests, with a timeout | runs a command |
| `mysql_query` | SELECT / SHOW / DESCRIBE / EXPLAIN on a staging database | read-only (anything else asks you first) |
| `blender_run_script` | `blender --background --python script.py` for a script in the project | runs a command (unavailable without Blender) |
| `approval_status` | Was a proposed action approved yet, and what happened | read-only |

"Project's own commands" are: its package.json scripts (`npm run x`, `pnpm x`, `yarn x`, `bun run x`),
`cargo build|check|test|run|clippy|fmt|doc|bench`, its make targets, `pytest`, `go build|test|vet|run`,
`dotnet build|test|run`, plus anything you list under `"commands"` in mcp.json. Shell operators
(`;`, `|`, `&&`, `$(...)`), paths outside the project and destructive commands always ask you first.

## How approval works

Commits, database changes and unfamiliar commands are never done straight away. The tool sends them to
Orion (the daemon on 127.0.0.1:9120) as a decision and answers the agent with
"Waiting for your approval … decision dec-…". You approve or decline it on the boardroom or your paired
phone, like any other Orion decision. These decisions always ask; they never become automatic.

A commit is pinned to exactly what was staged when it was proposed. If the staged changes are different
when you approve, nothing is committed and the agent is told to propose it again.

If Orion isn't running, read-only tools and project commands still work (runs then live only as long
as the agent's session), but anything that needs approval is refused.

Output is shortened (20,000 characters by default) and anything that looks like a password, token or key is
replaced with `[redacted]`.

## Which folders agents may use

Your Orion repo root (`ORION_REPO_ROOT`, `/home/orion/projects` on OrionOS) and anything listed in
`~/.config/orion/mcp.json` (or the file named by `ORION_MCP_CONFIG`):

```json
{
  "roots": ["~/code/openwow", "~/code/tools"],
  "commands": { "~/code/openwow": ["./scripts/dev-server.sh"] },
  "max_output_chars": 20000,
  "blender": "/opt/blender/blender",
  "mysql": {
    "staging_hosts": ["127.0.0.1", "db.staging.lan"],
    "default": "staging",
    "connections": {
      "staging": { "host": "db.staging.lan", "port": 3306, "user": "dev",
                   "password_env": "ORION_MYSQL_STAGING_PASSWORD", "database": "game" }
    }
  }
}
```

A MySQL connection only works if its host is in `staging_hosts`. Prefer `password_env` (the name of an
environment variable) over writing the password in the file. The MySQL tool needs `pip install PyMySQL`
in Orion's Python.

When no `project` argument is given, tools use the folder the agent was started in (if it's allowed).

## Register it

On OrionOS `orion-mcp` is on the PATH. From a checkout, use the full path to `os/lab/bin/orion-mcp`, or on
Windows `python C:\path\to\orion\apps\daemon\mcp_server.py`.

**Claude Code**

```sh
claude mcp add orion -- orion-mcp
# or for every project:  claude mcp add --scope user orion -- orion-mcp
```

**Codex** (`~/.codex/config.toml`)

```toml
[mcp_servers.orion]
command = "orion-mcp"
args = []
# cargo test can take a while:
tool_timeout_sec = 900
```

**Gemini CLI** (`~/.gemini/settings.json`, or `.gemini/settings.json` in a project)

```json
{
  "mcpServers": {
    "orion": { "command": "orion-mcp", "args": [], "timeout": 900000 }
  }
}
```

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `ORION_MCP_CONFIG` | `~/.config/orion/mcp.json` | Where the folder / MySQL settings live |
| `ORION_DAEMON_URL` | `http://127.0.0.1:9120` | Where approvals and shared runs go |
| `ORION_MCP_PROJECT` | the agent's folder | Default project folder |
| `ORION_MCP_LOG` | `WARNING` | Log level (logs go to stderr) |

## Protocol notes

Newline-delimited JSON-RPC 2.0 over stdio. Supports `initialize` (protocol versions 2025-11-25,
2025-06-18, 2025-03-26 and 2024-11-05), `notifications/initialized`, `ping`, `tools/list`, `tools/call`,
`notifications/cancelled` and batches. Each tool carries MCP annotations (`readOnlyHint`,
`destructiveHint`) and `_meta["orion/risk"]` = `read`, `run` or `approval`. Streamable HTTP isn't offered
yet; every supported agent can use stdio.

Daemon routes used by `orion-mcp` (local only): `POST /mcp/propose`, `GET /mcp/decision/{id}`,
`POST /mcp/run`, `GET /mcp/runs`, `GET /mcp/runs/{id}`. They re-check folders and commands with the same
rules.

# Toolkit: Secret Shield (tool 18)

No keys in the public repo. Most of it happens by itself:

- **Every commit is checked before it's made**, whoever makes it (you, Claude Code, Codex, Gemini, Cursor).
  `.githooks/pre-commit` runs gitleaks with the repo's `.gitleaks.toml` when gitleaks is installed, otherwise
  Orion's built-in scanner (same patterns as the daemon, from `apps/daemon/secret_shield_rules.json`). A blocked
  commit shows the exact `file:line`, the kind of secret (never its value) and how to fix it. Files that hold keys
  by design (`.env`, `.orion-signing/`, `*.key`) are blocked by name without being opened. A deliberate fake (in
  a test) can be allowed with a `gitleaks:allow` comment on that line.
- **Turning it on:** `npm install` (the `prepare` script) and "Publish to GitHub.cmd" run
  `node scripts/secret-shield/install-hooks.mjs`, which points git at `.githooks/`. Works in Git for Windows'
  bash and on Linux. For the strongest check install gitleaks (`winget install gitleaks.gitleaks`).
- **Whole history, once:** `node scripts/secret-shield/scan-history.mjs` (or `npm run secrets:scan-history`) uses
  gitleaks and trufflehog when installed (trufflehog runs with `--no-verification`, so nothing is sent anywhere),
  else the built-in scanner. It writes a plain-words report with rotation steps to `logs/secret-shield/`.
  "Publish to GitHub.cmd" runs it before uploading and stops if anything is found.
  `node scripts/secret-shield/scan-history.mjs --guide openai` prints the rotation steps for one kind of secret.
- **CI:** the `secrets` job in `.github/workflows/ci.yml` runs gitleaks (gitleaks-action, pinned) on every push and
  pull request, plus a full scan and the built-in scanner. On an organization account gitleaks-action needs a
  free `GITLEAKS_LICENSE` secret; personal accounts don't.
- **GitHub push protection (owner, one time):** turn it on as a second net in the repo on GitHub:
  Settings > Code security > Secret Protection > Push protection. Orion doesn't change repo settings for you.

| Tool | What it does | Risk |
| --- | --- | --- |
| `secret_shield_scan` | "Orion, is anything secret in my repos?" Scans the current files plus the last 300 commits (`history: "quick"`, default), the whole history (`"full"`) or the files only (`"none"`), for the repos you name or every repo Orion may use. Answers in plain words with every secret replaced by `[redacted]`, plus the steps to rotate what it found. | read-only |

When the daemon is running, findings also post a phone alert and a Queue card (approve = "I've rotated these",
reject = dismiss). The same findings never raise a second card. Daemon routes (local only):
`POST /secret-shield/scan {"repos"?, "history"?}`, `GET /secret-shield/last`, `GET /secret-shield/status`,
`GET /secret-shield/guide/{kind}`.

Rotation steps exist for OpenAI, Anthropic, GitHub, GitLab, Discord (bot token and webhooks), Telegram, Slack,
Stripe, AWS, Google, Hugging Face, npm, private keys, passwords in links and anything else ("generic").

# Toolkit: One Rulebook (tool 6)

Write the project rules once in `.orion/rules.md`. Orion keeps `CLAUDE.md` (Claude Code), `AGENTS.md` (Codex,
Antigravity and others), `GEMINI.md` (Gemini CLI) and `.cursor/rules/orion.mdc` (Cursor) in sync. Each generated
file says "generated from .orion/rules.md, edit there"; notes written between its `rulebook:keep` markers are kept
on every sync. Each rule has an id (`- [no-push] Never push without asking.`).

- Sync without the daemon: `node scripts/rulebook/sync.mjs` (`check`, `list` and `add "..." --section X` too), or
  `npm run rules:sync`.
- "Orion, add a rule: never push without asking." adds it under the right section and updates all four files
  (`POST /rulebook/rules {"text", "section"?}`).

| Tool | What it does | Risk |
| --- | --- | --- |
| `rulebook_list` | The project's rules, with how often agents have broken each one | read-only |
| `rulebook_add_rule` | Proposes a new rule; after your approval it's added and every agent's file is updated | needs approval |
| `rulebook_sync` | Rewrites the four generated files from `.orion/rules.md` (writes only those files) | writes generated files |

An agent can't add a rule on its own: it would change every agent's instructions, so it always asks you.

**Rules an agent keeps breaking.** Proof Gate (or anything else) reports a broken rule with
`rulebook.record_violation(rule_id, agent, evidence)` or `POST /rulebook/violations {"rule_id", "agent",
"evidence"}`. Orion counts them per rule and per agent; when a rule has been broken 3 times you get one alert and
`rulebook_list` marks it. `GET /rulebook/violations` shows the counts, `DELETE /rulebook/violations/{rule_id}`
resets one. The full contract is in the docstring of `apps/daemon/rulebook.py`.

# Get apps: asking to install an app

Agents can't install desktop apps. They can ask (`apps/daemon/get_apps.py`, `os/getapps/README.md`):

| Tool | What it does | Risk |
| --- | --- | --- |
| `apps_install` | Puts an "Install X?" card (Flathub app id, who asks, why, whether it's one of Orion's picks) in your approval queue. Only your approval installs it (`orion-apps install <id>`). | needs approval |
| `apps_request_status` | Whether that request is waiting, installed, failed or declined | read-only |

Routes: `POST /apps/install-request {app_id, why?, requested_by?}`, `GET /apps/install-request/{id}`, and
`POST /apps/notify {title, body}` (used by `orion-apps` to tell your phone what the weekly app update did).

# Orion Studio: the Craft apps' tools (`apps/daemon/craft_bridge.py`)

When PhotoCraft or PdfCraft is installed (Get apps, Orion Studio), Orion starts its own MCP server
(`photocraft-cli mcp`, the Flatpak, an AppImage, or the command in `~/.config/orion/craft.json`) and offers its
tools as `photocraft.<tool>` / `pdfcraft.<tool>`. `<app>.about` is always there and says "not installed, get it
in Get apps" until the app is installed.

| Kind of call | What happens |
| --- | --- |
| Tool marked read-only (or named get/list/info/...) | Runs straight away |
| Tool that writes | Saves to a new file next to the original (`photo-orion.png`); in-place tools work on a copy |
| Writes over an existing file, `overwrite_original: true`, a tool marked destructive, or any path outside the allowed folders | Proposed as a boardroom decision (`mcp.craft_write`, always asks); check with `approval_status` |

Routes: `GET /craft/status`, `POST /craft/propose`, `POST /craft/try-with-craft {kind: pdf|images, on}` (the
"Try with Craft" default-app switch, off by default; `os/basics/orion_default_apps.py`).

# Toolkit: Clean Box and Backups

| Tool | What it does | Risk |
| --- | --- | --- |
| `clean_box_run` | Runs a command in a brand-new Ubuntu 24.04 container (rootless Podman), deleted afterwards. Only the folder you pass is inside (read-only unless `writable`), no network unless `network`. | runs a command |
| `backup_status` | When the last backup ran, whether the last monthly restore test passed | read-only |

Routes: `GET /backup/status`, `POST /backup/now`, `POST /backup/drill`, `POST /backup/restore {snapshot?,
include?, target?}` (always into a new folder), `GET|POST /backup/config {usb, s3}` (never keys),
`GET /backup/recovery`. Command line: `orion-ctl box ...`, `orion-ctl backup ...`.
# Toolkit: Project Runbook, Release Butler, DB Time Machine, Raid Night (tools 10, 11, 12, 24)

| Tool | What it does | Risk |
| --- | --- | --- |
| `runbook_list` | How the project runs, tests, builds and deploys, and its pinned tool versions | read-only |
| `runbook_run` | Runs the project's run/test/build/lint step (`just <step>` when there's a justfile) | runs a command (deploy and anything destructive ask you first) |
| `release_plan` | Release dry run: changelog, version bumps, the steps it would take | read-only |
| `release_start` | Prepares a release on its own branch (changelog, bump, full tests, signing) | needs approval before anything is pushed |
| `db_snapshot` | Consistent snapshot of a staging MySQL database (or some tables) | runs a command (staging hosts only) |
| `db_changes` | Row-level changes since a snapshot, in plain words | read-only |
| `db_rollback` | Asks you to put a table, or the whole staging database, back to a snapshot | needs approval |
| `raid_status` | Raid night schedule, next raid, last checklist | read-only |

Approved `mysql_query` writes now snapshot the touched table first and report what changed afterwards
(`mcp.json` → `"mysql": {"time_machine": {"keep_last": 30, "keep_days": 14}}`). If the snapshot fails, the
write doesn't run. Routes: `ops_toolkit.py` (`/runbook`, `/release`, `/dbtm`, `/raid`).

# Toolkit: AI Blame, Context Pack, Perf Sentinel, Log Lens (tools 4, 5, 13, 14)

| Tool | Say | What it does | Risk |
| --- | --- | --- | --- |
| `ai_blame` | "Orion, why did line 42 of app.py change?" | The commit behind a line: which agent (tool, your request, estimated cost, Proof Gate result) or "you, by hand" | read-only |
| `context_pack` | "Orion, brief Claude on the vault bug" (copies it) | A token-budgeted brief: rules, relevant files and symbols, recent errors, plan decisions, Baton notes | read-only |
| `log_lens` | "Orion, why did the world server restart at 3 am?" | Journals, crash-healer records, change history and log files as a short timeline with the key lines | read-only |

Perf Sentinel has no MCP tool: it runs the benches in a repo's `.orion/perf.json` (on commit, on green Proof Gate
checks or nightly) and posts a card when something gets more than 10% slower. "Orion, is the server faster than
last week?" answers from its trend. AI Blame keeps its notes in `refs/notes/orion-blame`, never pushed. Routes:
`dev_lens.py` (`/blame`, `/context/pack`, `/perf`, `/logs/ask`). VS Code: "Orion: why did this line change?".
# Safety net (read-only)

| Tool | What it does | Risk |
| --- | --- | --- |
| `health_status` | The health monitor's summary and the fix for anything that isn't fine | read-only |
| `snapshots_list` | The system snapshots Orion can go back to, and the undo point before the last update | read-only (restoring always asks you) |

Both live in `trust_kit.py` with Tripwires (`/tripwires`, `/tripwire/<token>`), the Share Scrubber
(`/share/scrub`, `/share/scrub-image`, `/share/clipboard`) and Promise Keeper (`/promises`).

# Toolkit: Collision Guard, Proof Gate and Baton

Three Orion Toolkit tools (toolkit plan, tools 2, 3 and 1) that make coding agents safer to run side by side.
They work by themselves, by one phrase, and as MCP tools. MCP tool names use underscores (`collision_check`
rather than `collision.check`) because agents such as Claude Code only accept letters, digits, `_` and `-`.

| Tool | Say | What it does | Risk |
| --- | --- | --- | --- |
| `collision_check` | "Orion, is it safe to start?" | Yes, or the list of conflicts with a worktree command and a merge order | read-only |
| `collision_claim` | | Tells Orion which files/globs you're about to change, so others get warned first (expires, default 4 h) | changes Orion's map only |
| `proof_prove` | "Orion, prove it" | Checks an agent's "done" in a temporary copy: build, tests, claimed files vs the real diff, optional screenshot | runs the project's build and tests |
| `baton_handoff` | "Orion, hand this to Codex" | Writes `.orion/batons/<time>-<from>-to-<to>.md` and returns a prompt ready to paste | writes one file in the repo |

Repos must be inside the folders agents may use (see above). With Orion not running, `collision_check` still
checks git (but can't see other agents), `proof_prove` and `baton_handoff` work on the project you name, and
`collision_claim` is refused (a claim nobody else can see would be misleading).

## Collision Guard

A live map of who is changing which files, per repository and across all its worktrees:

- **agents** from the swarm (agent_watch): an agent alone in its folder owns every change there; agents
  sharing a folder own only the files their logs say they edited;
- **claims** made through `collision_claim` or `POST /toolkit/collisions/claim`; a claim by an agent Orion can
  see is attached to that agent's robot;
- **you**: changes in any other worktree of the repo (uncommitted, plus commits on its branch since it left
  `main`).

When two of them touch the same file, the daemon sends a `collision.warning` event (`a` and `b` are the two
agent ids; the HUD draws a dashed red line between their robots), a phone alert, the exact
`git worktree add … -b orion/<agent>` command for the agent that should move out (when they share a folder),
and a merge order (finished work first, then the smaller change, then whoever started first). It is announced
once it has been seen twice in a row (5 s apart), and `collision.cleared` follows when it goes away.

It is cheap: `git status` runs at most every 6 s per worktree, only for repos where an agent or a claim is
active, with `GIT_OPTIONAL_LOCKS=0` (it never takes the lock an agent needs). `node_modules`, `target`, `.git`,
build output, `.orion` and Orion's own temporary copies are ignored. Only paths are read, never contents.
Extra repos to watch: `ORION_COLLISION_REPOS="path;path"`.

## Proof Gate

When a robot turns "done" (and it changed files), or on `proof_prove`, Orion:

1. makes a temporary `git worktree` of the agent's commit in its data folder and copies in the uncommitted
   changes and new files, so the agent's own checkout is never touched (the copy is removed afterwards);
2. finds the projects next to the changed files and runs their build and tests there: Node `npm run build` /
   `npm test` (reusing the checkout's `node_modules`), Rust `cargo test --offline` (its own target folder),
   Python `compileall` plus pytest (the crash healer's test detection, the project's `.venv` if it has one).
   The environment is clean (no API keys), nothing needs the network, and every step has a timeout
   (build 10 min, tests 15 min);
3. compares the files the agent named (from its log, or the `claim` text) with the real git diff;
4. optionally screenshots a page on this computer with headless Chromium (only if Playwright is installed;
   otherwise it says it skipped it).

The robot stays "Checking its work" meanwhile, then turns green ("Proven: the build passes, 12 tests pass")
or amber with the reason ("Said tests pass; 2 fail"). Results go out as `proof.result` events and are kept
(`GET /toolkit/proof`). When an agent's claim was wrong (tests, build or files), it's reported to One Rulebook
(`rulebook.record_violation`) if that module is installed. The automatic check proves the same diff only once.
`ORION_PROOF_AUTO=false` turns the automatic check off.

A repo can say exactly what to run in `.orion/proof.json`:

```json
{ "build": "npm run build", "test": "npm test", "timeout_s": 900,
  "screenshot": { "url": "http://127.0.0.1:5173/" } }
```

## Baton

A hand-off in Markdown: goal, summary (by the local model when Ollama is running, otherwise a fixed template),
decisions made (commit messages plus your notes), the last steps the agent took, files changed (`git diff
--stat` and the key hunks), the latest Proof Gate result with failing tests, what's left, and the exact next
step. It's saved in the repo at `.orion/batons/` and in the daemon (`GET /toolkit/batons`), and comes with a
prompt ready to paste into the next agent.

When an agent hits its usage limit (agent_watch reads the agent tool's own limit notice, never your
messages), its robot says "Hit its usage limit" and a card appears in the approval queue: "Hand its work to
Codex?". Edit the name to pick another agent. Nothing happens until you approve, and no agent is started by
itself: launching goes through Mission Control only once it exists; until then the file and the prompt are the
output.

## Routes

`GET /toolkit/collisions`, `POST /toolkit/collisions/check` `{repo, files?, party?}`,
`POST /toolkit/collisions/claim` `{party, repo, files, branch?, ttl_minutes?}`,
`POST /toolkit/collisions/release` `{claim_id}` or `{party, repo?}`,
`POST /toolkit/proof` `{target?, repo?, claim?, screenshot_url?, wait?}`, `GET /toolkit/proof[/{id}]`,
`POST /toolkit/baton` `{from_agent, to_agent, repo?, goal?, notes?, launch?}`, `GET /toolkit/batons[/{id}]`.
`GET /agents/swarm` also carries `collisions` (robot pairs) and a `proof` verdict per robot.

# Local model kept warm

The daemon keeps `OLLAMA_MODEL` loaded so offline answers, indexing and quick completions start fast. Every
`ORION_OLLAMA_WARM_INTERVAL` seconds (default 240) it asks Ollama to keep the model loaded for
`ORION_OLLAMA_KEEP_ALIVE` (default `15m`). It only pings when Ollama answers, and backs off (30 s up to
15 min) when it doesn't. On a Linux laptop running on battery below `ORION_OLLAMA_WARM_MIN_BATTERY` percent
(default 30) it stops until you plug in or charge. `ORION_OLLAMA_WARM=false` turns it off.

`GET /ollama/warm` shows the model, whether it's loaded, the last ping, and why it's paused if it is.

# Toolkit: Package Reality Check, Injection Firewall and Key Proxy (tools 7, 8, 9)

All three run by themselves. Settings: **Agent safety** in OrionOS Settings, or `GET /agent-safety/status`.

## Package Reality Check
New dependencies are checked before they're installed: does the package exist on PyPI, npm or crates.io, how
old is it, how many downloads (npm, crates.io), who publishes it, and is its name one or two letters off a
popular package (`package_top_lists.json`, bundled; rebuild with `python3 scripts/package-check/update_top_lists.py`).
Suspicious packages are blocked and a card in the Queue explains why; approving it trusts them from then on.
- Checked on: `runner_start` / `grimoire_run` install commands, every command Orion's runner starts,
  `git_commit` (staged manifests), Proof Gate (manifests and lockfiles the branch changed) and every git commit
  (`.githooks/pre-commit` runs `scripts/package-check/pre-commit.mjs` after Secret Shield).
- Registries unreachable: `offline` = `ask` (default, blocked until approved) or `allow`. Without Orion
  running, the git hook still runs the look-alike test.
- MCP tool `package_check` (read): ask before adding a package. Routes: `POST /package-check/command`
  `{command, cwd?}`, `POST /package-check/packages` `{packages: ["pypi:requests"]}`, `POST /package-check/manifests`,
  `POST /package-check/staged` `{repo}`, `GET /package-check/status`, `POST /package-check/settings`.

## Injection Firewall
Results of MCP tools that carry outside text (database rows; `web_*`, `github_*`, `email_*`, `scout_*` and similar
tools; command output from curl, wget, gh, git clone) are framed as "data, not instructions" and remembered by a
per-session taint tracker. Orion's own readers (page reader, comms hub, Video Scout) feed the daemon's tracker.
Before a risky call (risk run or approval, or a send/post tool) the arguments are compared with that text; if the
call copies it (word for word, same phrases, same unknown web address or email) **and** does something dangerous
(sends data out, reads secrets such as .env or ~/.ssh, switches off a check, publishes, runs code from the
internet, changes startup), it's stopped and a card quotes the source and the attempted call. Approving lets that
exact call through once. The server instructions tell agents to treat tool output as data.
It can't see what agents read or do with their own built-in tools.
- MCP tool `firewall_status` (read). Routes: `POST /firewall/check`, `/firewall/taint`, `/firewall/report`,
  `/firewall/allowance`, `/firewall/settings`, `GET /firewall/status`, `GET /firewall/blocks`.

## Key Proxy
Agents get placeholders (`orion-placeholder-...`, also as `ORION_KEY_OPENAI` etc.) and point their base URL at
`http://127.0.0.1:9131/<provider>/`; the proxy adds the real key (from Orion's own settings) and forwards to the
provider's fixed address. Each placeholder has allowed providers, a spending cap (estimated with the compute
meter's prices, and recorded there), an expiry, and can be revoked. Every call is logged without headers or bodies.
```
# Claude Code (works; API-key use only, not a subscription login)
ANTHROPIC_BASE_URL=http://127.0.0.1:9131/anthropic   ANTHROPIC_API_KEY=orion-placeholder-...
# OpenAI SDKs (work) and Codex CLI (likely; otherwise set base_url in ~/.codex/config.toml)
OPENAI_BASE_URL=http://127.0.0.1:9131/openai/v1      OPENAI_API_KEY=orion-placeholder-...
# Gemini CLI (not sure every version reads this; check with a test call)
GOOGLE_GEMINI_BASE_URL=http://127.0.0.1:9131/gemini  GEMINI_API_KEY=orion-placeholder-...
```
"Orion, which agents used my OpenAI key today?" answers from the log. `ORION_KEY_PROXY=0` turns it off,
`ORION_KEY_PROXY_PORT` moves it. MCP tool `key_proxy_usage` (read). Routes: `GET /key-proxy/status`,
`GET|POST /key-proxy/tokens` `{mission, agent, providers, cap_usd, ttl_hours}`,
`POST /key-proxy/tokens/{id}/revoke`, `GET /key-proxy/usage?provider=&since=today|yesterday|week|all`.

# Toolkit: Dependency Autopilot, Supply-Chain Scan, Exposure Map, Host Health (tools 17, 19, 20, 21)

| Tool | What it does | Risk |
| --- | --- | --- |
| `exposure_map` | "What's open on the server?" Each listening port and who can reach it (this computer only, home network, Tailscale, internet), checked against ufw/Windows Firewall and Tailscale (Shields Up, Funnel). | read-only |
| `host_health` | "How healthy is the laptop?" A score out of 100 (Lynis) and the three most useful fixes, with file watch (AIDE), disks (SMART), battery and brute-force protection. | read-only |
| `supply_chain_scan` | Known holes in every project's Rust, Python and npm packages (osv-scanner, cargo-audit, pip-audit, npm audit), merged and explained, with the fixing version and whether your code calls the broken part when osv-scanner can tell. | read-only |
| `image_safe_to_ship` | "Is the USB image safe to ship?" from the newest syft + grype scan made by `os/image/scan-image.sh`. | read-only |
| `dependency_autopilot` | Finds updates, tries them in a throwaway copy, runs the tests, sends one card. Approving (boardroom `deps.apply`) makes the branch `orion/deps-<date>`; never main, never pushed. | runs a command |

Routes: `GET /security/status`, `GET|POST /security/config`, `GET /deps`, `POST /deps/run {project?}`,
`GET /supply`, `POST /supply/scan`, `GET /supply/image`, `GET /exposure?machine=`, `POST /exposure/scan`,
`POST /exposure/lan-scan` (only when turned on), `POST /exposure/report` (from this computer only:
`os/windows/Report-Listeners.ps1`), `GET /host-health`, `POST /host-health/check`. Root reports come from
`orion-security-dump` (os/security, timers installed by `os/image/os-security.sh`) in `/var/lib/orion/security/`.
