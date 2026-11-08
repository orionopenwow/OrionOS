#!/usr/bin/env bash
# Checklist for orion-boardroom (Appendix A.8). Run as the admin user (not sudo): bash verify-boardroom.sh
set -uo pipefail
fails=0
pass() { printf '  \033[1;32mPASS\033[0m %s\n' "$*"; }
fail() { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; fails=$((fails + 1)); }
todo() { printf '  \033[1;33mTODO\033[0m %s\n' "$*"; }
K="$HOME/.orion-boardroom"

echo "orion-boardroom checklist"
sudo ufw status | grep -q "Status: active" && pass "firewall is on" || fail "firewall is off"
grep -qs "PasswordAuthentication no" /etc/ssh/sshd_config.d/10-orion.conf && pass "SSH is key-only" || fail "SSH still allows passwords"
systemctl is-active --quiet orion-egress && pass "egress rules loaded" || fail "egress rules not loaded"
tailscale status >/dev/null 2>&1 && pass "Tailscale connected" || fail "Tailscale not connected"

if curl -sS -m 6 -o /dev/null https://example.com 2>/dev/null; then fail "this user can reach the internet"
else pass "this user cannot reach the internet (only the VPN)"; fi

if [[ -f "$K/boardroom.key" ]]; then
  [[ "$(stat -c %a "$K/boardroom.key")" =~ ^6?00$ ]] && pass "boardroom.key exists and is private" || fail "boardroom.key permissions too open (chmod 600)"
else todo "generate the key: minisign -G -p $K/boardroom.pub -s $K/boardroom.key"; fi
[[ -f "$K/boardroom.pub" ]] && pass "boardroom.pub exists (copy only this to the PC)" || todo "boardroom.pub missing"

if command -v firefox chromium chromium-browser google-chrome 2>/dev/null | grep -q .; then fail "a web browser is installed"
else pass "no web browser installed"; fi
if grep -rIilE --exclude="*.md" --exclude="*.sh" --exclude-dir=.git "seed phrase|mnemonic" "$HOME" 2>/dev/null | head -1 | grep -q .; then fail "a seed phrase / mnemonic file is in your home folder"
else pass "no seed phrase or mnemonic in your home folder"; fi
[[ "${TMOUT:-0}" -gt 0 && "${TMOUT}" -le 300 ]] && pass "shell auto-logout after ${TMOUT}s" || fail "auto-logout not active (log out and back in)"

todo "boardroom.key is backed up to an encrypted, offline USB stick"
todo "snapshots clean-install and boardroom-ready exist and restore"
echo
if [[ $fails -eq 0 ]]; then echo "All automatic checks passed."; else echo "$fails check(s) failed."; fi
exit $(( fails > 0 ))
