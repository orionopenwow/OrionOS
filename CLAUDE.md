<!-- Generated from .orion/rules.md by Orion's One Rulebook for Claude Code. Don't edit the rules here: edit .orion/rules.md, then run `node scripts/rulebook/sync.mjs` (or ask Orion to sync the rules). Notes between the two rulebook:keep markers at the end are kept on every sync. -->

# Orion project rules

These rules apply to every coding agent working in this repo (Claude Code, Codex, Gemini CLI, Antigravity,
Cursor). They are not optional. Quote a rule's id (for example `[no-push]`) when a rule stops you doing something.

## Approvals
- [no-push] Never push to GitHub or any other remote, merge into the main branch, or change git remotes without
  asking the owner first.
- [approval-rules] Anything public, irreversible, involving money or secrets, or touching the production server
  waits for the owner's approval (fingerprint on the phone, or the boardroom). Tools and agents never get more
  power than that, and never work around an approval.
- [no-messaging] Never send a message, post or reply to anyone on the owner's behalf. Draft it and put it in the
  approval queue instead.
- [no-destructive] No destructive commands (rm -rf, git reset --hard, force-push, dropping a database) outside your
  own working folder and scratch folders. Never delete other people's files.

## Secrets
- [no-secrets] Never commit secrets: API keys, tokens, passwords, private keys. Keys live in apps/daemon/.env,
  which is never committed. If Secret Shield's pre-commit check blocks a commit, take the secret out of the file;
  never bypass or disable the check.
- [no-secret-reads] Never read, print or copy apps/daemon/.env, .orion-signing/ or any real secret, and never
  create real keys.
- [fake-secrets] Test secrets are built at runtime by joining pieces (for example "sk-" + "proj-" + ...), so no
  realistic-looking key is ever written into a file.

## Privacy
- [no-owner-name] Never put the owner's personal name anywhere: code, docs, images, commit messages, greetings.
  Greetings say "Morning" or "Evening", or use a name only if the user sets one in Settings at runtime.

## How to work
- [plain-words] Write for people in plain words: what you found, what you did, and what needs a decision. No
  jargon in anything the owner reads.
- [plans-are-truth] The plans in docs/ are the source of truth (ORION_TOOLKIT_PLAN.md, ORION_ART_UPDATE_PLAN.md,
  ORIONOS_*.md). Follow the decisions already made there instead of redesigning.
- [stay-in-scope] Keep changes focused on the task. If you must touch a file outside it, keep the edit small and
  say so.
- [prove-it] Don't say "done" until it's proven: it builds, the tests pass (run them), and the files you say you
  changed really changed.
- [free-licences] Fonts and assets must be freely licensed (OFL, MIT, CC0 or Apache), with the licence recorded
  next to anything bundled.
- [line-endings] Keep the line endings .gitattributes sets: .cmd, .bat and .ps1 files use CRLF; files under os/
  and *.sh files use LF.

## Network
- [network] No network calls except package registries (npm, pip, crates, Ubuntu mirrors) and GitHub clones. If a
  download is refused, don't try other routes: note it and use a fallback.

## Notes for Claude Code only

<!-- rulebook:keep:start -->
<!-- rulebook:keep:end -->
