# OrionOS v0.1: two virtual machines

OrionOS turns Orion from an app into the operating system's shell. It runs as two VirtualBox VMs on your
Windows PC:

| VM | Address on OrionNet | What you see | What it holds |
|---|---|---|---|
| `orion-lab` | 10.77.0.10 | Boots straight into the Orion HUD (Sway, no desktop) | The Orion daemon, agents' sandboxes. **No signing key, no wallets.** |
| `orion-boardroom` | 10.77.0.11 | Boots into a text sign-off console | The only key that can approve Orion's decisions |

The lab proposes, the boardroom signs. An agent on the lab can never approve its own work because the lab has
no key, and the HUD refuses to approve boardroom items. **Money is paper money in v0.1:** donations are simulated,
the V3 waterfall fills five buckets, and an approved payout only writes a ledger entry marked SIMULATED. No
wallet, exchange or payment service is contacted anywhere.

## 0. The one-click way

Double-click **`Start OrionOS.cmd`** in the repo root. It:
1. turns off Windows' hypervisor if it's on (one admin prompt; it's what made boot freeze), restarts Windows
   with your OK and carries on by itself after you log back in;
2. creates the two VMs if they don't exist yet (the steps in section 1 happen automatically);
3. starts both VMs, and offers to start OrionOS every time you log in to Windows.

After that, Orion runs the first-time setup itself and only stops to ask you (section 3b).

## 1. Create the VMs (about 5 minutes of your time, then 15-30 minutes of installing)

1. Double-click **`Create OrionOS VMs.cmd`** in the repo root. It offers to install VirtualBox 7 with winget
   if you don't have it (restart Windows afterwards if it asks, then run it again).
2. It downloads the official Ubuntu 24.04 cloud image (~570 MB, checksum-verified), builds both VMs and
   starts them.
3. It prints two **one-time admin passwords** (user `orion-admin`). Write them down; you'll be asked to change
   them at first login. They're only needed for maintenance.
4. Wait. Both VMs install themselves and reboot once. Progress: `%USERPROFILE%\VirtualBox VMs\OrionOS\orion-lab\console.log`.

Optional: pass a Tailscale auth key so both VMs join your tailnet automatically:
`powershell -ExecutionPolicy Bypass -File os\windows\New-OrionVMs.ps1 -TailscaleAuthKey tskey-auth-…`
(Without it, run `sudo tailscale up --ssh` on each VM later. Tailscale is optional in v0.1: the two VMs talk
over the private OrionNet network.)

## 2. Pair the boardroom (once)

1. **orion-boardroom** window: choose the key passphrase (12+ characters). This is the passphrase you'll type
   for every approval. It then asks for a pairing code.
2. **orion-lab** window: press **Ctrl+Alt+T** (a terminal opens on workspace 2) and run
   `orion-ctl pair-boardroom`. Type the code it shows into the boardroom.
3. The boardroom shows its key fingerprint (like `3A6A-B953-76CB-BDF7-7393`), and the lab shows the fingerprint
   it received. Type `yes` on the lab **only if they're identical**. Until you do, the key can't sign anything.
   A different fingerprint means something swapped the key on the way: answer no and investigate.
4. Back up the key: it's `/home/boardroom/.orion-boardroom/boardroom.key` on the boardroom, encrypted with your
   passphrase. Copy it to an offline USB stick (see V3 Appendix A.6).

## 3. Use it

On the lab, stand-in CFO/CTO/Community agents propose a decision every 10 minutes (bounty payouts, hosting
bills, docs-only merges, FAQ answers). Make one now with `orion-ctl simulate bounty` (or `donation`, `docs`,
`faq`, `hosting`). Each shows up:

- in the HUD's approval queue as a **Boardroom** card (read-only there), and
- on the boardroom console, where you **a**pprove, **e**dit (payouts can be lowered, never raised) or **r**eject,
  then type the passphrase to sign.

**The autonomy learner** counts clean approvals per task type. After 10 in a row it proposes promoting that task
type a level, and the promotion itself needs your signature. The hard limits from V3 are enforced in code:

| Level | Behaviour | Earned after | Allowed for |
|---|---|---|---|
| 0 Ask | Always asks | default | everything |
| 1 Suggest | Pre-fills "Orion recommends approve", still asks | 10 clean approvals | everything except promotions |
| 2 Act + notify | Does it, tells you | 30 | reversible, non-money, non-sensitive only (docs merges, FAQ answers) |
| 3 Silent | Does it, logs it | 100 | same as level 2 |

Money, merges to `main` of code, and infrastructure spend never go past level 1. Any edit or rejection drops a
task type back one level. `orion-ctl status` shows the paper treasury and every task type's level.

**AI model:** the lab reaches Ollama running on Windows automatically (the host's `127.0.0.1:11434` appears as
`10.77.0.2:11434` inside OrionNet). API keys go in `~/.config/orion/daemon.env` on the lab, readable only by you.

## 3b. Automatic first-time setup (the setup agent)

About half a minute after the lab starts, Orion proposes **"Set up your Orion workspace"**: developer tools
(git, GitHub CLI, Node.js, build tools), Firefox, VS Code and Discord, all from their makers' official
packages. Approve it on the boardroom (or your phone) and Orion installs them one by one, with a notification
for each. If one fails, Orion asks again later. Installing software always asks; it can never be promoted to run
by itself.

Then tell Orion where your code is: **Ctrl+Alt+S → Set OpenWoW repo** (or `orion-ctl set-repo <url>`). Orion
proposes **"Get the openwow code"**; approve it and it clones into `~/projects/openwow` and runs `npm install`.
For a private repo, Orion tells you to run `gh auth login` once (you sign in yourself), then `orion-ctl setup`.
Signing in to Discord is also yours to do the first time you open it.

`orion-ctl setup` re-checks and proposes whatever is still missing.

**OpenWoW commands** (terminal: Ctrl+Alt+T). The first time, `wow` shows the commands it found in OpenWoW's
`package.json` and asks you to confirm them.

| Command | Does | Asks your phone? |
|---|---|---|
| `wow start` / `wow stop` / `wow restart` | Run or stop the server | No |
| `wow status` / `wow logs` | Up or down, last error / the last 50 lines | No |
| `wow update` | Newest code + packages | Yes |
| `wow build` | Build a release | Yes |

If OpenWoW stops after you started it, Orion notices within 5 minutes and asks to restart it.

## Orion Mobile (Android)

**Build it (once):** double-click **`Build Orion APK.cmd`** in the Orion folder. It uses the Android Studio on
your PC; the first build takes 3-10 minutes. If your phone is plugged in with USB debugging on, it installs
straight onto the phone; otherwise copy `Orion-Mobile.apk` to the phone and tap it (allow "install unknown apps"
when Android asks). Needs Android 11 or newer with a fingerprint or face unlock set up.

**Connect it (once), at home on Wi-Fi:**
1. On Windows, double-click **`Open Phone Access.cmd`** (one admin prompt). It opens only the lab's approval
   port, only to your home network. Run it again if your PC's address changes.
2. On orion-lab: Ctrl+Alt+S → **Pair my phone**. A QR code appears.

Away from home instead: install **Tailscale** on the phone, run `su -P - orion-admin -c 'sudo tailscale up --ssh'`
on the lab, and pair with `orion-ctl pair-phone --tailscale`.

Then:
3. Point the phone's camera at it and tap the link; Orion opens with everything filled in. Tap **Pair this phone**.
4. The phone shows a fingerprint; the lab shows the one it received. If identical, type `yes` on the lab, then
   tap **I typed yes on the lab** on the phone.

**Use it:** decisions show up as notifications (checked every 15 minutes in the background, every 10 seconds
while the app is open). Tap **Approve** or **Reject** and touch the fingerprint sensor. The phone signs with a
key that only works right after a fingerprint, and only if the decision's text matches its fingerprint.
Lost phone: `orion-ctl phones` then `orion-ctl unpair-phone <id>` on the lab.

## Updating the lab

1. On Windows: double-click **`Update OrionOS Lab.cmd`** (orion-lab must be running).
2. In orion-lab: Ctrl+Alt+T, then `su -P - orion-admin -c 'sudo orion-update'`.
   The very first update (labs installed before 2026-10-15) needs the longer command the Windows window prints.

## Keys on orion-lab

| Keys | Does |
|---|---|
| Super+O or Ctrl+Alt+O | Back to the HUD (workspace 1) |
| Super+Enter or Ctrl+Alt+T | Terminal (opens on workspace 2) |
| Super+D or Ctrl+Alt+Space | App launcher |
| Super+E or Ctrl+Alt+E | Orion Files |
| Super+, or Ctrl+Alt+S | Orion Settings (pair the boardroom, display, sound, restart, power off) |
| Super+2…5 / Ctrl+Alt+2…4 | Workspaces for your apps |
| Super+Shift+Q | Close window |
| Super+Shift+E | Log out |

VirtualBox may keep the Windows key for itself, which is why every key has a Ctrl+Alt twin. Release the
mouse from the VM with the right **Ctrl** key.

## What's in the box

| Path | What |
|---|---|
| `os/windows/New-OrionVMs.ps1` | Builds both VMs (VirtualBox 7, Windows Home is fine) |
| `os/cloud-init/*.yaml` | First-boot instructions for each VM |
| `os/lab/provision.sh` | Lab setup: Sway session, HUD, daemon service, Podman sandboxes, RAM-disk, firewall |
| `os/boardroom/provision.sh` | Boardroom setup: client, console autologin, outbound-locked firewall |
| `os/common/` | Shared hardening, the fixed killswitch |
| `os/dist/Orion_2.0.0_amd64.deb` | The HUD built for Ubuntu 24.04 |
| `apps/boardroom/orion_boardroom.py` | The boardroom client (key, pairing, sign-off console) |
| `apps/daemon/boardroom.py` | Lab side: signed verdicts, paper ledger, autonomy learner |
| `apps/daemon/linux_desktop.py` | Sway/Wayland screen, input, idle and clipboard for the daemon |

## Security model, plainly

- **Inbound to the lab:** only the boardroom's address on port 9121, and SSH over Tailscale. Nothing else.
- **Boardroom:** nothing inbound except SSH over Tailscale; outbound only to the lab's port 9121, DNS, NTP, updates, Tailscale.
- **Every boardroom request is signed** (Ed25519) and time-stamped; replays and stale requests are refused. Each
  verdict also signs the hash of the exact decision you read, so a decision changed after you saw it can't be approved.
- **Pairing** needs a one-time 28-character code that only the lab's session user can create (not over the network,
  and not by the agent user), then your fingerprint check on the lab. Five wrong codes close pairing. A second
  boardroom can't pair while one is paired.
- **The boardroom checks each decision itself:** it recomputes the decision's hash from what it shows you and
  signs that, and the lab recomputes it again before acting, so a decision altered anywhere along the way is refused.
- **The agent user** (`orion-agent`) runs task containers with no network by default and is blocked by the kernel
  firewall from Orion's local ports, so an agent can't drive the HUD's API either.
- **Outbound killswitch** is installed **off**: `sudo orion-killswitch enable <vpn-endpoint-ip>` (WireGuard `wg0`) or
  `sudo orion-killswitch enable-tailscale` (Tailscale exit node). It pins the VPN handshake to one IP and drops
  everything else if the tunnel goes down.
- What OrionOS does **not** give you: anonymity. Orion logs into your own Discord, Telegram and GitHub accounts.

## Rebuilding the HUD .deb

The `.deb` is built on Ubuntu 24.04 (WSL works):

```bash
sudo apt install libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev patchelf build-essential
npm ci && cd apps/desktop
npx tauri build --bundles deb --config '{"bundle":{"createUpdaterArtifacts":false}}'
cp src-tauri/target/release/bundle/deb/*.deb ../../os/dist/
```

Then re-run `New-OrionVMs.ps1 -Recreate` (rebuilds both VMs; the boardroom key is lost, so pair again), or copy
the new `.deb` to the lab and `sudo apt install ./Orion_*.deb`.

## Known limits of v0.1

- **Graphics are software-rendered** in VirtualBox (no 3D for Linux guests), so the black hole animates slower than
  on Windows. Bare metal with a GPU uses the normal renderer automatically.
- **Voice** (Kokoro, Whisper) isn't installed on the lab by default, to keep the 6 GB VM light. Add it with
  `sudo ORION_WITH_VOICE=1 bash /opt/orion-src/os/lab/provision.sh`.
- **Talk-to-Develop (self-evolve)** is off on OrionOS: the daemon's code is installed read-only in `/opt/orion`.
- **The agents are stand-ins.** The V3 CFO/CTO/Community agents don't exist yet; the simulator proposes realistic
  decisions so the sign-off loop and the learner can be used today.
- On Windows Home with WSL2 installed, VirtualBox runs on top of Hyper-V and can be noticeably slower (a green turtle
  icon in the VM's status bar). That's normal.
