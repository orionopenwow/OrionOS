#!/usr/bin/env bash
# Agent sandbox setup (review H12), run as root by provision.sh step 8:
#   ORION_USER=orion AGENT_USER=orion-agent bash os/lab/sandbox-setup.sh
#
#  - /usr/local/bin/orion-sandbox (os/lab/orion-sandbox): one throwaway rootless Podman container per task, run as
#    the separate orion-agent user
#  - /etc/sudoers.d/orion-sandbox: the session user may run exactly that script as orion-agent, without a password,
#    and nothing else (checked with visudo before it's put in place)
#  - /var/lib/orion/agent-work: the shared folder for agent work (owner the session user, group orion-agent, setgid,
#    no access for anyone else). Orion's crash healer makes its worktrees here (ORION_AGENT_WORK_DIR) because
#    orion-agent can't read the session user's home.
#  - orion-agent stays blocked from Orion's ports 9120/9121 by os/common/harden.sh (nftables, provision step 10).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORION_USER="${ORION_USER:-orion}"
AGENT_USER="${AGENT_USER:-orion-agent}"
WORK=/var/lib/orion/agent-work
ENV_FILE="${ORION_DAEMON_ENV:-/etc/orion/daemon.env}"

id "$ORION_USER" >/dev/null 2>&1 || { echo "!! no user $ORION_USER"; exit 1; }
id "$AGENT_USER" >/dev/null 2>&1 || { echo "!! no user $AGENT_USER"; exit 1; }

grep -q "^$AGENT_USER:" /etc/subuid 2>/dev/null || usermod --add-subuids 200000-265535 --add-subgids 200000-265535 "$AGENT_USER"
install -m 755 -o root -g root "$HERE/orion-sandbox" /usr/local/bin/orion-sandbox

install -d -m 755 -o root -g root /var/lib/orion
install -d -m 2750 -o "$ORION_USER" -g "$AGENT_USER" "$WORK"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
cat > "$tmp" <<SUDOERS
# OrionOS agent sandbox (os/lab/sandbox-setup.sh, review H12). $ORION_USER may run the sandbox script as
# $AGENT_USER without a password, and nothing else. The environment is cleared; the script checks its arguments
# and runs Podman with fixed safety options.
Defaults!/usr/local/bin/orion-sandbox env_reset
$ORION_USER ALL=($AGENT_USER) NOPASSWD: /usr/local/bin/orion-sandbox
SUDOERS
if command -v visudo >/dev/null && visudo -cf "$tmp" >/dev/null; then
  install -d -m 750 /etc/sudoers.d
  install -m 440 -o root -g root "$tmp" /etc/sudoers.d/orion-sandbox
else
  echo "!! sudo/visudo missing or the rule didn't check out: the agent sandbox stays off (commands run without it)"
fi

if [ -f "$ENV_FILE" ] && ! grep -q '^ORION_AGENT_WORK_DIR=' "$ENV_FILE"; then
  printf '\n# Shared folder the sandboxed agent user can read (os/lab/sandbox-setup.sh)\nORION_AGENT_WORK_DIR=%s\n' "$WORK" >> "$ENV_FILE"
fi
echo "Agent sandbox: orion-sandbox runs as $AGENT_USER; work folder $WORK"
