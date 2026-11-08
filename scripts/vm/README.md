# Orion V3 lab machines

Scripts for the two Linux VMs described in **Appendix A of `docs/NEXT_PASS_PLAN.md`**. Run them inside fresh
Ubuntu Server 24.04 VMs. They are safe to re-run.

| Script | Run on | How |
|---|---|---|
| `lab-setup.sh` | `orion-lab` | `sudo bash lab-setup.sh` (add a model API with `sudo EXTRA_ALLOW=".anthropic.com" bash lab-setup.sh`) |
| `verify-lab.sh` | `orion-lab` | `sudo bash verify-lab.sh` |
| `boardroom-setup.sh` | `orion-boardroom` | `sudo bash boardroom-setup.sh` |
| `verify-boardroom.sh` | `orion-boardroom` | `bash verify-boardroom.sh` (as yourself, not sudo) |
| `common-harden.sh` | both | used by the setup scripts — don't run it directly |

## Getting the scripts onto a VM
The VMs have clipboard and shared folders turned off on purpose, so pull them from GitHub inside the VM:
```bash
git clone --depth 1 https://github.com/<you>/orion.git && cd orion/scripts/vm
```

## Order
1. Create both VMs (A.2), install Ubuntu, take the `clean-install` snapshot.
2. On your PC: `ssh-keygen -t ed25519`, then paste `~\.ssh\id_ed25519.pub` into `~/.ssh/authorized_keys` on each VM.
   (Without a key the scripts leave password login on so you can't lock yourself out. Re-run once the key is in.)
3. Run the setup script on each VM. It prints a Tailscale login link the first time.
4. Tag the machines in the Tailscale admin console (`tag:lab`, `tag:boardroom`, your PC `tag:owner-pc`), turn off
   key expiry and paste the ACL from Appendix A.4.
5. Run the verify script on each VM until everything passes, then take the `lab-ready` / `boardroom-ready` snapshots.

## How the lab is locked down
- Agents run as `orion-agent`: no sudo, rootless Docker only.
- A per-user firewall (`orion-egress` service, nftables) blocks every connection `orion-agent` makes, and every
  container it runs, except loopback and the Tailscale network.
- The only way out is the local Squid proxy on `127.0.0.1:3128`. It only allows GitHub, npm, PyPI, crates.io,
  Docker Hub and whatever you add with `EXTRA_ALLOW` (the list lives in `/etc/squid/orion-allow.txt`).
- Each task runs with `orion-task <workdir> <image> <command>`: a throwaway container with 2 CPUs and 4 GB, a
  read-only root, no capabilities, and the proxy as its only route out.

## How the boardroom is locked down
- Only root services (Tailscale itself and apt security updates) may use the internet. Your user can only talk
  over the Tailscale network, and the ACL limits that to the PC's daemon.
  (Note: the plan's earlier `ufw deny out` idea would also cut Tailscale's own connection, so this per-user rule
  is used instead.)
- Idle shells log out after 5 minutes. Browsers are removed.
- You generate the signing key yourself with `minisign`. The private key never leaves the VM; only
  `boardroom.pub` goes to the PC.
