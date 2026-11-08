#!/usr/bin/env bash
# orion-lab: the agent sandbox VM (Appendix A.5 of docs/NEXT_PASS_PLAN.md).
#
#   sudo bash lab-setup.sh
#   sudo EXTRA_ALLOW=".anthropic.com" bash lab-setup.sh     # also allow a model API domain
#
# Result:
#   - user `orion-agent` (no sudo) running rootless Docker
#   - every network connection made by orion-agent (including its containers) is blocked, except:
#       * the local allowlist proxy (Squid, 127.0.0.1:3128) which only lets through GitHub, npm, PyPI,
#         crates.io, Docker Hub and anything in EXTRA_ALLOW
#       * the Tailscale network (100.64.0.0/10) — Tailscale ACLs then decide what it may reach (the PC's :9120 only)
#   - Python 3.12, Node 20, build tools
#   - secrets file at /home/orion-agent/.config/orion/secrets.env (chmod 600, empty)
#   - tools: `orion-task` to run a throwaway, locked-down container for one task
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common-harden.sh
source "$HERE/common-harden.sh"
require_root

AGENT=orion-agent
EXTRA_ALLOW="${EXTRA_ALLOW:-}"

harden_base orion-lab
export DEBIAN_FRONTEND=noninteractive

# ---------------------------------------------------------------- agent account
if ! id "$AGENT" >/dev/null 2>&1; then
  log "Creating $AGENT (no password, no sudo)…"
  adduser --disabled-password --gecos "" "$AGENT"
fi
gpasswd -d "$AGENT" sudo >/dev/null 2>&1 || true
AGENT_UID="$(id -u "$AGENT")"
AGENT_HOME="$(getent passwd "$AGENT" | cut -d: -f6)"

# ---------------------------------------------------------------- toolchains
log "Installing Python, Node 20 and build tools…"
apt-get install -y -q python3.12-venv python3-pip build-essential cmake uidmap dbus-user-session slirp4netns squid
if ! command -v node >/dev/null || [[ "$(node -v | cut -c2- | cut -d. -f1)" -lt 20 ]]; then
  # NodeSource's signed apt repository (apt checks the signing key; no "curl | bash")
  install -d -m 755 /etc/apt/keyrings
  curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
  echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list
  apt-get update -q
  apt-get install -y -q nodejs
fi

# ---------------------------------------------------------------- rootless docker (Docker's own repo)
if ! command -v dockerd-rootless-setuptool.sh >/dev/null; then
  log "Installing Docker (rootless)…"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  # shellcheck disable=SC1091
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    >/etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-ce-rootless-extras
fi
# the root daemon is not used — agents only get their own rootless one
systemctl disable --now docker.service docker.socket >/dev/null 2>&1 || true
loginctl enable-linger "$AGENT"
sleep 1
RUNTIME="/run/user/$AGENT_UID"
as_agent() { sudo -u "$AGENT" XDG_RUNTIME_DIR="$RUNTIME" DBUS_SESSION_BUS_ADDRESS="unix:path=$RUNTIME/bus" "$@"; }
if [[ ! -f "$AGENT_HOME/.config/systemd/user/docker.service" ]]; then
  as_agent dockerd-rootless-setuptool.sh install
fi
# containers reach the host's allowlist proxy at 10.0.2.2:3128 (slirp4netns host loopback)
install -d -o "$AGENT" -g "$AGENT" "$AGENT_HOME/.config/systemd/user/docker.service.d"
cat >"$AGENT_HOME/.config/systemd/user/docker.service.d/orion.conf" <<'EOF'
[Service]
Environment=DOCKERD_ROOTLESS_ROOTLESSKIT_DISABLE_HOST_LOOPBACK=false
Environment=HTTP_PROXY=http://127.0.0.1:3128
Environment=HTTPS_PROXY=http://127.0.0.1:3128
Environment=NO_PROXY=localhost,127.0.0.1
EOF
chown -R "$AGENT:$AGENT" "$AGENT_HOME/.config"
as_agent systemctl --user daemon-reload
as_agent systemctl --user enable --now docker >/dev/null
as_agent systemctl --user restart docker

# ---------------------------------------------------------------- egress allowlist proxy
log "Setting up the egress allowlist (Squid)…"
cat >/etc/squid/orion-allow.txt <<EOF
.github.com
.githubusercontent.com
.npmjs.org
.npmjs.com
.yarnpkg.com
pypi.org
.pythonhosted.org
crates.io
.crates.io
.docker.io
.docker.com
production.cloudflare.docker.com
EOF
for d in ${EXTRA_ALLOW//,/ }; do echo "$d" >>/etc/squid/orion-allow.txt; done
cat >/etc/squid/conf.d/orion.conf <<'EOF'
# Orion lab: only allowlisted domains, only from this machine
http_port 127.0.0.1:3128
acl orion_allow dstdomain "/etc/squid/orion-allow.txt"
acl SSL_ports port 443
acl CONNECT method CONNECT
http_access deny CONNECT !SSL_ports
http_access allow localhost orion_allow
http_access deny all
EOF
# Ubuntu's default squid.conf listens on 3128 on all interfaces; keep only our loopback listener
sed -i 's/^http_port 3128$/# http_port 3128  (disabled by orion)/' /etc/squid/squid.conf
systemctl enable squid >/dev/null
systemctl restart squid

# ---------------------------------------------------------------- per-user firewall for the agent
log "Blocking direct internet for $AGENT (and its containers)…"
cat >/etc/nftables-orion.conf <<EOF
#!/usr/sbin/nft -f
# Orion lab egress: orion-agent may only talk to loopback (the allowlist proxy) and the Tailscale network.
table inet orion_egress
delete table inet orion_egress
table inet orion_egress {
  chain output {
    type filter hook output priority 0; policy accept;
    meta skuid $AGENT_UID oif lo accept
    meta skuid $AGENT_UID ip daddr 100.64.0.0/10 accept
    meta skuid $AGENT_UID ip6 daddr fd7a:115c:a1e0::/48 accept
    meta skuid $AGENT_UID counter reject
  }
}
EOF
cat >/etc/systemd/system/orion-egress.service <<'EOF'
[Unit]
Description=Orion lab egress rules (agent may only use the allowlist proxy and Tailscale)
After=network-pre.target
Before=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/nftables-orion.conf
ExecStop=/usr/sbin/nft delete table inet orion_egress

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now orion-egress.service >/dev/null
systemctl restart orion-egress.service

# ---------------------------------------------------------------- agent environment
log "Configuring $AGENT's proxy and secrets file…"
install -d -m 700 -o "$AGENT" -g "$AGENT" "$AGENT_HOME/.config/orion"
SECRETS="$AGENT_HOME/.config/orion/secrets.env"
if [[ ! -f "$SECRETS" ]]; then
  cat >"$SECRETS" <<'EOF'
# Fine-grained GitHub token: Contents read, Pull requests write, no admin. Agents open PRs, never merge.
GITHUB_TOKEN=
EOF
fi
chown "$AGENT:$AGENT" "$SECRETS"; chmod 600 "$SECRETS"
cat >"$AGENT_HOME/.config/orion/proxy.env" <<'EOF'
export HTTP_PROXY=http://127.0.0.1:3128 HTTPS_PROXY=http://127.0.0.1:3128
export http_proxy=$HTTP_PROXY https_proxy=$HTTPS_PROXY NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1
export DOCKER_HOST=unix:///run/user/$(id -u)/docker.sock
EOF
chown "$AGENT:$AGENT" "$AGENT_HOME/.config/orion/proxy.env"
grep -q 'orion/proxy.env' "$AGENT_HOME/.profile" 2>/dev/null \
  || echo '. "$HOME/.config/orion/proxy.env"' >>"$AGENT_HOME/.profile"
grep -q 'orion/proxy.env' "$AGENT_HOME/.bashrc" 2>/dev/null \
  || echo '. "$HOME/.config/orion/proxy.env"' >>"$AGENT_HOME/.bashrc"
sudo -u "$AGENT" git config --global http.proxy http://127.0.0.1:3128

# ---------------------------------------------------------------- orion-task: one throwaway container per task
cat >/usr/local/bin/orion-task <<'EOF'
#!/usr/bin/env bash
# Run one agent task in a throwaway, locked-down container (run as orion-agent).
#   orion-task <workdir> <image> <command...>
#   orion-task ~/tasks/1234 node:20 npm test
set -euo pipefail
[[ $# -ge 3 ]] || { echo "usage: orion-task <workdir> <image> <command...>" >&2; exit 2; }
work="$(realpath "$1")"; image="$2"; shift 2
[[ "$(id -un)" == orion-agent ]] || { echo "run as orion-agent" >&2; exit 1; }
export DOCKER_HOST="unix:///run/user/$(id -u)/docker.sock"
exec docker run --rm --cpus=2 --memory=4g --pids-limit=512 \
  --read-only --tmpfs /tmp:rw,size=1g --cap-drop=ALL --security-opt no-new-privileges \
  -e HTTP_PROXY=http://10.0.2.2:3128 -e HTTPS_PROXY=http://10.0.2.2:3128 \
  -e http_proxy=http://10.0.2.2:3128 -e https_proxy=http://10.0.2.2:3128 \
  -v "$work:/work" -w /work "$image" "$@"
EOF
chmod 755 /usr/local/bin/orion-task

log "orion-lab is set up."
cat <<EOF

Next steps
  1. In the Tailscale admin console: tag this machine  tag:lab , disable key expiry, apply the ACL from Appendix A.4.
  2. Put the GitHub fine-grained token in $SECRETS   (sudo -u $AGENT nano $SECRETS)
  3. Run the checklist:   sudo bash $HERE/verify-lab.sh
  4. In your hypervisor, take a snapshot named  lab-ready
EOF
