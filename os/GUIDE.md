# OrionOS user guide

> **New here?** On Windows, double-click **Start Here.cmd** in the Orion folder: it sets everything up step by step. To check that it all works, see [docs/TESTING.md](../docs/TESTING.md).

Plain-language guide to living in OrionOS. Items marked *(coming)* are planned in `ARCHITECTURE.md` and not built yet.
This guide started with the v0.1 VMs; for installing on a real PC see [docs/install](../docs/install/README.md).

## Getting in

1. Double-click **Create OrionOS VMs.cmd** in the Orion folder on Windows. Wait for both windows to finish installing
   (15-30 minutes; they restart once).
2. **orion-boardroom** window: choose your boardroom passphrase. Write it down somewhere safe offline.
3. **orion-lab** window: you land on the black hole. Press **Ctrl+Alt+T**, type `orion-ctl pair-boardroom`, and follow
   the screen: type the code into the boardroom, check the fingerprints match, type `yes`.

Done. From now on the lab starts straight into Orion.

## Moving around

| Do this | Keys | Or say *(coming)* |
|---|---|---|
| Back to the HUD | Ctrl+Alt+O | "Orion, home" |
| Open a terminal | Ctrl+Alt+T | "Orion, terminal" |
| Launch an app | Ctrl+Alt+Space, type its name | "Orion, open Firefox" |
| Switch workspace | Ctrl+Alt+2 / 3 / 4 | "Orion, workspace two" |
| Orion Files | Ctrl+Alt+E | "Orion, open files" |
| Web browser (Firefox) | Super+B or Ctrl+Alt+B | "Orion, open Firefox" |
| Orion Settings (pairing, display, sound, power) | Ctrl+Alt+S | "Orion, settings" |
| Close a window | Super+Shift+Q | "Orion, close this" |
| Release the mouse from the VM | right **Ctrl** | |

Your apps always open on workspace 2 so the HUD stays on 1.

Everyday files open with a ready-made app: PDFs in Document Viewer, pictures in Image Viewer, video and music in
mpv, text and code in Text Editor, zip/7z/tar in Archive Manager, Word/Excel/PowerPoint in LibreOffice, links in
Firefox. Right-click a file in Orion Files and pick **Open With** to choose another app (it's remembered). Firefox
starts on Firefox Home with DuckDuckGo search, no sponsored tiles and no telemetry; change either in its Settings.
Chromium is optional: `orion-ctl install chromium`.

## Approvals

- When Orion wants to do something that matters, a **moon** appears on the HUD and in the Queue tab.
- Small things (a draft reply) you approve in the HUD.
- Big things (money, merges, public posts, firewall) show as **Boardroom** cards: go to the boardroom window,
  press **a** (approve), **e** (edit), or **r** (reject), then type your passphrase.
- Orion learns: after 10 clean approvals of the same kind, it asks to take on a little more. Money never goes past
  "suggest and still ask".

## Guide Mode *(coming)*

Say "Orion, what should I do next?" or click **Guide** on the HUD.

1. Orion suggests one job and why ("Your realm crashed twice today; let's fix the login loop").
2. A small card floats over everything with **one instruction at a time**, read aloud.
3. Steps marked **Orion does it** happen by themselves while you watch. Steps marked **You do it** wait for you:
   a glowing ring shows exactly where to click.
4. Press **Done** (or say "done"), **Skip**, or **Help** ("explain that differently").
5. Press **Esc** or say **"Orion, stop"** at any time; everything pauses.

You will always do these yourself: making accounts, logging in, captchas, ID checks, two-factor codes and anything
with payment details. Orion tells you exactly what to click, but never types those for you.

## Chat in one place

The **Comms** tab shows Discord, Telegram and Messenger together. Type a reply, or let Orion draft one in your voice
and press approve. Notifications for VIP contacts break through Focus mode; everything else waits for the digest.

## Useful commands (terminal)

| Command | What it does |
|---|---|
| `orion-ctl status` | Brain, screen/keyboard control, boardroom, paper treasury, learned levels |
| `orion-ctl simulate bounty` | Make a test decision appear (also: donation, docs, faq, hosting) |
| `orion-ctl logs` | Watch Orion's log |
| `orion-ctl restart` | Restart Orion's brain |
| `sudo orion-killswitch status` | Is the VPN killswitch on? |

## When something's wrong

| Problem | Fix |
|---|---|
| Black screen after boot | Wait one minute; then Ctrl+Alt+T and `orion-ctl status` |
| HUD says the brain is offline | `orion-ctl restart` |
| Boardroom says "can't reach the lab" | Both VMs must be running; check the lab with `orion-ctl status` |
| "stale request" on the boardroom | The two clocks drifted; restart the boardroom VM |
| Everything is slow | Close other apps on Windows; the lab wants about 6 GB free |
| Install never finished | Send `%USERPROFILE%\VirtualBox VMs\OrionOS\orion-lab\console.log` to Claude |

## Phone *(coming)*

Install the Orion APK, run `orion-ctl pair-phone` on the lab, scan the code, confirm the fingerprint. You'll get
approval notifications, chat, voice and Guide cards on the phone, over your private Tailscale network.
