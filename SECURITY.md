# Security policy

Orion runs on your own computer and phone, can read your screen when you allow it, and can run commands for you.
We take security problems seriously and are grateful for every report.

## Reporting a vulnerability

Please report security problems **privately**, not in a public issue, pull request or discussion.

1. Go to the repository on GitHub: https://github.com/orionopenwow/OrionOS
2. Open the **Security** tab and choose **Report a vulnerability**. This opens a private security advisory that
   only you and the maintainers can see.
3. Tell us what you found, how to reproduce it, which part is affected (desktop app, daemon, phone app, OrionOS
   image or scripts), and the version or commit you tested.

If you can't use GitHub's private reporting, open a public issue that only says you have a security report and
asks for a private way to send it. Don't include any details in that issue.

## What happens next

- We aim to reply within 7 days and to keep you updated until it's fixed.
- We fix the problem, publish a release, and then publish the advisory. We credit you unless you'd rather we didn't.
- Please give us a reasonable time to fix it before you share details publicly.

## Scope

In scope: everything in this repository, including the daemon (`apps/daemon`), the desktop app (`apps/desktop`),
the phone app (`apps/mobile`), the OrionOS image and provisioning scripts (`os/`), the Windows launchers and the
CI workflows.

Especially interesting:

- Ways to approve an action, send a message or move money without the owner's fingerprint or tap.
- Ways for a web page, document, message or agent to make Orion run commands it shouldn't (prompt injection
  that gets past the approval queue).
- Ways to reach the daemon (port 9120) or the phone endpoint (port 9121) without a valid signed request.
- Secrets, keys or personal data that leak into logs, crash reports, the repository or release files.

Out of scope: problems that need someone who already has full control of your unlocked computer, and reports from
automated scanners without a working example.

## Supported versions

Only the latest release and the `main` branch get security fixes.

## For contributors

- Never commit secrets. Keys belong in `apps/daemon/.env` (ignored by git). Secret Shield checks every commit and
  CI checks every push; don't bypass it.
- Test secrets are built at runtime from pieces (for example `"sk-" + "proj-" + ...`) so no real-looking key is
  ever written into a file.
