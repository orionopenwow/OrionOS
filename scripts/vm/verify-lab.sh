#!/usr/bin/env bash
# Checklist for orion-lab (Appendix A.8). Run: sudo bash verify-lab.sh
# Prints PASS / FAIL / TODO for each item and exits non-zero if anything failed.
set -uo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo bash $0" >&2; exit 1; }
AGENT=orion-agent
BOARDROOM="${BOARDROOM_HOST:-orion-boardroom}"
TEST_REPO="${TEST_REPO:-https://github.com/git/git.git}"
fails=0
pass() { printf '  \033[1;32mPASS\033[0m %s\n' "$*"; }
fail() { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; fails=$((fails + 1)); }
todo() { printf '  \033[1;33mTODO\033[0m %s\n' "$*"; }
agent() { sudo -iu "$AGENT" bash -lc "$*"; }

echo "orion-lab checklist"

id "$AGENT" >/dev/null 2>&1 && pass "$AGENT account exists" || fail "$AGENT account missing"
if sudo -l -U "$AGENT" 2>/dev/null | grep -q "may run"; then fail "$AGENT has sudo rights"; else pass "$AGENT has no sudo"; fi

ufw status | grep -q "Status: active" && pass "firewall is on" || fail "firewall is off"
grep -qs "PasswordAuthentication no" /etc/ssh/sshd_config.d/10-orion.conf && pass "SSH is key-only" \
  || fail "SSH still allows passwords (add your key and re-run lab-setup.sh)"
systemctl is-active --quiet unattended-upgrades && pass "automatic security updates on" || fail "unattended-upgrades not running"
systemctl is-active --quiet orion-egress && nft list table inet orion_egress >/dev/null 2>&1 \
  && pass "agent egress rules loaded" || fail "agent egress rules not loaded (systemctl status orion-egress)"
tailscale status >/dev/null 2>&1 && pass "Tailscale connected" || fail "Tailscale not connected"

# network isolation
if agent "timeout 6 ping -c1 -W3 $BOARDROOM" >/dev/null 2>&1; then fail "lab can reach $BOARDROOM (fix the Tailscale ACL)"
else pass "lab cannot reach $BOARDROOM"; fi
if agent "env -u HTTPS_PROXY -u https_proxy curl -sS -m 6 -o /dev/null https://example.com" >/dev/null 2>&1; then
  fail "agent can reach the internet directly"
else pass "agent cannot reach the internet directly"; fi
if agent "curl -sS -m 8 -o /dev/null https://example.com" >/dev/null 2>&1; then
  fail "allowlist proxy lets example.com through"
else pass "allowlist proxy blocks example.com"; fi
if agent "timeout 20 git ls-remote $TEST_REPO HEAD" >/dev/null 2>&1; then pass "git ls-remote to GitHub works"
else fail "git ls-remote to GitHub fails (check squid: journalctl -u squid)"; fi
if agent 'docker info' >/dev/null 2>&1; then pass "rootless Docker running for $AGENT"
else fail "rootless Docker not running (sudo -iu $AGENT systemctl --user status docker)"; fi

# secrets
S="/home/$AGENT/.config/orion/secrets.env"
if [[ -f "$S" && "$(stat -c %a "$S")" == 600 ]]; then pass "secrets file is private (600)"; else fail "secrets file missing or not chmod 600"; fi
if grep -rIilE --exclude="*.md" --exclude="*.sh" --exclude-dir=node_modules --exclude-dir=.git "seed phrase|mnemonic" /home 2>/dev/null | grep -v '/\.cache/' | head -1 | grep -q .; then
  fail "something that looks like a wallet seed/private key is under /home — remove it"
else pass "no wallet seed or private key under /home"; fi

todo "GitHub token can open a PR but NOT merge — test on a scratch branch"
todo "an approval forged without the boardroom key is rejected by the daemon (needs V3 step 2)"
todo "snapshots clean-install and lab-ready exist and restore"

echo
if [[ $fails -eq 0 ]]; then echo "All automatic checks passed."; else echo "$fails check(s) failed."; fi
exit $(( fails > 0 ))
